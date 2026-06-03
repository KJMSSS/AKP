"""
C안 — 문제 크롭 단위 Vision OCR.

한 장의 크롭 이미지에서:
  1. 인쇄된 문제 텍스트 + 수식(LaTeX) 추출  (손풀이 무시)
  2. 그림/도형 여부 + bbox 동시 감지

반환: VisionOCRResult
"""
from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class VisionOCRResult:
    problem_no: str
    text: str               # 마크다운 (수식 $...$, $$...$$)
    has_figure: bool = False
    figure_bbox: list[float] = field(default_factory=list)  # [left%, top%, right%, bottom%]
    raw_response: str = ""


_PROMPT = """\
이 이미지는 한국 수학 시험지에서 {num}번 문제를 크롭한 것입니다.

━━ 반드시 무시할 것 ━━
• 연필·볼펜으로 쓴 학생 손글씨 (풀이 계산, 숫자, 동그라미·체크 표시)
• 지저분한 흔적, 지운 자국

━━ 추출할 것 (인쇄된 내용만) ━━
• 문제 번호 + 문제 텍스트 (한국어)
• 수식 → LaTeX 변환
  - 인라인: $수식$
  - 디스플레이(별도 줄): $$\n수식\n$$
• 선택지: ① ② ③ ④ ⑤
• 배점: [N점] 또는 [N.N점]
• 서술형 라벨: [서술형N] 또는 [단답형N]

━━ LaTeX 필수 규칙 ━━
1. 지수·하첨자는 항상 중괄호 사용
   - 올바름: $x^{2}+ax+b$,  $P_{1}$,  $a_{n}$
   - 잘못됨: $x^2+ax+b$,  $P_1$,  $a_n$

2. 순열·조합은 반드시 {} 앞에 붙임
   - 올바름: ${}_{4}P_{2}$,  ${}_{9}C_{4}$
   - 잘못됨: $_4P_2$,  $_9C_4$

3. 선분 위 bar는 \\overline 사용
   - 올바름: $\\overline{P_{1}C}=a$,  $\\overline{AB}=8$
   - 잘못됨: $\\bar{P_1C}$

4. 조건 (가)(나)(다)는 반드시 전각 괄호 사용
   - 올바름: （가） $z = 3x$
   - 잘못됨: (가) $z = 3x$

5. 보기 ㄱ/ㄴ/ㄷ는 아래 형식 유지
   ㄱ. ...내용...
   ㄴ. ...내용...
   ㄷ. ...내용...

━━ 그림 감지 ━━
인쇄된 수학 도형·그래프·3D 박스가 있으면 bbox를 함께 반환.
(학생이 그린 손그림, 선택지, 문제 텍스트는 bbox에 포함 금지)
bbox는 도형 자체만 타이트하게 — 문제 텍스트나 선택지 행은 포함하지 말 것.

━━ 출력 형식 (JSON만, 설명 없이) ━━
{
  "text": "9. $\\\\angle C=90^{\\\\circ}$ 인 직각삼각형 ABC 가 있다...\\n[3.7점]\\n① 24\\n② 25",
  "has_figure": true,
  "figure_bbox": [left%, top%, right%, bottom%]
}

그림 없으면: "has_figure": false, "figure_bbox": []
"""


def ocr_problem_crop(
    crop_png: Path,
    problem_no: str,
    api_key: str | None = None,
    model: str = "claude-sonnet-4-6",
) -> VisionOCRResult:
    """
    문제 크롭 1장을 Vision OCR.

    crop_png: 문제 단위 크롭 PNG
    problem_no: "9", "18", "서술형5" 등
    반환: VisionOCRResult
    """
    import anthropic

    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY 환경변수 없음")

    client = anthropic.Anthropic(api_key=key)
    b64 = base64.standard_b64encode(crop_png.read_bytes()).decode()
    prompt = _PROMPT.replace("{num}", problem_no)

    resp = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            {"type": "text", "text": prompt},
        ]}],
    )
    raw = resp.content[0].text.strip()

    # JSON 파싱
    try:
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
    except Exception:
        data = {}

    text = data.get("text", "").strip()
    has_figure = bool(data.get("has_figure", False))
    figure_bbox = data.get("figure_bbox", [])

    return VisionOCRResult(
        problem_no=problem_no,
        text=text,
        has_figure=has_figure,
        figure_bbox=figure_bbox if isinstance(figure_bbox, list) else [],
        raw_response=raw,
    )


def ocr_all_crops(
    crop_dir: Path,
    api_key: str | None = None,
    model: str = "claude-sonnet-4-6",
) -> list[VisionOCRResult]:
    """
    crop_dir 안의 prob_N.png 전체를 Vision OCR.

    반환: 문제 번호 순으로 정렬된 VisionOCRResult 리스트
    """
    crops = sorted(
        crop_dir.glob("prob_*.png"),
        key=lambda p: _sort_key(p.stem.replace("prob_", ""))
    )
    results = []
    for crop in crops:
        num = crop.stem.replace("prob_", "")
        print(f"  OCR {num}번...", end=" ", flush=True)
        result = ocr_problem_crop(crop, num, api_key=api_key, model=model)
        ok = "그림O" if result.has_figure else "그림X"
        preview = result.text[:40].replace("\n", " ")
        print(f"{ok}  {preview}")
        results.append(result)
    return results


def build_markdown_from_results(results: list[VisionOCRResult]) -> str:
    """
    VisionOCRResult 리스트 → 전체 raw.md 문자열.
    각 문제 사이에 빈 줄 삽입.
    그림 있는 문제에는 【★ 그림:N번】 마커 자동 삽입.
    """
    parts = []
    for r in results:
        text = r.text
        if r.has_figure:
            # 배점 또는 선택지 바로 앞에 그림 마커 삽입
            marker = f"【★ 그림:{r.problem_no}번】"
            # [N점] 앞, 또는 ① 앞에 삽입
            inserted = False
            for pat in (r'\n(\[[\d.]+점\])', r'\n(①|⑤|\(1\))'):
                m = re.search(pat, text)
                if m:
                    pos = m.start()
                    text = text[:pos] + f"\n{marker}" + text[pos:]
                    inserted = True
                    break
            if not inserted:
                text = text + f"\n{marker}"
        parts.append(text)
    return "\n\n".join(parts)


def _sort_key(num_str: str) -> tuple:
    """prob_9 → (0,9), prob_101 → (1,101) 정렬용."""
    try:
        n = int(num_str)
        return (1, n) if n >= 100 else (0, n)
    except ValueError:
        return (2, 0)
