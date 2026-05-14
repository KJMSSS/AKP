"""
OCR Fallback — Claude Vision 재처리 모듈

Mathpix OCR 결과에서 복구 불가능한 손상을 감지하면
원본 PDF를 Claude Vision API로 재처리하여 더 나은 마크다운을 반환.

손상 판단 기준:
  1. 수식 구분자($...$, $$...$$) 안에 한글 포함
  2. Mathpix가 텍스트 대신 이미지 링크를 반환한 영역 존재
     (https://cdn.mathpix.com/cropped/... 패턴)
"""

import base64
import os
import re
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

# ── 손상 감지 패턴 ────────────────────────────────────────────────────
_KOREAN = re.compile("[가-힣]")

# Mathpix가 OCR 포기하고 이미지로 대체한 영역
_MATHPIX_IMG = re.compile(r"!\[\]\(https://cdn\.mathpix\.com/")

# 인라인 수식 안 한글: $...[가-힣]...$
_INLINE_MATH = re.compile(r"(?<!\$)\$(?!\$)((?:[^\$\n])+?)(?<!\$)\$(?!\$)")

# 디스플레이 수식 블록: $$...$$
_DISPLAY_MATH = re.compile(r"\$\$([\s\S]+?)\$\$")

# ── Claude Vision 프롬프트 ─────────────────────────────────────────────
_VISION_PROMPT = """\
이 수학 시험지를 마크다운으로 변환해줘.

[추출 규칙]
- 인쇄된 본문만 추출. 학생 손글씨 풀이·마킹은 완전히 무시.
- 모든 수식은 LaTeX: 인라인은 $...$, 디스플레이(별도 줄)는 $$...$$
- 문제 번호(1. 2. 3. ...) 유지
- 선택지 ①②③④⑤ 유지 (인쇄된 것만)
- 배점 [N점] 유지
- 도형·그래프가 있으면 [그림] 으로 표시
- 학교명·시험 정보·저작권 문구 유지
- 표는 마크다운 테이블로

결과는 마크다운만, 설명·주석 없이.\
"""

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 8192
_COST_PER_M_INPUT = 3.0    # $/M tokens (claude-sonnet-4-6)
_COST_PER_M_OUTPUT = 15.0  # $/M tokens


# ── 손상 감지 ─────────────────────────────────────────────────────────

def _has_damage(md: str) -> tuple[bool, list[str]]:
    """
    Mathpix 마크다운에서 손상 패턴을 감지.
    반환: (손상 여부, 감지된 패턴 목록)
    """
    reasons: list[str] = []

    # 패턴 1: Mathpix CDN 이미지 링크 (OCR 포기 = 복구 불가 영역)
    img_matches = _MATHPIX_IMG.findall(md)
    if img_matches:
        reasons.append(f"Mathpix 이미지 대체 {len(img_matches)}건")

    # 패턴 2: 인라인 수식 안에 한글
    for m in _INLINE_MATH.finditer(md):
        if _KOREAN.search(m.group(1)):
            reasons.append(f"수식 내 한글: {m.group(0)[:50]}")
            break

    # 패턴 3: 디스플레이 수식 블록이 한글 주도 (≥30%)
    for m in _DISPLAY_MATH.finditer(md):
        inner = m.group(1)
        non_ws = re.sub(r"\s", "", inner)
        if non_ws:
            korean_ratio = len(_KOREAN.findall(non_ws)) / len(non_ws)
            if korean_ratio >= 0.3:
                reasons.append(f"디스플레이 수식 한글 비율 {korean_ratio:.0%}")
                break

    return bool(reasons), reasons


# ── Claude Vision 재처리 ──────────────────────────────────────────────

def _vision_reocr(pdf_path: Path) -> tuple[str, float]:
    """
    PDF 전체를 Claude Vision으로 재처리.
    반환: (마크다운 문자열, 추정 USD 비용)
    """
    pdf_bytes = pdf_path.read_bytes()
    b64 = base64.standard_b64encode(pdf_bytes).decode()

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    t0 = time.time()

    resp = client.messages.create(
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": _VISION_PROMPT},
                ],
            }
        ],
    )

    elapsed = time.time() - t0
    in_tok = resp.usage.input_tokens
    out_tok = resp.usage.output_tokens
    cost = (in_tok * _COST_PER_M_INPUT + out_tok * _COST_PER_M_OUTPUT) / 1_000_000

    print(
        f"  [fallback] Vision 완료: {elapsed:.1f}s  "
        f"입력 {in_tok:,}tok / 출력 {out_tok:,}tok  "
        f"비용 ${cost:.4f} (≈₩{cost * 1400:.0f})"
    )
    return resp.content[0].text, cost


# ── 공개 API ──────────────────────────────────────────────────────────

def apply_fallback(md: str, pdf_path: Path) -> str:
    """
    Mathpix OCR 마크다운에 손상 패턴이 있으면 Claude Vision으로 재처리.
    손상 없으면 원본 그대로 반환.

    사용 예 (pdf_to_text.py에 한 줄 삽입):
        md = apply_fallback(md, pdf_path)
    """
    damaged, reasons = _has_damage(md)

    if not damaged:
        print("  [fallback] 손상 패턴 없음 — 원본 유지")
        return md

    print(f"  [fallback] 손상 감지: {'; '.join(reasons)}")
    print(f"  [fallback] Claude Vision으로 전체 재처리: {pdf_path.name}")

    result, _ = _vision_reocr(pdf_path)
    return result
