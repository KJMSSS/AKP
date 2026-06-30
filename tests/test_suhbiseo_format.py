"""출력 양식 프로필(타이퍼/수학비서) 회귀+신규 검증 (무네트워크).

타이퍼  = A3(84188) 2단 + 문제별 1×6 메타표.
수학비서 = B4(72852) 2단 + 메타표 없음 (서울세종고 명조 header).
둘 다 git추적 template.hwpx / suhbiseo_template.hwpx 로 빌드 → 워크트리에서 실행 가능.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest

from src.text_only.text_builder import build_from_markdown
from src.text_only.typer_builder import (
    build_typer_hwpx, build_suhbiseo_hwpx, build_by_format,
    _REF_TYPER, _REF_SUHBISEO,
)

pytestmark = pytest.mark.skipif(
    not _REF_TYPER.exists() or not _REF_SUHBISEO.exists(),
    reason="template.hwpx / suhbiseo_template.hwpx 없음",
)

_MD = "\n".join([
    "1. 두 다항식의 합을 구하면? [4점]",
    "① 1", "② 2", "③ 3", "④ 4", "⑤ 5",
    "",
    "2. 다음을 계산하시오. [3점]",
    "① 1", "② 2", "③ 3", "④ 4", "⑤ 5",
])


def _section(path: Path) -> str:
    return zipfile.ZipFile(path).read("Contents/section0.xml").decode("utf-8")


def _page_w(xml: str) -> int:
    m = re.search(r'<hp:pagePr[^>]*width="(\d+)"', xml)
    return int(m.group(1)) if m else 0


def _one_dan(tmp_path: Path, base: Path) -> Path:
    one = tmp_path / "one.hwpx"
    build_from_markdown(_MD, one, base)
    xml = _section(one)
    assert 'colCount="1"' in xml, "1단 빌드는 colCount=1 이어야"
    return one


def test_typer_unchanged(tmp_path):
    one = _one_dan(tmp_path, _REF_TYPER)
    two = tmp_path / "typer.hwpx"
    build_typer_hwpx(one, "2026_1_1_a_공수1_테스트고", two)
    xml = _section(two)
    assert 'colCount="2"' in xml                 # 2단
    assert _page_w(xml) == 84188                 # A3
    assert 'rowCnt="1" colCnt="6"' in xml        # 메타표 있음(타이퍼)


def test_suhbiseo_b4_no_meta(tmp_path):
    one = _one_dan(tmp_path, _REF_SUHBISEO)
    two = tmp_path / "suhbiseo.hwpx"
    build_suhbiseo_hwpx(one, "2026_1_1_a_공수1_테스트고", two)
    xml = _section(two)
    assert 'colCount="2"' in xml                 # 2단
    assert _page_w(xml) == 72852                 # B4
    assert 'rowCnt="1" colCnt="6"' not in xml    # 메타표 없음(수학비서)
    # 수학비서는 스타일 재매핑 안 함 → 본문 단락이 1단 스타일(styleIDRef=0) 유지
    assert 'styleIDRef="0"' in xml


def test_build_by_format_dispatch(tmp_path):
    one = _one_dan(tmp_path, _REF_SUHBISEO)
    out = build_by_format(one, "x_테스트고", tmp_path / "f.hwpx", "수학비서")
    assert _page_w(_section(out)) == 72852
    with pytest.raises(ValueError):
        build_by_format(one, "x", tmp_path / "g.hwpx", "없는양식")
