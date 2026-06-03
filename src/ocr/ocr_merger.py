"""
Claude Vision + Mathpix OCR 결과 병합.

전략:
  - 한글 텍스트 · 레이아웃 · 그림 마커: Claude Vision 기준
  - 수식($...$ / $$...$$): Mathpix 기준 (더 정확한 LaTeX)
  - 수식 개수 일치 시: 위치 순서로 1:1 교체 (규칙 기반, 무료)
  - 불일치 시: Vision 수식 유지 + 선택적 LLM(Haiku) 병합

사용:
  from src.ocr.ocr_merger import merge_vision_mathpix
  merged = merge_vision_mathpix(vision_text, mathpix_mmd)
  # LLM 병합 활성화
  merged = merge_vision_mathpix(vision_text, mathpix_mmd, client=anthropic_client)
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import anthropic

# ── 수식 추출 ─────────────────────────────────────────────────────────────────

_DISPLAY_RE = re.compile(r'\$\$(.+?)\$\$', re.DOTALL)
_INLINE_RE  = re.compile(r'(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)', re.DOTALL)

# Mathpix \(...\) / \[...\] → $...$ 변환용
_MP_INLINE_RE  = re.compile(r'\\\((.+?)\\\)', re.DOTALL)
_MP_DISPLAY_RE = re.compile(r'\\\[(.+?)\\\]', re.DOTALL)


def _mathpix_to_dollars(mmd: str) -> str:
    """Mathpix \\(...\\) / \\[...\\] → $...$ / $$...$$ 변환."""
    s = _MP_DISPLAY_RE.sub(r'$$\1$$', mmd)
    s = _MP_INLINE_RE.sub(r'$\1$', s)
    return s


def _extract_math_spans(text: str) -> list[tuple[int, int, str, str]]:
    """
    텍스트에서 수식 위치 목록 반환.
    [(start, end, kind, content), ...]  kind = 'display' | 'inline'
    """
    spans: list[tuple[int, int, str, str]] = []
    for m in _DISPLAY_RE.finditer(text):
        spans.append((m.start(), m.end(), 'display', m.group(1)))
    for m in _INLINE_RE.finditer(text):
        # display 범위와 겹치면 skip
        if not any(s <= m.start() and m.end() <= e for s, e, *_ in spans):
            spans.append((m.start(), m.end(), 'inline', m.group(1)))
    return sorted(spans, key=lambda x: x[0])


def _replace_math_by_position(vision_text: str, mp_maths: list[str]) -> str:
    """
    Vision 텍스트의 수식을 Mathpix 수식으로 순서대로 교체.
    수식 개수가 달라도 min(len) 까지만 교체, 나머지는 원본 유지.
    """
    spans = _extract_math_spans(vision_text)
    if not spans or not mp_maths:
        return vision_text

    result = []
    prev_end = 0
    for i, (start, end, kind, _) in enumerate(spans):
        result.append(vision_text[prev_end:start])
        if i < len(mp_maths):
            content = mp_maths[i].strip()
            result.append(f'$${content}$$' if kind == 'display' else f'${content}$')
        else:
            result.append(vision_text[start:end])
        prev_end = end
    result.append(vision_text[prev_end:])
    return ''.join(result)


def _extract_math_contents(text: str) -> list[str]:
    """텍스트에서 수식 내용만 순서대로 추출."""
    spans = _extract_math_spans(text)
    return [content for _, _, _, content in spans]


# ── LLM 병합 ─────────────────────────────────────────────────────────────────

_MERGE_PROMPT = """\
두 OCR 결과를 병합해 하나의 마크다운을 만들어라.

[Claude Vision OCR] — 한글·레이아웃·선택지 정확
{vision}

[Mathpix OCR] — 수식(LaTeX) 정확
{mathpix}

규칙:
- 한글 텍스트·선택지·배점·문제번호·레이아웃: Vision 기준
- 수식($...$, $$...$$): Mathpix 기준으로 교체 (더 정확한 LaTeX)
- 그림 마커 【★ 그림:N번】: Vision 위치 그대로 보존
- 설명 없이 마크다운만 출력
"""


def _llm_merge(vision_text: str, mathpix_dollars: str, client: 'anthropic.Anthropic') -> str:
    """Haiku로 Vision + Mathpix 병합 (불일치 시 fallback)."""
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": _MERGE_PROMPT.format(vision=vision_text, mathpix=mathpix_dollars),
        }],
    )
    return resp.content[0].text.strip()


# ── 공개 API ─────────────────────────────────────────────────────────────────

def merge_vision_mathpix(
    vision_text: str,
    mathpix_mmd: str,
    client: 'anthropic.Anthropic | None' = None,
) -> str:
    """
    Claude Vision OCR 텍스트와 Mathpix MMD를 병합.

    vision_text  : VisionOCRResult.text ($...$ 형식)
    mathpix_mmd  : Mathpix raw_ocr_image 의 mmd 또는 text 필드
                   (\\(...\\) / \\[...\\] 또는 $...$ 모두 허용)
    client       : anthropic.Anthropic 인스턴스 — 있으면 불일치 시 Haiku 병합
                   None이면 Vision 텍스트를 그대로 반환(불일치 시)

    반환: 병합된 마크다운 문자열
    """
    if not vision_text:
        return mathpix_mmd or ''
    if not mathpix_mmd:
        return vision_text

    mp_dollars = _mathpix_to_dollars(mathpix_mmd)

    v_maths  = _extract_math_contents(vision_text)
    mp_maths = _extract_math_contents(mp_dollars)

    # 수식 개수 비교
    v_cnt  = len(v_maths)
    mp_cnt = len(mp_maths)

    if v_cnt == 0:
        return vision_text

    if v_cnt == mp_cnt:
        # 완전 일치 → 1:1 교체
        return _replace_math_by_position(vision_text, mp_maths)

    diff = abs(v_cnt - mp_cnt)
    if diff <= 2 and mp_cnt > 0:
        # 소폭 불일치 → 맞는 개수까지 교체
        return _replace_math_by_position(vision_text, mp_maths)

    # 큰 불일치
    if client is not None:
        try:
            return _llm_merge(vision_text, mp_dollars, client)
        except Exception as e:
            print(f"    [merge] LLM 병합 실패({e}), Vision 텍스트 사용")

    return vision_text


def merge_all(
    vision_texts: dict[int, str],
    mathpix_mmds: dict[int, str],
    client: 'anthropic.Anthropic | None' = None,
) -> dict[int, str]:
    """문제 번호 → 병합 텍스트 딕셔너리 반환."""
    result: dict[int, str] = {}
    for num in sorted(vision_texts):
        v_text = vision_texts.get(num, '')
        mp_mmd = mathpix_mmds.get(num, '')
        merged = merge_vision_mathpix(v_text, mp_mmd, client=client)
        result[num] = merged
    return result
