"""하이브리드 OCR 머지 회귀 테스트 (무네트워크).

구조·선택지·헤더는 Claude, 수식은 Mathpix로 합쳐지는지 + 문제번호 분할 검증.
수식 개수가 일치하면 LLM 호출 없이 위치 교체(무과금)되므로 네트워크 불필요.
"""
from __future__ import annotations

import scripts.text.pdf_to_text as p2t


def test_split_by_problem():
    head, blocks = p2t._split_by_problem("머리말\n\n1. 첫문제\n선택지\n\n2. 둘째")
    assert "머리말" in head
    assert set(blocks) == {1, 2}
    assert blocks[1].startswith("1. 첫문제")


def test_hybrid_merge_uses_mathpix_equations(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)   # client=None → LLM 안 씀
    md_claude = "헤더줄\n\n1. 문제 $x^2$ 이다. ① 1 ② 2\n\n2. 다음 $a+b$\n① 3 ② 4"
    md_mathpix = "1. 문제 $x^{2}$ 이다.\n\n2. 다음 $a + b$"
    out = p2t._hybrid_merge_ocr(md_claude, md_mathpix)
    assert "x^{2}" in out          # 수식 = Mathpix (정밀)
    assert "① 1 ② 2" in out        # 선택지/구조 = Claude
    assert "헤더줄" in out          # 헤더 보존


def test_hybrid_keeps_claude_when_mathpix_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = p2t._hybrid_merge_ocr("1. 문제 $x^2$ 【★ 그림:1번】", "")
    assert "$x^2$" in out and "【★ 그림:1번】" in out   # Mathpix 없으면 Claude 유지
