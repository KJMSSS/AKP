"""
학습 데이터 패턴 분석 — _aligned_dataset.jsonl → learning_YYYYMMDD.md

입력: samples/11b/_aligned_dataset.jsonl
출력: reports/learning_YYYYMMDD.md

분류 항목:
  A. LaTeX → HWP 수식 변환 패턴
  B. OCR 오인식 패턴
  C. 공백/포맷팅 규칙
  D. 보기 정규화 규칙
  E. 이미지 대체 패턴

LLM 비용 한도: $10
  claude-opus-4-7: 입력 $5/1M, 출력 $25/1M
  예상: 분석당 ~10K 입력 + 2K 출력 → 분석 100회 가능
"""
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

import anthropic

ROOT      = Path(__file__).resolve().parent.parent.parent
JSONL     = ROOT / "samples" / "11b" / "_aligned_dataset.jsonl"
REPORT_DIR = ROOT / "reports"

# Claude 비용 추적 (입력 $5/1M, 출력 $25/1M)
_INPUT_COST_PER_TOKEN  = 5.0  / 1_000_000
_OUTPUT_COST_PER_TOKEN = 25.0 / 1_000_000
_COST_CAP = 10.0

_accumulated_cost = 0.0
_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _call_claude(prompt: str, system: str = "") -> tuple[str, float]:
    """Claude 호출. 비용 한도 초과 시 예외 발생. Returns (text, cost)."""
    global _accumulated_cost
    if _accumulated_cost >= _COST_CAP:
        raise RuntimeError(f"LLM 비용 한도 도달 (${_accumulated_cost:.2f})")

    client = _get_client()
    msgs = [{"role": "user", "content": prompt}]
    kwargs: dict = dict(
        model="claude-opus-4-7",
        max_tokens=4096,
        messages=msgs,
    )
    if system:
        kwargs["system"] = system

    resp = client.messages.create(**kwargs)
    in_tok  = resp.usage.input_tokens
    out_tok = resp.usage.output_tokens
    cost = in_tok * _INPUT_COST_PER_TOKEN + out_tok * _OUTPUT_COST_PER_TOKEN
    _accumulated_cost += cost
    print(f"    Claude: {in_tok}in+{out_tok}out = ${cost:.4f} (누적 ${_accumulated_cost:.4f})")

    text = next(
        (b.text for b in resp.content if hasattr(b, "text")), ""
    )
    return text, cost


# ── 데이터 로드 ────────────────────────────────────────────────

