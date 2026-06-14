"""타이퍼 2단 변환의 그림 보존·멱등성 회귀 테스트.

핵심 사고: build_section이 텍스트·수식·표 없는 단락을 버리면서 그림(hp:pic)
단락까지 삭제 → 2단에서 그림 전멸. 본 변환이 2단을 내보내므로 반드시 보존돼야 한다.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest

from src.text_only.text_builder import build_from_markdown
from src.common.hwpx_image_inserter import insert_figure_placeholder
from src.text_only.typer_builder import build_typer_hwpx

_SAMPLES = Path(__file__).resolve().parent.parent / "samples"
_TEMPLATE = next(
    (f for f in _SAMPLES.glob("*.hwpx") if "워드초벌" in f.name and "]1." not in f.name),
    None,
)
_REF = _SAMPLES / "template.hwpx"

pytestmark = pytest.mark.skipif(
    _TEMPLATE is None or not _REF.exists(),
    reason="samples 템플릿(워드초벌/template.hwpx) 없음",
)

_MD = "\n".join([
    "1. 다음 그림을 보고 물음에 답하시오.",
    "【★ 그림:1번】 [4점]",
    "① 1", "② 2", "③ 3", "④ 4", "⑤ 5",
    "",
    "2. 계산하시오. [3점]",
    "① 1", "② 2", "③ 3", "④ 4", "⑤ 5",
])


def _png(tmp_path: Path, size=(120, 90)) -> Path:
    from PIL import Image
    p = tmp_path / "fig1.png"
    Image.new("RGB", size, (180, 120, 60)).save(str(p))
    return p


def _build_1dan_with_figure(tmp_path: Path) -> Path:
    one = tmp_path / "one.hwpx"
    build_from_markdown(_MD, one, _TEMPLATE)
    insert_figure_placeholder(one, "1", _png(tmp_path), dpi=150)
    return one


def _cols_pics(path: Path) -> tuple[set, int]:
    xml = zipfile.ZipFile(path).read("Contents/section0.xml").decode("utf-8")
    return set(re.findall(r'colCount="(\d+)"', xml)), xml.count("<hp:pic")


def test_figure_survives_2dan(tmp_path):
    one = _build_1dan_with_figure(tmp_path)
    cols1, pic1 = _cols_pics(one)
    assert cols1 == {"1"} and pic1 == 1   # 1단 + 그림 1

    two = tmp_path / "two.hwpx"
    build_typer_hwpx(one, "2026_2_1_a_공수1_테스트고", two)
    cols2, pic2 = _cols_pics(two)
    assert cols2 == {"2"}, "2단이어야 함"
    assert pic2 == 1, "그림이 2단 변환에서 보존돼야 함 (회귀 방지)"


def test_typer_idempotent_on_2dan(tmp_path):
    one = _build_1dan_with_figure(tmp_path)
    two = tmp_path / "two.hwpx"
    build_typer_hwpx(one, "2026_2_1_a_공수1_테스트고", two)
    # 이미 2단인 걸 또 변환 → 그대로 (이중변환 방지)
    three = tmp_path / "three.hwpx"
    build_typer_hwpx(two, "2026_2_1_a_공수1_테스트고", three)
    cols3, pic3 = _cols_pics(three)
    assert cols3 == {"2"} and pic3 == 1


def test_wide_figure_scaled_into_column(tmp_path):
    # 열폭(_COL_W=32456 HWPUNIT)보다 넓은 그림은 축소돼야 함
    from src.text_only.typer_builder import _COL_W
    one = tmp_path / "one.hwpx"
    build_from_markdown(_MD, one, _TEMPLATE)
    # 매우 넓은 그림 (600px @150dpi ≈ 288pt = 28800 HWP < col, so push bigger)
    from PIL import Image
    wide = tmp_path / "wide.png"
    Image.new("RGB", (1400, 300), (100, 100, 200)).save(str(wide))
    insert_figure_placeholder(one, "1", wide, dpi=150, max_width_hpc=90000)

    two = tmp_path / "two.hwpx"
    build_typer_hwpx(one, "2026_2_1_a_공수1_테스트고", two)
    xml = zipfile.ZipFile(two).read("Contents/section0.xml").decode("utf-8")
    m = re.search(r'<hp:pic[\s\S]{0,260}?<hp:orgSz width="(\d+)"', xml)
    assert m, "그림 보존"
    w = int(m.group(1))
    assert w <= _COL_W, f"넓은 그림이 열폭 안으로 축소돼야 함 (w={w} > {_COL_W})"
