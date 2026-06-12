"""hwpx_table_inserter 마커 페어링 회귀 테스트.

핵심 시나리오: 끝마커 번호 정확 매칭 — 마커 소실 시 이웃 문제를
조건 박스에 흡수하지 않고, 마커 리터럴을 결과물에 남기지 않는다.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest

from src.common.hwpx_table_inserter import replace_condition_tables


def _para(text: str, pid: int = 10) -> str:
    return (
        f'<hp:p id="{pid}" paraPrIDRef="8" charPrIDRef="0">'
        f'<hp:run charPrIDRef="0"><hp:t>{text}</hp:t></hp:run></hp:p>'
    )


def _make_hwpx(tmp_path: Path, paras: list[str]) -> Path:
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
        'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
        + "".join(paras)
        + "</hs:sec>"
    )
    hwpx = tmp_path / "t.hwpx"
    with zipfile.ZipFile(hwpx, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Contents/section0.xml", xml)
    return hwpx


def _read_xml(hwpx: Path) -> str:
    with zipfile.ZipFile(hwpx) as zf:
        return zf.read("Contents/section0.xml").decode("utf-8")


def _visible_text(xml: str) -> str:
    return "".join(re.findall(r"<hp:t[^>]*>([^<]*)</hp:t>", xml))


class TestMarkerPairing:
    def test_normal_pair_replaced(self, tmp_path):
        hwpx = _make_hwpx(tmp_path, [
            _para("【★ 조건시작:3번】", 10),
            _para("(가) 조건 내용", 11),
            _para("【★ 조건끝:3번】", 12),
        ])
        n = replace_condition_tables(hwpx)
        xml = _read_xml(hwpx)
        assert n == 1
        assert "【★" not in _visible_text(xml)
        assert "(가) 조건 내용" in xml
        assert "<hp:tbl" in xml

    def test_lost_end_marker_does_not_swallow_neighbor(self, tmp_path):
        """끝:3 소실 + 끝:5 존재 — 4번 문제를 3번 박스에 흡수하면 안 된다."""
        hwpx = _make_hwpx(tmp_path, [
            _para("【★ 조건시작:3번】", 10),
            _para("(가) 3번 조건", 11),
            # 【★ 조건끝:3번】 소실
            _para("4. 다음 문제 본문", 12),
            _para("【★ 조건시작:5번】", 13),
            _para("(가) 5번 조건", 14),
            _para("【★ 조건끝:5번】", 15),
        ])
        n = replace_condition_tables(hwpx)
        xml = _read_xml(hwpx)
        text = _visible_text(xml)

        assert n == 1                       # 5번 쌍만 정상 교체
        assert "【★" not in text            # 마커 리터럴 잔존 금지
        assert "(가) 3번 조건" in text      # 3번 내용 보존 (박스 밖 평문)
        assert "4. 다음 문제 본문" in text  # 4번 문제 흡수 금지
        # 4번 본문이 표 안으로 들어가지 않았는지: 표는 1개(5번)뿐
        assert xml.count("<hp:tbl") == 1

    def test_no_end_marker_at_all(self, tmp_path):
        """끝마커가 아예 없으면 — 마커 제거 + 내용 보존, 리터럴 노출 금지."""
        hwpx = _make_hwpx(tmp_path, [
            _para("【★ 조건시작:7번】", 10),
            _para("(가) 외로운 조건", 11),
            _para("8. 다음 문제", 12),
        ])
        n = replace_condition_tables(hwpx)
        xml = _read_xml(hwpx)
        text = _visible_text(xml)
        assert n == 0
        assert "【★" not in text
        assert "(가) 외로운 조건" in text
        assert "8. 다음 문제" in text

    def test_stray_end_marker_cleaned(self, tmp_path):
        """시작마커 소실로 끝마커만 남은 경우 — 리터럴 제거."""
        hwpx = _make_hwpx(tmp_path, [
            _para("(가) 조건이었던 것", 10),
            _para("【★ 조건끝:2번】", 11),
        ])
        n = replace_condition_tables(hwpx)
        xml = _read_xml(hwpx)
        assert n == 0
        assert "【★" not in _visible_text(xml)
        assert "(가) 조건이었던 것" in xml

    def test_content_merged_into_marker_para_preserved(self, tmp_path):
        """마커 단락에 내용이 합쳐진 경우 — 내용을 삭제하지 않는다."""
        hwpx = _make_hwpx(tmp_path, [
            _para("【★ 조건시작:3번】 (가) 합쳐진 조건 【★ 조건끝:3번】", 10),
        ])
        n = replace_condition_tables(hwpx)
        xml = _read_xml(hwpx)
        text = _visible_text(xml)
        assert "【★" not in text
        assert "(가) 합쳐진 조건" in text

    def test_empty_marker_pair_removed(self, tmp_path):
        """내용 없는 빈 마커 쌍 — 두 단락 모두 제거."""
        hwpx = _make_hwpx(tmp_path, [
            _para("앞 문장", 9),
            _para("【★ 조건시작:1번】", 10),
            _para("【★ 조건끝:1번】", 11),
            _para("뒤 문장", 12),
        ])
        n = replace_condition_tables(hwpx)
        xml = _read_xml(hwpx)
        text = _visible_text(xml)
        assert n == 0
        assert "【★" not in text
        assert "앞 문장" in text and "뒤 문장" in text

    def test_multiple_pairs(self, tmp_path):
        hwpx = _make_hwpx(tmp_path, [
            _para("【★ 조건시작:1번】", 10),
            _para("(가) 일번", 11),
            _para("【★ 조건끝:1번】", 12),
            _para("【★ 조건시작:2번】", 13),
            _para("(나) 이번", 14),
            _para("【★ 조건끝:2번】", 15),
        ])
        n = replace_condition_tables(hwpx)
        xml = _read_xml(hwpx)
        assert n == 2
        assert xml.count("<hp:tbl") == 2
        assert "【★" not in _visible_text(xml)
