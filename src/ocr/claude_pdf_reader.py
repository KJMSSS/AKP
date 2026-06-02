"""
Claude API 기반 PDF → 마크다운 변환.

Mathpix fetch_pdf_markdown()과 동일한 형식으로 반환:
  - 인라인 수식: $...$
  - 디스플레이 수식: $$...$$

vision_stage_a.py의 document API 패턴 재사용.
"""
from __future__ import annotations

import base64
import os
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from src.ocr.cost_guard import CostGuard

load_dotenv()

_MODEL = "claude-sonnet-4-6"
_COST_PER_M_INPUT  = 3.0    # USD / 1M input tokens
_COST_PER_M_OUTPUT = 15.0   # USD / 1M output tokens
_MAX_TOKENS      = 8192
_MAX_TOKENS_FULL = 16000  # 정답·해설 포함 시 더 긴 출력 필요
_PAGE_CHUNK = 15             # 청크당 최대 페이지 수

# ── 시스템 프롬프트 (문제만) ────────────────────────────────────────────
_SYSTEM = """\
당신은 한국 수학 시험지 PDF를 정확하게 마크다운으로 전사하는 전문가입니다.

[출력 규칙]
1. 수식 표기
   - 문장 안 인라인 수식: $LaTeX$
   - 독립 줄 디스플레이 수식: $$LaTeX$$
   - 수식 내용은 LaTeX 원문 그대로
2. 한국어 텍스트: 보이는 그대로 정확히 전사 (추측·수정 금지)
3. 문제 번호: "1." "2." ... 형식으로 줄 시작
4. 선택지: ①②③④⑤ 원형 마커 그대로 사용
5. 배점: 문제 끝에 [3점] [4점] 형태 그대로 유지
6. 그림·도표 위치: 해당 위치에 【★ 그림:N번】 마커 삽입 (N=해당 문제번호)
7. 제거 대상
   - 페이지 헤더/푸터 (페이지 번호, 학교명, 날짜)
   - 학생 답안 기입란 (빈 칸, 채점표)
   - 결재선·서명란

[절대 금지]
- 수식 해설·풀이 추가 금지
- 답안 추론·추가 금지
- 원문에 없는 내용 추가 금지
- 마크다운 헤더(#, ##) 사용 금지
- 코드 블록(```) 사용 금지\
"""

# ── 시스템 프롬프트 (정답·해설 포함 전체) ──────────────────────────────
_SYSTEM_FULL = r"""\
당신은 한국 수학 시험지 PDF를 정확하게 마크다운으로 전사하는 전문가입니다.
문제, 정답, 해설, 풀이 과정 등 PDF에 인쇄된 모든 내용을 빠짐없이 전사합니다.

[수식 정확도 — 최우선]
- 인라인 수식: $LaTeX$  /  독립 줄 수식: $$LaTeX$$
- 분수: \dfrac{분자}{분모}  (인라인도 \dfrac 사용)
- 거듭제곱·지수: x^{2}, a^{n+1}  (두 자리 이상은 반드시 중괄호)
- 아래첨자: a_{n}, S_{10}  (두 자리 이상은 중괄호)
- 루트: \sqrt{x}, \sqrt[n]{x}
- 극한: \lim_{x \to a}, \lim_{n \to \infty}
- 적분: \int_{a}^{b} f(x)\,dx
- 시그마: \sum_{k=1}^{n}
- 조합: \binom{n}{r}  또는 {}_{n}C_{r}
- 벡터·절댓값: \overrightarrow{AB}, |x|, \left| \dfrac{a}{b} \right|
- 삼각함수: \sin, \cos, \tan, \log (백슬래시 필수)
- 그리스 문자: \alpha, \beta, \pi, \theta 등
- 부등호: \leq, \geq, \neq
- 집합 기호: \in, \subset, \cup, \cap
- 무한대: \infty
- 행렬·cases 환경: \begin{cases} ... \end{cases}
- 괄호 크기 자동 조절: \left( ... \right), \left[ ... \right]

[내용 전사 규칙]
1. 한국어 텍스트: 보이는 그대로 정확히 전사 (추측·수정 금지)
2. 문제 번호·섹션 제목 (정답, 해설, 풀이 등): 원문 그대로 유지
3. 선택지: ①②③④⑤ 원형 마커 그대로 사용
4. 배점·정답·채점 기준·모범 답안: 원문 그대로 전사
5. 풀이·해설: 풀이 단계와 계산 과정 포함하여 원문 그대로 전사
6. 그림·도표 위치: 해당 위치에 【★ 그림】 마커 삽입
7. 제거 대상: 페이지 헤더/푸터(페이지 번호, 날짜), 빈 답안 기입란·채점표

[절대 금지]
- 원문에 없는 내용 추가 금지
- 수식을 텍스트로 쓰거나 근사 표현 사용 금지
- 마크다운 헤더(#, ##) 사용 금지
- 코드 블록(```) 사용 금지\
"""

