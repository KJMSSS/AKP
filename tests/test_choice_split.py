"""한 줄 다중 선택지 분리 회귀 테스트.

골드셋 비교에서 발견: '① 4 ② 8 ③ 12 ④ 16 ⑤ 18' 이 통째 1개 수식이 되고
②③④⑤가 수식 안에 갇혔다. 각 값이 개별 수식 + 마커는 텍스트여야 한다.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest

from src.text_only.text_builder import _choice_line_segs, build_from_markdown

_TEMPLATE = next(
    (f for f in (Path(__file__).resolve().parent.parent / "samples").glob("*.hwpx")
     if "워드초벌" in f.name and "]1." not in f.name), None)


def test_split_segments():
    segs, neq = _choice_line_segs("① 4 ② 8 ③ 12 ④ 16 ⑤ 18")
    assert neq == 5
    # 마커는 text, 값은 inline(수식)
    inlines = [v for k, v in segs if k == "inline"]
    assert inlines == ["4", "8", "12", "16", "18"]
    texts = "".join(v for k, v in segs if k == "text")
    assert "①" in texts and "②" in texts and "⑤" in texts


def test_split_keeps_existing_math():
    segs, neq = _choice_line_segs("① $-2$ ② $-1$ ③ 0")
    inlines = [v for k, v in segs if k == "inline"]
    assert inlines == ["-2", "-1", "0"]   # 이미 $인 건 그대로, 0은 새로 수식화
    assert neq == 1                        # 0만 새 수식화


def test_single_choice_unaffected():
    # 한 줄에 선택지 하나뿐이면 분리 로직 안 탐 (findall < 2)
    segs, _ = _choice_line_segs("① 일직선 위의 점")
    # 마커 + 내용 (내용은 한글이라 그대로 inline 수식화돼도 무방하나, 분리 자체는 정상)
    assert any(k == "text" and "①" in v for k, v in segs)


@pytest.mark.skipif(_TEMPLATE is None, reason="워드초벌 템플릿 없음")
def test_build_produces_separate_equations(tmp_path):
    md = "1. 다음 값은? [4점]\n① 4 ② 8 ③ 12 ④ 16 ⑤ 18"
    out = tmp_path / "c.hwpx"
    build_from_markdown(md, out, _TEMPLATE)
    xml = zipfile.ZipFile(out).read("Contents/section0.xml").decode("utf-8")
    eqs = re.findall(r"<hp:script>([^<]*)</hp:script>", xml)
    assert eqs == ["4", "8", "12", "16", "18"]   # 5개 개별 (이전엔 1개 '4 ② 8…')
    # ②③④⑤가 수식 스크립트 안에 갇히지 않음
    assert not any("②" in e for e in eqs)
