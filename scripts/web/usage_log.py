"""
변환별 토큰·비용 로그 — JSON Lines 형식.

로그 파일: scripts/web/logs/usage.jsonl
각 줄: {"ts": "2026-06-03T14:32:11", "pdf": "...", "mode": "full",
         "in_tok": 21609, "out_tok": 9426, "cost_usd": 0.2048,
         "duration_s": 126.4, "status": "ok"}
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

def _data_dir() -> Path:
    """경로 단일 출처(store.DATA_DIR) — 볼륨마운트 > DATA_DIR > scripts/web/data."""
    from scripts.web.store import DATA_DIR
    return DATA_DIR

_LOG_DIR  = _data_dir()
_LOG_FILE = _LOG_DIR / "usage.jsonl"

DAILY_CAP_USD: float = float(os.environ.get("DAILY_COST_CAP", "5.0"))

# 지원 provider — usage 항목의 provider 필드·직원별 한도 필드와 일치해야 한다.
PROVIDERS: tuple[str, ...] = ("claude", "gemini")
PROVIDER_LABEL: dict[str, str] = {"claude": "Claude", "gemini": "Gemini"}


def append_entry(entry: dict) -> None:
    """변환 1건을 로그 파일에 추가."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    with _LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_entries(days: int = 7) -> list[dict]:
    """최근 N일 항목을 최신순으로 반환."""
    if not _LOG_FILE.exists():
        return []
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    entries: list[dict] = []
    for line in _LOG_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
            if e.get("ts", "") >= cutoff:
                entries.append(e)
        except json.JSONDecodeError:
            pass
    return list(reversed(entries))


def _today_entries() -> list[dict]:
    today = datetime.now().strftime("%Y-%m-%d")
    return [e for e in read_entries(days=1) if e.get("ts", "").startswith(today)]


def provider_today_cost() -> dict[str, float]:
    """provider -> 오늘 비용 합계(USD).

    provider 필드가 없는 옛 항목은 'claude' 로 귀속한다(대전환 이전 로그는
    전부 Claude 과금이었다). Gemini 재작도 항목은 provider='gemini' 로 기록된다.
    """
    out: dict[str, float] = {"claude": 0.0, "gemini": 0.0}
    for e in _today_entries():
        prov = e.get("provider", "claude")
        # 비용은 상태 무관 합산 — 재작도 '반려(rejected)'·'오류(error)'도 실제 청구가
        # 발생한 경로다(2026-07-06 QA: ok 만 합산하면 반려 연발 시 캡이 뚫린다).
        out[prov] = out.get(prov, 0.0) + e.get("cost_usd", 0.0)
    return {k: round(v, 4) for k, v in out.items()}


def user_provider_today_cost(email: str) -> dict[str, float]:
    """특정 사용자의 오늘 provider별 비용 합계(USD).

    직원별 Claude/Gemini 한도 검사·표시에 쓴다. 로그의 token 필드가 사용자
    이메일, provider 필드가 claude/gemini(옛 항목은 claude 귀속)다.
    """
    out: dict[str, float] = {"claude": 0.0, "gemini": 0.0}
    for e in _today_entries():
        if e.get("token") != email:
            continue
        prov = e.get("provider", "claude")
        out[prov] = out.get(prov, 0.0) + e.get("cost_usd", 0.0)
    return {k: round(v, 4) for k, v in out.items()}


def today_summary() -> dict:
    """오늘의 총비용·변환 횟수·토큰 합계 + provider 별 비용 분해."""
    today = datetime.now().strftime("%Y-%m-%d")
    total_cost = 0.0
    total_in = 0
    total_out = 0
    count = 0
    for e in _today_entries():
        total_cost += e.get("cost_usd", 0.0)
        total_in   += e.get("in_tok", 0)
        total_out  += e.get("out_tok", 0)
        count += 1
    return {
        "date": today,
        "cost_usd": round(total_cost, 4),
        "cap_usd": DAILY_CAP_USD,
        "remaining_usd": round(max(0.0, DAILY_CAP_USD - total_cost), 4),
        "conversions": count,
        "in_tok": total_in,
        "out_tok": total_out,
        "providers": provider_today_cost(),
    }