_USER_PROMPT = "이 시험지 PDF의 모든 문제를 마크다운 형식으로 전사해주세요."
_USER_PROMPT_FULL = "이 PDF에 인쇄된 모든 내용(문제, 정답, 해설, 풀이 과정)을 빠짐없이 마크다운으로 전사해주세요."


def read_pdf_as_markdown(
    pdf_path: Path,
    *,
    max_tokens: int | None = None,
    cost_cap_usd: float = 5.0,
    full_content: bool = False,
) -> str:
    """
    PDF → Claude API → Mathpix 호환 마크다운.

    full_content=True 이면 정답·해설을 포함한 모든 내용을 전사.
    반환값은 $...$  / $$...$$ 형식이므로 기존 파이프라인 그대로 사용 가능.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.")

    guard = CostGuard(cap_usd=cost_cap_usd)
    guard.check_or_raise("claude_pdf")

    pdf_bytes = pdf_path.read_bytes()
    n_pages = _count_pages(pdf_bytes)

    system      = _SYSTEM_FULL if full_content else _SYSTEM
    user_prompt = _USER_PROMPT_FULL if full_content else _USER_PROMPT
    if max_tokens is None:
        max_tokens = _MAX_TOKENS_FULL if full_content else _MAX_TOKENS

    mode_label = "전체(정답·해설 포함)" if full_content else "문제만"
    print(f"  [claude_pdf] {pdf_path.name}  ({n_pages}p)  {mode_label}")

    if n_pages == 0 or n_pages <= _PAGE_CHUNK:
        md, cost = _call_api(pdf_bytes, api_key, max_tokens, system, user_prompt)
    else:
        md, cost = _call_api_chunked(pdf_bytes, n_pages, api_key, max_tokens, system, user_prompt)

    guard.record("claude_pdf", cost)
    print(f"  [claude_pdf] 완료: {len(md):,}자  ${cost:.4f}")
    return md


# ── 내부 함수 ──────────────────────────────────────────────────────────


def _count_pages(pdf_bytes: bytes) -> int:
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        n = doc.page_count
        doc.close()
        return n
    except Exception:
        return 0


def _call_api(
    pdf_bytes: bytes,
    api_key: str,
    max_tokens: int,
    system: str,
    user_prompt: str,
) -> tuple[str, float]:
    b64 = base64.standard_b64encode(pdf_bytes).decode()
    client = anthropic.Anthropic(api_key=api_key)

    t0 = time.time()
    resp = client.messages.create(
        model=_MODEL,
        max_tokens=max_tokens,
        system=system,
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
                    {"type": "text", "text": user_prompt},
                ],
            }
        ],
    )

    elapsed = time.time() - t0
    in_tok  = resp.usage.input_tokens
    out_tok = resp.usage.output_tokens
    cost    = (in_tok * _COST_PER_M_INPUT + out_tok * _COST_PER_M_OUTPUT) / 1_000_000
    print(f"  [claude_pdf] {elapsed:.1f}s  in={in_tok:,} out={out_tok:,}  ${cost:.4f}")
    return resp.content[0].text, cost


def _call_api_chunked(
    pdf_bytes: bytes,
    n_pages: int,
    api_key: str,
    max_tokens: int,
    system: str,
    user_prompt: str,
) -> tuple[str, float]:
    """20페이지 초과 PDF를 청크로 나눠 처리."""
    import fitz  # PyMuPDF

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    parts: list[str] = []
    total_cost = 0.0

    for start in range(0, n_pages, _PAGE_CHUNK):
        end = min(start + _PAGE_CHUNK, n_pages)
        print(f"  [claude_pdf] 청크 {start + 1}~{end}p ...")

        sub = fitz.open()
        sub.insert_pdf(doc, from_page=start, to_page=end - 1)
        chunk_bytes = sub.tobytes()
        sub.close()

        chunk_md, cost = _call_api(chunk_bytes, api_key, max_tokens, system, user_prompt)
        parts.append(chunk_md)
        total_cost += cost

    doc.close()
    return "\n\n".join(parts), total_cost
