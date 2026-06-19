"""데이터표 원자 보존 회귀 테스트 (problem_segmenter).

배경: `_split_block`이 `[N점]` 경계로 블록을 나누고 선택지 5개만 수집·나머지
폐기하면서, 문제 본문 표의 데이터행이 선택지로 오분류되거나 폐기됐다(광주고
16번: 8행 중 1행만 생존). `_extract_tables`로 표를 _split_block 이전에 떼어내
`ProblemSegment.data_tables`에 보존하고, rebuild_markdown이 본문 직후 재부착한다.

채점기준표/결재칸은 추출하지 않아(키워드) 기존 드롭/스코프 동작을 유지한다
— 국제고 서술형 채점표가 본문에 끼어 포함도가 떨어지던 회귀를 방지.
"""
from __future__ import annotations

from src.text_only.problem_segmenter import (
    _extract_tables, parse_problems, rebuild_markdown,
)


def test_extract_genuine_data_table():
    """구분행 + 데이터 2행 이상 → 추출, 본문 줄에서 제거."""
    lines = [
        "17. 다음 표를 보고 물음에 답하시오. [3점]",
        "| 학교 | A | B |", "| :--: | :--: | :--: |",
        "| 1학년 | 300 | 200 |", "| 2학년 | 250 | 150 |",
    ]
    clean, tables = _extract_tables(lines)
    assert len(tables) == 1
    assert "| 학교 | A | B |" in tables[0] and "| 2학년 | 250 | 150 |" in tables[0]
    assert not any(l.strip().startswith("|") for l in clean)   # 본문엔 표줄 없음


def test_skip_rubric_table_keyword():
    """채점기준표(채점요소/배점) → 추출 안 함, 줄은 통째로 보존(재수집 방지)."""
    lines = [
        "서술형1) 풀이를 쓰시오. [6점]",
        "| 단계 | 채점요소 | 배점 |", "|---|---|---|",
        "| 1 | 식을 세우면 | 3점 |", "| 2 | 답을 구하면 | 3점 |",
    ]
    clean, tables = _extract_tables(lines)
    assert tables == []                       # 채점표 미추출
    # 구분행이 헤더 없는 새 블록으로 재수집되어 추출되지 않아야 함
    assert sum(1 for l in clean if l.strip().startswith("|")) == 4


def test_skip_gyeoljae_box():
    """결재칸(결재/교장 키워드) → 추출 안 함."""
    lines = [
        "1. 문제. [2점]",
        "| 결재 | 교감 | 교장 |", "| :--: | :--: | :--: |", "| 가 | 나 | 다 |",
    ]
    _, tables = _extract_tables(lines)
    assert tables == []


def test_skip_orphan_single_row():
    """구분행 없는 고아 1행 → 추출 안 함(기존 동작 유지)."""
    lines = ["1. 문제. [2점]", "| 학교 | A | B |", "본문 계속"]
    _, tables = _extract_tables(lines)
    assert tables == []


def test_no_table_is_noop():
    """표가 없으면 줄을 그대로 반환(0-table 학교 바이트 동일 보장)."""
    lines = ["1. 문제. [2점]", "① 1", "② 2", "③ 3"]
    clean, tables = _extract_tables(lines)
    assert clean == lines and tables == []


def test_parse_and_rebuild_preserves_table_atomically():
    """parse_problems → rebuild_markdown 왕복에서 표가 본문 직후 한 덩어리로 보존."""
    md = "\n".join([
        "17. 다음 표를 보고 답하시오. [3점]",
        "| 학교 | A | B |", "| :--: | :--: | :--: |",
        "| 1학년 | 300 | 200 |", "| 2학년 | 250 | 150 |",
        "① 1", "② 2", "③ 3", "④ 4", "⑤ 5",
    ])
    header, segs = parse_problems(md)
    s17 = next(s for s in segs if s.number == 17)
    assert len(s17.data_tables) == 1                     # 표 8→0 유실 없이 1블록 보존
    out = rebuild_markdown(header, segs)
    # 표 4행(헤더+구분+데이터2)이 재조립 md에 모두 contiguous하게 존재
    pipe_lines = [l for l in out.split("\n") if l.strip().startswith("|")]
    assert len(pipe_lines) == 4
