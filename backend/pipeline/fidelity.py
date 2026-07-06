"""충실도 QA 레이어 — 원본 vs 재현 결과 VLM 채점의 임계/판정.

채점 자체는 vision_claude.verify_redraw(그림)/verify_table(표)이 담당한다.
convert 파이프라인이 이 임계로 '구조화 채택 vs 이미지 폴백'을 결정하고,
저신뢰 항목을 flagged 표시해 검수 UI 가 우선 노출하도록 한다(충실도 우선 정책).
"""
from __future__ import annotations

FIGURE_MIN_SCORE = 80
TABLE_MIN_SCORE = 85


def passes(verdict: dict | None, min_score: int) -> bool:
    """verdict(={score, ok, ...})가 임계를 통과하는가."""
    return bool(verdict) and verdict.get("ok", False) and verdict.get("score", 0) >= min_score


def summarize(candidates: list[dict]) -> dict:
    """figure_candidates/table_candidates 의 fidelity 를 집계.

    반환: {total, scored, low}(저신뢰 항목 수). 검수 우선순위/리포트용.
    """
    scored = [c for c in candidates if isinstance(c.get("fidelity"), dict)]
    low = [c for c in scored if c.get("fidelity", {}).get("flagged")]
    return {"total": len(candidates), "scored": len(scored), "low": len(low)}
