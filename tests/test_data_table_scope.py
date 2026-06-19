"""본문 데이터표 자동 변환 스코프 회귀 테스트 (text_builder).

배경: build_section은 모든 `|`-블록을 HWPX 표로 변환했는데, OCR 마크다운엔
표지·결재칸·배점 안내·채점기준표(해설측)도 표로 들어와 오변환됐다. 또한
불균형 `$$`가 있으면 _preprocess_md의 멀티라인 `$$` 합치기가 표·뒤 본문을
통째로 흡수해 표가 사라졌다. 두 결함을 검증한다.

- _keep_table_block: 문제 본문 데이터표만 True, 머릿말/키워드/고아/이미지 제외
- _preprocess_md: `$$` 합치기가 표 행(`\n|`)을 건너뛰지 않음
- build_section: 머릿말 결재표는 텍스트, 본문 데이터표만 HWPX 표
"""
from __future__ import annotations

import re

from src.text_only.text_builder import (
    _keep_table_block, _preprocess_md, _HwpxWriter,
)


def _tbl(*rows: str) -> list[str]:
    return list(rows)


# ── _keep_table_block ──────────────────────────────────────────────────────

def test_keep_body_table():
    """문제 본문(seen=True) + 구분선 + 2행 이상 → 변환."""
    blk = _tbl("| 학교 | A | B |", "| :---: | :---: | :---: |",
               "| 1학년 | 300 | 200 |", "| 2학년 | 250 | 150 |")
    assert _keep_table_block(blk, seen_problem=True) is True


def test_exclude_before_first_problem():
    """첫 문제 이전(머릿말 표지·결재) → 제외."""
    blk = _tbl("| 과목 | 점수 |", "| :--: | :--: |", "| 수학 | 100 |")
    assert _keep_table_block(blk, seen_problem=False) is False


def test_exclude_keyword_gyeoljae():
    """결재칸(키워드 '결재'/'교장') → 제외 (본문이어도)."""
    blk = _tbl("| 결재 | 교감 | 교장 |", "| :--: | :--: | :--: |", "|  |  |  |")
    assert _keep_table_block(blk, seen_problem=True) is False


def test_exclude_keyword_chaejeom():
    """해설측 채점기준표 → 제외."""
    blk = _tbl("| 단계 | 채점기준 | 배점 |", "| :--: | :--: | :--: |",
               "| 1 | 식을 세우면 | 3점 |")
    assert _keep_table_block(blk, seen_problem=True) is False


def test_exclude_orphan_single_row():
    """구분선 없는 고아 1행 조각 → 표 아님."""
    blk = _tbl("| 학교 | A | B |")
    assert _keep_table_block(blk, seen_problem=True) is False


def test_exclude_image_only_cells():
    """이미지 전용 셀(도장칸) — 정리 후 텍스트 전무 → 제외."""
    blk = _tbl("| ![](http://x/a.jpg) | ![](http://x/b.jpg) |",
               "| :--: | :--: |", "| ![](http://x/c.jpg) | ![](http://x/d.jpg) |")
    assert _keep_table_block(blk, seen_problem=True) is False


def test_keep_no_sep_but_multirow():
    """구분선이 유실됐어도 2행 이상이면 표로 인정(OCR 구분선 누락 대응)."""
    blk = _tbl("| 학년 | 인원 |", "| 1 | 30 |", "| 2 | 25 |")
    assert _keep_table_block(blk, seen_problem=True) is True


# ── _preprocess_md: $$ 합치기가 표를 삼키지 않음 ────────────────────────────

def test_preprocess_dollar_does_not_swallow_table():
    """불균형 $$ 앞뒤에 표가 있어도 표 행이 보존된다."""
    md = "문제 본문 $$\n| 학년 | A | B |\n| :--: | :--: | :--: |\n| 1 | 3 | 2 |\n뒤 본문 $$ 끝"
    lines = _preprocess_md(md)
    pipe_lines = [l for l in lines if l.strip().startswith("|")]
    assert len(pipe_lines) == 3, f"표 3행 보존 기대, 실제 {pipe_lines}"


def test_preprocess_real_multiline_equation_still_joins():
    """표가 없는 정상 멀티라인 $$수식$$ 은 여전히 한 줄로 합쳐진다 (무회귀)."""
    md = "$$\nx = 1\ny = 2\n$$"
    lines = [l for l in _preprocess_md(md) if l.strip()]
    assert len(lines) == 1 and lines[0].startswith("$$") and lines[0].endswith("$$")
    assert "x = 1 y = 2" in lines[0]


# ── build_section 통합: 머릿말 결재표 텍스트 / 본문표만 변환 ─────────────────

def _count_tables(xml: str) -> int:
    return xml.count('numberingType="TABLE"')


def test_build_section_excludes_header_table_keeps_body():
    """머릿말 결재표(문제 전) → 표 아님, 문제 본문 데이터표 → HWPX 표 1개."""
    lines = [
        "| 결재 | 교감 | 교장 |", "| :--: | :--: | :--: |", "| 가 | 나 | 다 |",
        "1. 다음 표를 보고 물음에 답하시오.",
        "| 학년 | 인원 |", "| :--: | :--: |", "| 1 | 30 |", "| 2 | 25 |",
    ]
    xml = _HwpxWriter().build_section(lines)
    assert _count_tables(xml) == 1, xml
    # 결재 텍스트는 본문에 남되 표가 아님
    assert "결재" in xml


def test_build_section_br_in_cell_to_space():
    """셀 내 <br> 은 공백으로 정리(리터럴 &lt;br&gt; 방지)."""
    lines = [
        "1. 메뉴를 고르시오.",
        "| 메뉴 | 설명 |", "| :--: | :--: |", "| 코스 | 수프 <br> 스테이크 |",
    ]
    xml = _HwpxWriter().build_section(lines)
    assert "&lt;br&gt;" not in xml and "<br" not in xml   # <br> 리터럴 잔여 없음
    assert "수프" in xml and "스테이크" in xml             # 내용 보존(공백으로 분리)


def test_data_table_cells_use_solid_borderfill():
    """데이터표 셀이 사방 SOLID(BF2) — 워드초벌 템플릿에서 BF3(무테)/BF4(부분테)라
    표가 거의 안 보이던 버그 회귀 방지."""
    import re
    lines = ["1. 다음 표를 보고 답하시오.",
             "| 구분 | A | B |", "| :--: | :--: | :--: |", "| 1학년 | 3 | 2 |"]
    xml = _HwpxWriter().build_section(lines)
    tm = re.search(r'<hp:tbl[^>]*numberingType="TABLE".*?</hp:tbl>', xml, re.DOTALL)
    cell_bfs = re.findall(r'<hp:tc[^>]*borderFillIDRef="(\d+)"', tm.group(0))
    assert cell_bfs and all(b == "2" for b in cell_bfs), cell_bfs
