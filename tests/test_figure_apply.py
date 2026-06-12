"""그림 검수 결정 HWPX 반영(apply_figure_decisions) 회귀 테스트.

핵심 시나리오: manual/auto 삽입, skipped 마커 제거,
이미지 파일·플레이스홀더 부재 시 안전 동작.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest

from src.common.hwpx_image_inserter import (
    apply_figure_decisions,
    strip_figure_placeholders,
)


def _para(text: str, pid: int = 10) -> str:
    return (
        f'<hp:p id="{pid}" paraPrIDRef="8" charPrIDRef="0">'
        f'<hp:run charPrIDRef="0"><hp:t>{text}</hp:t></hp:run></hp:p>'
    )


def _make_hwpx(tmp_path: Path, paras: list[str]) -> Path:
    section = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
        'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
        'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core">'
        + "".join(paras)
        + "</hs:sec>"
    )
    hpf = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<opf:package xmlns:opf="http://www.idpf.org/2007/opf/">'
        '<opf:manifest>'
        '<opf:item id="section0" href="Contents/section0.xml" media-type="application/xml"/>'
        '</opf:manifest></opf:package>'
    )
    hwpx = tmp_path / "t.hwpx"
    with zipfile.ZipFile(hwpx, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Contents/section0.xml", section)
        zf.writestr("Contents/content.hpf", hpf)
    return hwpx


def _make_png(tmp_path: Path, name: str, size=(40, 30)) -> Path:
    from PIL import Image

    p = tmp_path / name
    Image.new("RGB", size, (200, 100, 50)).save(str(p))
    return p


def _read_xml(hwpx: Path) -> str:
    with zipfile.ZipFile(hwpx) as zf:
        return zf.read("Contents/section0.xml").decode("utf-8")


def _visible_text(xml: str) -> str:
    return "".join(re.findall(r"<hp:t[^>]*>([^<]*)</hp:t>", xml))


def _item(status: str, **kw) -> dict:
    base = {"status": status, "manual_path": None, "auto_path": None}
    base.update(kw)
    return base


class TestApplyDecisions:
    def test_manual_inserted(self, tmp_path):
        hwpx = _make_hwpx(tmp_path, [
            _para("3. 문제 본문", 10),
            _para("【★ 그림:3번】", 11),
        ])
        png = _make_png(tmp_path, "3_manual.png")
        counts = apply_figure_decisions(hwpx, {
            "3": _item("manual_selected", manual_path=str(png)),
        })
        xml = _read_xml(hwpx)
        assert counts["manual"] == 1
        assert "【★ 그림:3번】" not in xml
        assert "<hp:pic" in xml
        with zipfile.ZipFile(hwpx) as zf:
            assert any(n.startswith("BinData/") for n in zf.namelist())

    def test_auto_and_pending_use_auto_path(self, tmp_path):
        hwpx = _make_hwpx(tmp_path, [
            _para("【★ 그림:1번】", 10),
            _para("【★ 그림:2번】", 11),
        ])
        png1 = _make_png(tmp_path, "1_auto.png")
        png2 = _make_png(tmp_path, "2_auto.png")
        counts = apply_figure_decisions(hwpx, {
            "1": _item("auto_selected", auto_path=str(png1)),
            "2": _item("pending", auto_path=str(png2)),
        })
        xml = _read_xml(hwpx)
        assert counts["auto"] == 2
        assert "【★ 그림" not in xml
        assert xml.count("<hp:pic") == 2

    def test_skipped_marker_removed(self, tmp_path):
        hwpx = _make_hwpx(tmp_path, [
            _para("5. 그림 없는 문제", 10),
            _para("【★ 그림:5번】", 11),
        ])
        counts = apply_figure_decisions(hwpx, {"5": _item("skipped")})
        xml = _read_xml(hwpx)
        assert counts["skipped"] == 1
        assert "【★ 그림:5번】" not in xml
        assert "<hp:pic" not in xml
        assert "5. 그림 없는 문제" in _visible_text(xml)

    def test_missing_image_keeps_placeholder(self, tmp_path):
        hwpx = _make_hwpx(tmp_path, [_para("【★ 그림:7번】", 10)])
        counts = apply_figure_decisions(hwpx, {
            "7": _item("auto_selected", auto_path=str(tmp_path / "없는파일.png")),
        })
        xml = _read_xml(hwpx)
        assert counts["missing"] == 1
        assert "【★ 그림:7번】" in xml  # 유실 대신 플레이스홀더 보존

    def test_no_placeholder_counted(self, tmp_path):
        hwpx = _make_hwpx(tmp_path, [_para("마커 없는 문서", 10)])
        png = _make_png(tmp_path, "9_auto.png")
        counts = apply_figure_decisions(hwpx, {
            "9": _item("auto_selected", auto_path=str(png)),
        })
        assert counts["no_placeholder"] == 1
        assert "<hp:pic" not in _read_xml(hwpx)

    def test_mixed_decisions(self, tmp_path):
        hwpx = _make_hwpx(tmp_path, [
            _para("【★ 그림:1번】", 10),
            _para("【★ 그림:2번】", 11),
            _para("【★ 그림:3번】", 12),
        ])
        png1 = _make_png(tmp_path, "1_m.png")
        png2 = _make_png(tmp_path, "2_a.png")
        counts = apply_figure_decisions(hwpx, {
            "1": _item("manual_selected", manual_path=str(png1)),
            "2": _item("auto_selected", auto_path=str(png2)),
            "3": _item("skipped"),
        })
        xml = _read_xml(hwpx)
        assert (counts["manual"], counts["auto"], counts["skipped"]) == (1, 1, 1)
        assert "【★ 그림" not in xml
        assert xml.count("<hp:pic") == 2


class TestDpiScaling:
    """300 DPI 크롭이 150 DPI 가정으로 2배 크게 들어가던 버그 회귀."""

    def _pic_size(self, xml: str) -> tuple[int, int]:
        m = re.search(r'<hp:orgSz width="(\d+)" height="(\d+)"', xml)
        return (int(m.group(1)), int(m.group(2)))

    def test_auto_with_explicit_dpi(self, tmp_path):
        hwpx = _make_hwpx(tmp_path, [_para("【★ 그림:1번】", 10)])
        png = _make_png(tmp_path, "a.png", size=(300, 150))
        apply_figure_decisions(hwpx, {
            "1": _item("auto_selected", auto_path=str(png), auto_dpi=300),
        })
        w, h = self._pic_size(_read_xml(hwpx))
        # 300px @300dpi = 72pt = 7200 HWP
        assert (w, h) == (7200, 3600)

    def test_legacy_conf_strategy_assumes_300(self, tmp_path):
        # DPI 메타 없는 구버전 큐 — strategy=agreement는 300 DPI 크롭
        hwpx = _make_hwpx(tmp_path, [_para("【★ 그림:2번】", 10)])
        png = _make_png(tmp_path, "b.png", size=(300, 150))
        apply_figure_decisions(hwpx, {
            "2": _item("auto_selected", auto_path=str(png), strategy="agreement"),
        })
        assert self._pic_size(_read_xml(hwpx)) == (7200, 3600)

    def test_legacy_pymupdf_strategy_assumes_150(self, tmp_path):
        hwpx = _make_hwpx(tmp_path, [_para("【★ 그림:3번】", 10)])
        png = _make_png(tmp_path, "c.png", size=(300, 150))
        apply_figure_decisions(hwpx, {
            "3": _item("auto_selected", auto_path=str(png), strategy="pymupdf"),
        })
        # 300px @150dpi = 144pt = 14400 HWP
        assert self._pic_size(_read_xml(hwpx)) == (14400, 7200)

    def test_manual_inherits_crop_dpi(self, tmp_path):
        hwpx = _make_hwpx(tmp_path, [_para("【★ 그림:4번】", 10)])
        png = _make_png(tmp_path, "d.png", size=(300, 150))
        apply_figure_decisions(hwpx, {
            "4": _item("manual_selected", manual_path=str(png), crop_dpi=300),
        })
        assert self._pic_size(_read_xml(hwpx)) == (7200, 3600)

    def test_manual_legacy_prob_crop_path_assumes_300(self, tmp_path):
        # 메타 없는 구버전 — crop_path 파일명에 prob_crop 포함이면 300 DPI
        hwpx = _make_hwpx(tmp_path, [_para("【★ 그림:5번】", 10)])
        png = _make_png(tmp_path, "e.png", size=(300, 150))
        apply_figure_decisions(hwpx, {
            "5": _item("manual_selected", manual_path=str(png),
                       crop_path="C:/tmp/figs/prob_crop_5.png"),
        })
        assert self._pic_size(_read_xml(hwpx)) == (7200, 3600)


class TestStripPlaceholders:
    def test_strip_keeps_merged_text(self, tmp_path):
        # 마커와 본문이 한 단락에 합쳐진 경우 — 본문은 보존
        hwpx = _make_hwpx(tmp_path, [
            _para("문제 텍스트 【★ 그림:4번】 이어지는 텍스트", 10),
        ])
        removed = strip_figure_placeholders(hwpx, ["4"])
        xml = _read_xml(hwpx)
        assert removed == ["4"]
        assert "【★ 그림:4번】" not in xml
        assert "문제 텍스트" in _visible_text(xml)
        assert "이어지는 텍스트" in _visible_text(xml)

    def test_strip_empty_list_noop(self, tmp_path):
        hwpx = _make_hwpx(tmp_path, [_para("【★ 그림:1번】", 10)])
        assert strip_figure_placeholders(hwpx, []) == []
        assert "【★ 그림:1번】" in _read_xml(hwpx)

    def test_strip_absent_marker(self, tmp_path):
        hwpx = _make_hwpx(tmp_path, [_para("마커 없음", 10)])
        assert strip_figure_placeholders(hwpx, ["8"]) == []
