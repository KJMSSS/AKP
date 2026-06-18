"""수피아여고 진단에서 나온 3대 수정 회귀 테스트.

B) typer가 닫는괄호형 단답형 번호 "5)" 를 문제로 인식 (마침표형 "5." 도 유지).
C) 거대 페이지(수피아 수2 4284×5712pt) 렌더 DPI 자동 축소 → DecompressionBomb 방지.
(A 가드는 scripts/gold_compare 의 하네스 동작 — end-to-end 로 검증됨.)
"""
from __future__ import annotations

import fitz

from src.text_only.typer_builder import _parse_prob_header
from src.common.pdf_utils import _safe_dpi, _OSD_MAX_PX, _RENDER_MAX_PX


def _para(text: str) -> str:
    return f'<hp:p><hp:run><hp:t>{text}</hp:t></hp:run></hp:p>'


# ── Fix B: 단답형 "N)" 인식 ────────────────────────────────────────────────

def test_parse_prob_header_paren_short_answer():
    """닫는괄호형 단답형 '5)' 를 문제(번호 5, 배점 3.9)로 인식."""
    assert _parse_prob_header(_para('5) $x+y=2$ 일 때 값을 구하시오. [3.9점]')) == (5, 3.9)


def test_parse_prob_header_period_still_works():
    """기존 마침표형 '12.' 은 그대로 유지 (회귀 방지)."""
    no, _ = _parse_prob_header(_para('12. 다음을 구하시오. [4점]'))
    assert no == 12


def test_parse_prob_header_non_problem_line():
    """문제 번호가 아닌 줄(풀이·본문)은 0 — 오검출 방지."""
    assert _parse_prob_header(_para('따라서 답은 3 이다'))[0] == 0
    assert _parse_prob_header(_para('① 보기 내용'))[0] == 0


# ── Fix C: 거대 페이지 DPI 상한 ────────────────────────────────────────────

def test_safe_dpi_caps_giant_page():
    """4284×5712pt(수피아 수2): 150dpi→106M px > 한도 → DPI 축소로 상한 이하."""
    doc = fitz.open()
    giant = doc.new_page(width=4284, height=5712)
    s = _safe_dpi(giant, 150, _OSD_MAX_PX)
    px = (4284 / 72 * s) * (5712 / 72 * s)
    assert s < 150 and px <= _OSD_MAX_PX * 1.02
    doc.close()


def test_safe_dpi_keeps_normal_page():
    """일반 A4(595×842pt)는 상한 이하 → DPI 그대로 (품질 보존)."""
    doc = fitz.open()
    a4 = doc.new_page(width=595, height=842)
    assert _safe_dpi(a4, 250, _RENDER_MAX_PX) == 250.0
    doc.close()


def test_safe_dpi_floor_72():
    """극단적으로 큰 페이지라도 최소 72dpi 는 보장 (OSD 가독성)."""
    doc = fitz.open()
    huge = doc.new_page(width=10000, height=10000)
    assert _safe_dpi(huge, 150, _OSD_MAX_PX) >= 72.0
    doc.close()
