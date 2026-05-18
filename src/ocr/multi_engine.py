"""
3-way Cross-check — Mathpix × 클로바 OCR × LLM 한글 검증.

전략:
  1. Mathpix MD에서 문단 단위 한글 텍스트 추출
  2. 같은 영역을 클로바 OCR로 재확인
  3. 불일치 시 LLM으로 교정 제안
  4. 합의 실패 → ★ 플레이스홀더 삽입

한글 영역만 비교 — 수식, 숫자, LaTeX는 절대 변경 금지 (Stage C)

로깅: log/cycle_15h/crosscheck/
"""
from __future__ import annotations

import difflib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from src.ocr.clova_ocr import ClovaResult

_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "log" / "cycle_15h" / "crosscheck"

# 한글 문단: 한글 2자 이상 포함하고 수식만 아닌 줄
_KOR_LINE_RE = re.compile(r"[가-힣]{2,}")
# 수식 전용 줄 제외
_FORMULA_ONLY_RE = re.compile(r"^\s*\$[\s\S]+\$\s*$")


@dataclass
class CrossCheckResult:
    paragraph: str            # Mathpix 원본 단락
    clova_text: str           # 클로바가 같은 영역에서 읽은 텍스트
    llm_suggestion: str       # LLM 교정 제안 (없으면 "")
    agreed_text: str          # 최종 합의 텍스트
    status: str               # "agree" | "clova_fix" | "llm_fix" | "placeholder"
    similarity: float = 0.0


@dataclass
class CrossCheckReport:
    results: list[CrossCheckResult] = field(default_factory=list)
    placeholder_count: int = 0
    fix_count: int = 0

    def to_dict(self) -> dict:
        return {
            "placeholder_count": self.placeholder_count,
            "fix_count": self.fix_count,
            "results": [
                {
                    "status": r.status,
                    "similarity": round(r.similarity, 3),
                    "paragraph": r.paragraph[:80],
                    "agreed": r.agreed_text[:80],
                }
                for r in self.results
                if r.status != "agree"
            ],
        }


def _extract_korean_paragraphs(md: str) -> list[str]:
    """Mathpix MD에서 한글이 포함된 단락 추출."""
    paragraphs: list[str] = []
    for line in md.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if _FORMULA_ONLY_RE.match(stripped):
            continue
        if _KOR_LINE_RE.search(stripped):
            paragraphs.append(stripped)
    return paragraphs


def _similarity(a: str, b: str) -> float:
    """두 텍스트의 한글 토큰 유사도 (0~1)."""
    a_kor = " ".join(re.findall(r"[가-힣]+", a))
    b_kor = " ".join(re.findall(r"[가-힣]+", b))
    if not a_kor and not b_kor:
        return 1.0
    if not a_kor or not b_kor:
        return 0.0
    return difflib.SequenceMatcher(None, a_kor, b_kor).ratio()


def _nearest_clova_text(paragraph: str, clova: ClovaResult, threshold: float = 0.5) -> str:
    """클로바 결과에서 paragraph와 가장 유사한 한글 텍스트 찾기."""
    best_sim = 0.0
    best_text = ""
    for cf in clova.korean_fields():
        sim = _similarity(paragraph, cf.text)
        if sim > best_sim:
            best_sim = sim
            best_text = cf.text
    if best_sim < threshold:
        return ""
    return best_text


def check_paragraph(
    paragraph: str,
    clova: ClovaResult,
    llm_suggestion: str = "",
    agree_threshold: float = 0.8,
) -> CrossCheckResult:
    """
    단락 하나에 대해 3-way 검증.

    clova_text를 찾지 못하거나 유사도가 낮으면 placeholder 처리.
    """
    clova_text = _nearest_clova_text(paragraph, clova)
    sim = _similarity(paragraph, clova_text) if clova_text else 0.0

    if not clova_text:
        # 클로바가 이 영역을 못 읽음
        return CrossCheckResult(
            paragraph=paragraph,
            clova_text="",
            llm_suggestion=llm_suggestion,
            agreed_text=llm_suggestion if llm_suggestion else paragraph,
            status="placeholder" if not llm_suggestion else "llm_fix",
            similarity=0.0,
        )

    if sim >= agree_threshold:
        return CrossCheckResult(
            paragraph=paragraph,
            clova_text=clova_text,
            llm_suggestion=llm_suggestion,
            agreed_text=paragraph,
            status="agree",
            similarity=sim,
        )

    # 불일치 — LLM 제안이 있으면 우선 채택, 없으면 placeholder
    if llm_suggestion:
        llm_sim = _similarity(paragraph, llm_suggestion)
        clova_sim = _similarity(clova_text, llm_suggestion)
        # LLM과 클로바가 어느 쪽과 더 비슷한지
        if clova_sim >= 0.7:
            agreed = llm_suggestion
            status = "llm_fix"
        else:
            agreed = f"【★ 3-way 불일치 — Mathpix: {paragraph[:30]} / 클로바: {clova_text[:30]}】"
            status = "placeholder"
    else:
        agreed = f"【★ 3-way 불일치 — Mathpix: {paragraph[:30]} / 클로바: {clova_text[:30]}】"
        status = "placeholder"

    return CrossCheckResult(
        paragraph=paragraph,
        clova_text=clova_text,
        llm_suggestion=llm_suggestion,
        agreed_text=agreed,
        status=status,
        similarity=sim,
    )


def crosscheck_md(
    md: str,
    clova: ClovaResult,
    llm_corrected_md: str = "",
    log_stem: str = "",
) -> tuple[str, CrossCheckReport]:
    """
    Mathpix MD 전체를 cross-check.

    반환: (수정된_md, CrossCheckReport)
    """
    paragraphs = _extract_korean_paragraphs(md)
    report = CrossCheckReport()

    # LLM 교정 MD에서 같은 줄 인덱스의 수정 내용 파악 (단순 라인 매핑)
    llm_lines: dict[str, str] = {}
    if llm_corrected_md:
        for orig_line, corr_line in zip(md.split("\n"), llm_corrected_md.split("\n")):
            if orig_line.strip() != corr_line.strip():
                llm_lines[orig_line.strip()] = corr_line.strip()

    result_md_lines = md.split("\n")
    for i, line in enumerate(result_md_lines):
        stripped = line.strip()
        if stripped not in [p for p in paragraphs]:
            continue

        llm_sug = llm_lines.get(stripped, "")
        cr = check_paragraph(stripped, clova, llm_suggestion=llm_sug)
        report.results.append(cr)

        if cr.status == "placeholder":
            report.placeholder_count += 1
            result_md_lines[i] = cr.agreed_text
        elif cr.status in ("clova_fix", "llm_fix"):
            report.fix_count += 1
            result_md_lines[i] = cr.agreed_text

    if log_stem:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = _LOG_DIR / f"{log_stem}_{int(time.time())}.json"
        log_path.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return "\n".join(result_md_lines), report