def load_records() -> list[dict]:
    if not JSONL.exists():
        raise FileNotFoundError(f"JSONL 없음: {JSONL}")
    records = []
    with open(JSONL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


# ── 사전 클러스터링 (LLM 없이) ──────────────────────────────────

def _cluster_records(records: list[dict]) -> dict[str, list[dict]]:
    """eq_diff, img_count, align_note 기준으로 사전 분류."""
    clusters: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        eq_diff = r.get("eq_diff", 0)
        img = r.get("gold_img_count", 0)
        note = r.get("align_note", "choice")

        if "essay" in note or "서술형" in note:
            clusters["D_essay"].append(r)
        elif img > 0 and eq_diff < -2:
            clusters["C_img_replace"].append(r)   # raw에 수식, gold에 이미지
        elif eq_diff > 8:
            clusters["A_gold_more"].append(r)      # gold 수식이 훨씬 많음
        elif eq_diff < -4:
            clusters["B_raw_more"].append(r)       # raw 수식이 훨씬 많음
        elif eq_diff == 0:
            clusters["E_exact"].append(r)          # 수식 수 일치
        else:
            clusters["F_minor_diff"].append(r)     # 소폭 차이
    return clusters


def _sample(lst: list, n: int = 8) -> list:
    """균등 샘플링."""
    if len(lst) <= n:
        return lst
    step = len(lst) / n
    return [lst[int(i * step)] for i in range(n)]


# ── LLM 분석 섹션 ─────────────────────────────────────────────

SYSTEM_PROMPT = """너는 한국 수학 시험지 자동화 파이프라인의 품질 분석가이다.
입력: PDF OCR 결과(raw LaTeX)와 사람이 작성한 HWP 정답(gold HWP 스크립트) 쌍들.
목표: raw→gold 변환 패턴을 찾아 규칙화한다.

출력 형식: 마크다운, 명확한 패턴 목록, 구체 예시 포함.
한국어로 작성.
"""

def _fmt_pairs(records: list[dict], max_n: int = 8) -> str:
    """레코드를 분석용 텍스트로 포맷."""
    sampled = _sample(records, max_n)
    lines = []
    for r in sampled:
        src = r.get("source", "?")
        num = r.get("num", "?")
        raw_eqs = r.get("raw_inline_eqs", [])[:5]
        gold_eqs = r.get("gold_eqs", [])[:5]
        lines.append(
            f"[{src} #{num}번]\n"
            f"  raw LaTeX:  {raw_eqs}\n"
            f"  gold HWP:   {gold_eqs}\n"
        )
    return "\n".join(lines)


def analyze_latex_to_hwp(records: list[dict]) -> str:
    """A. LaTeX → HWP 수식 변환 패턴."""
    if not records:
        return "_데이터 없음_"
    sample_text = _fmt_pairs(records)
    prompt = f"""다음은 LaTeX 수식(raw)과 이에 대응하는 HWP 수식 스크립트(gold) 쌍들이다.

{sample_text}

분석 요청:
1. LaTeX 구문이 HWP 스크립트로 어떻게 변환되는지 패턴을 추출하라.
2. 자주 등장하는 변환 규칙을 표로 정리하라 (LaTeX → HWP).
3. 변환이 복잡하거나 불규칙한 사례를 별도로 표시하라.
4. 이 패턴들을 자동화 코드에 반영하기 위한 우선순위 권고를 제시하라.
"""
    text, _ = _call_claude(prompt, SYSTEM_PROMPT)
    return text


def analyze_gold_more(records: list[dict]) -> str:
    """gold 수식이 raw보다 훨씬 많은 경우 분석."""
    if not records:
        return "_데이터 없음_"
    sample_text = _fmt_pairs(records)
    prompt = f"""다음은 gold HWP 수식이 raw LaTeX보다 현저히 많은 문제 쌍들이다.

{sample_text}

분석 요청:
1. gold에서 수식이 더 많은 이유를 파악하라 (예: 표 숫자 각각이 수식, 보기 내 수식 분할 등).
2. raw OCR이 놓치고 있는 유형을 카테고리별로 정리하라.
3. 이를 보완하기 위한 파싱 개선 방향을 제안하라.
"""
    text, _ = _call_claude(prompt, SYSTEM_PROMPT)
    return text


def analyze_raw_more(records: list[dict]) -> str:
    """raw 수식이 gold보다 많은 경우 분석."""
    if not records:
        return "_데이터 없음_"
    sample_text = _fmt_pairs(records)
    prompt = f"""다음은 raw LaTeX 수식이 gold HWP보다 현저히 많은 문제 쌍들이다.

{sample_text}

분석 요청:
1. raw에서 수식으로 인식됐으나 gold에서는 단순 텍스트/이미지로 대체된 이유를 파악하라.
2. OCR이 과잉 수식화하는 패턴을 목록으로 정리하라.
3. 향후 파이프라인에서 필터링하거나 정규화해야 할 패턴을 제안하라.
"""
    text, _ = _call_claude(prompt, SYSTEM_PROMPT)
    return text


def analyze_img_replace(records: list[dict]) -> str:
    """이미지 대체 패턴 분석."""
    if not records:
        return "_데이터 없음_"
    sample_text = _fmt_pairs(records)
    prompt = f"""다음은 raw에는 LaTeX 수식이 있지만 gold에서는 이미지(그림)로 대체된 문제 쌍들이다.

{sample_text}

분석 요청:
1. 어떤 수식/내용이 이미지로 대체되는지 패턴을 파악하라.
2. 이미지 대체가 필요한 경우와 수식으로 처리 가능한 경우를 구분하라.
3. 파이프라인 개선을 위한 실용적 권고를 제시하라.
"""
    text, _ = _call_claude(prompt, SYSTEM_PROMPT)
    return text


def analyze_essay(records: list[dict]) -> str:
    """서술형 문제 패턴 분석."""
    if not records:
        return "_데이터 없음_"
    sample_text = _fmt_pairs(records, max_n=4)
    prompt = f"""다음은 서술형 문제의 raw-gold 쌍들이다.

{sample_text}

분석 요청:
1. 서술형 문제에서 raw OCR vs gold 작성 방식의 주요 차이를 정리하라.
2. 서술형 문제 자동 처리 시 주의해야 할 사항을 제시하라.
"""
    text, _ = _call_claude(prompt, SYSTEM_PROMPT)
    return text


def generate_summary(all_sections: dict[str, str], total_records: int) -> str:
    """전체 종합 요약 생성."""
    section_summaries = "\n\n".join(
        f"## {k}\n{v[:500]}" for k, v in all_sections.items() if v != "_데이터 없음_"
    )
    prompt = f"""다음은 수학 시험지 자동화 파이프라인의 raw-gold 정합 데이터 분석 결과다.
총 {total_records}개 레코드 분석.

{section_summaries}

요청:
1. 파이프라인 개선을 위한 최우선 과제 5개를 순위별로 제시하라.
2. 각 과제의 예상 개선 효과를 간략히 기술하라.
3. 즉시 적용 가능한 간단한 규칙 3개를 코드 형태로 예시하라.

학원장이 아침에 읽을 수 있는 1페이지 분량으로 작성하라.
"""
    text, _ = _call_claude(prompt, SYSTEM_PROMPT)
    return text


# ── 메인 ─────────────────────────────────────────────────────────

def main() -> None:
    print("─" * 62)
    print("  패턴 분석 시작")
    print("─" * 62)

    records = load_records()
    print(f"  레코드: {len(records)}개")
    if not records:
        print("  레코드 없음 → 종료")
        return

    # 소스별 통계
    sources = sorted(set(r.get("source", "") for r in records))
    print(f"  소스: {len(sources)}개 학교")

    clusters = _cluster_records(records)
    print(f"  클러스터: {', '.join(f'{k}={len(v)}' for k,v in clusters.items())}")

    # ── LLM 분석 ────────────────────────────────────────────────
    sections: dict[str, str] = {}
    analysis_map = [
        ("A. LaTeX→HWP 변환 패턴",
         analyze_latex_to_hwp,
         clusters.get("A_gold_more", []) + clusters.get("F_minor_diff", [])),
        ("B. gold 수식 >> raw (OCR 누락)",
         analyze_gold_more,
         clusters.get("A_gold_more", [])),
        ("C. raw 수식 >> gold (OCR 과잉)",
         analyze_raw_more,
         clusters.get("B_raw_more", [])),
        ("D. 이미지 대체 패턴",
         analyze_img_replace,
         clusters.get("C_img_replace", [])),
        ("E. 서술형 처리 패턴",
         analyze_essay,
         clusters.get("D_essay", [])),
    ]

    for title, func, data in analysis_map:
        print(f"\n  분석 중: {title} ({len(data)}건)...")
        if _accumulated_cost >= _COST_CAP:
            print(f"  비용 한도 도달 (${_accumulated_cost:.2f}) → 이후 분석 생략")
            sections[title] = "_비용 한도로 생략_"
            continue
        try:
            sections[title] = func(data)
        except RuntimeError as e:
            sections[title] = f"_{e}_"
        except Exception as e:
            sections[title] = f"_오류: {e}_"

    # 종합 요약
    print(f"\n  종합 요약 생성 중...")
    if _accumulated_cost < _COST_CAP:
        try:
            summary = generate_summary(sections, len(records))
        except Exception as e:
            summary = f"_오류: {e}_"
    else:
        summary = "_비용 한도로 생략_"

    # ── 보고서 작성 ──────────────────────────────────────────────
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    report_path = REPORT_DIR / f"learning_{today}.md"

    stats_block = "\n".join([
        f"- 총 레코드: {len(records)}개",
        f"- 소스 학교: {len(sources)}개 ({', '.join(sources[:5])}{'...' if len(sources)>5 else ''})",
        f"- 클러스터 분포: {', '.join(f'{k}={len(v)}' for k,v in clusters.items())}",
        f"- Claude 비용: ${_accumulated_cost:.4f}",
        f"- 생성 일시: {datetime.now():%Y-%m-%d %H:%M:%S}",
    ])

    report_lines = [
        f"# 학습 데이터 패턴 분석 보고서 — {datetime.now():%Y-%m-%d}",
        "",
        "## 데이터 통계",
        stats_block,
        "",
        "## 종합 권고",
        summary,
        "",
    ]
    for title, content in sections.items():
        report_lines += [f"## {title}", content, ""]

    report_path.write_text("\n".join(report_lines), encoding="utf-8")

    print()
    print("─" * 62)
    print(f"  총 레코드: {len(records)}개")
    print(f"  Claude 비용: ${_accumulated_cost:.4f}")
    print(f"  보고서: {report_path}")
    print("─" * 62)


if __name__ == "__main__":
    main()
