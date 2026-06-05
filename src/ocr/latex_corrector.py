"""
2차 LaTeX 교정 패스.

OCR 1차 출력의 마크다운에서 수식($...$, $$...$$)만 검토해서
LaTeX 문법 오류를 수정한다. 텍스트 내용은 절대 변경하지 않는다.

비용: 1차의 10~20% 추가 (수식 위주 짧은 응답)
"""
from __future__ import annotations

import os
import re
import time

import anthropic
from dotenv import load_dotenv

from src.ocr.cost_guard import CostGuard

load_dotenv()

_MODEL = "claude-haiku-4-5-20251001"   # 비용 절감: Haiku로 교정
_COST_PER_M_INPUT  = 0.8
_COST_PER_M_OUTPUT = 4.0
_MAX_TOKENS = 8192

_SYSTEM_CORRECTOR = """\
당신은 한국 수학 마크다운 문서의 LaTeX 수식 교정 전문가입니다.

[임무]
입력으로 받은 마크다운 문서의 $...$ 및 $$...$$ 수식 안의 LaTeX 오류만 수정하세요.
한국어 텍스트, 문제 번호, 선택지, 배점 등 수식 바깥의 내용은 절대 변경하지 마세요.

[교정 기준 — 자주 틀리는 패턴]
- 지수·첨자 중괄호 누락:  x^2 → x^{2},  a_n → a_{n},  3^-x → 3^{-x}
- 백슬래시 누락:  sin → \\sin,  cos → \\cos,  log → \\log,  lim → \\lim,  ln → \\ln
- 분수 표기:  frac{a}{b} → \\frac{a}{b}  (백슬래시 누락)
- 루트 표기:  sqrt{x} → \\sqrt{x}
- 그리스 문자:  alpha → \\alpha,  theta → \\theta,  pi → \\pi
- 극한:  lim_{x→a} → \\lim_{x \\to a}  (화살표도 \\to)
- 부등호:  ≤ → \\leq,  ≥ → \\geq
- 집합 기호:  ∈ → \\in,  ∪ → \\cup,  ∩ → \\cap
- 무한대:  ∞ → \\infty

[출력 형식]
수정된 마크다운 전체를 그대로 출력하세요.
수식 외에 아무것도 바꾸지 마세요. 설명, 주석, 마크다운 코드블록 래핑 금지.\
"""


def correct_latex(
    md: str,
    *,
    subject: str = "",
    cost_cap_usd: float = 5.0,
) -> str:
    """
    OCR 마크다운의 LaTeX 수식 오류를 2차 패스로 교정.

    수식이 전혀 없으면 API를 호출하지 않고 원문 반환.
    subject: 과목 ID — 교정 기준 보강에 사용.
    """
    if not re.search(r'\$', md):
        return md

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return md

    guard = CostGuard(cap_usd=cost_cap_usd)
    try:
        guard.check_or_raise("latex_corrector")
    except Exception:
        print("  [교정] 비용 한도 초과 — 교정 패스 건너뜀")
        return md

    system = _SYSTEM_CORRECTOR
    if subject:
        system += f"\n\n[과목: {subject}]\n이 과목의 주요 수식 패턴에 특히 주의해서 교정하세요."

    client = anthropic.Anthropic(api_key=api_key)
    t0 = time.time()
    resp = client.messages.create(
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": md}],
    )
    elapsed = time.time() - t0

    result = resp.content[0].text if resp.content else md
    cost = (
        resp.usage.input_tokens  / 1_000_000 * _COST_PER_M_INPUT +
        resp.usage.output_tokens / 1_000_000 * _COST_PER_M_OUTPUT
    )
    guard.record("latex_corrector", cost)
    print(f"  [교정] LaTeX 2차 패스 완료: {elapsed:.1f}s  ${cost:.4f}")
    return result
