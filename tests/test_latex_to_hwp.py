"""LaTeX → 한글 수식 스크립트 변환 단위 테스트 (backend/mathconv).

examconv 이식 엔진의 절대 규칙 회귀 방지:
- 위/아래첨자 인자는 반드시 중괄호로 감싼다 (x^2 → x^{2})
  — 한글 수식은 ^/_ 뒤 인자를 공백 경계까지 탐욕 흡수하므로 미준수 시 렌더 붕괴.
- 미지원 명령은 ok=False (상위가 이미지 폴백).
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from mathconv.latex_to_hwp import latex_to_hwp  # noqa: E402


def test_frac_simple():
    r = latex_to_hwp(r"\frac{1}{2}")
    assert r.ok
    assert r.script == "{1} over {2}"


def test_sqrt():
    r = latex_to_hwp(r"\sqrt{x}")
    assert r.ok
    assert "sqrt" in r.script and "{x}" in r.script


def test_superscript_braced():
    # x^2+3x+2=0 이 통째로 위첨자가 되지 않도록 ^ 인자를 중괄호로 감싼다
    r = latex_to_hwp(r"x^2+3x+2=0")
    assert r.ok
    assert "x^{2}" in r.script


def test_subscript_braced():
    r = latex_to_hwp(r"a_1 + a_n")
    assert r.ok
    assert "a_{1}" in r.script and "a_{n}" in r.script


def test_delimiters_stripped():
    r = latex_to_hwp(r"\( \frac{a}{b} \)")
    assert r.ok
    assert "over" in r.script


def test_box_blank_symbol():
    # 빈칸(□) 인수분해 문제를 LLM 이 \Box 로 옮김 — 2026-07-19 실사고 5건
    # (\Box x^2 - 16x + 4 등이 '미지원 명령: Box'로 확인필요 폴백)
    r = latex_to_hwp(r"\Box x^2 - 16x + 4")
    assert r.ok
    assert "□" in r.script and "x^{2}" in r.script
    r2 = latex_to_hwp(r"a^2 + \Box a + 64")
    assert r2.ok
    assert "□" in r2.script


def test_unsupported_command_falls_back():
    r = latex_to_hwp(r"\unknowncommand{x}")
    assert not r.ok
    assert r.reason


def test_unbalanced_braces_rejected():
    # \frac{1}{2 는 변환 정규식이 자동 보정하므로, 보정 불가능한 불균형으로 검증
    r = latex_to_hwp(r"x^{2")
    assert not r.ok
    assert "불균형" in r.reason
