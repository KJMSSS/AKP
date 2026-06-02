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
    """DATA_DIR 환경변수가 있으면 그 경로, 없으면 scripts/web/logs/"""
    d = os.environ.get("DATA_DIR", "")
    return Path(d) if d else Path(__file__).resolve().parent / "logs"

_LOG_DIR  = _data_dir()
_LOG_FILE = _LOG_DIR / "usage.jsonl"

DAILY_CAP_USD: float = float(os.environ.get("DAILY_COST_CAP", "5.0"))


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


def today_summary() -> dict:
    """오늘의 총비용·변환 횟수·토큰 합계."""
    today = datetime.now().strftime("%Y-%m-%d")
    total_cost = 0.0
    total_in = 0
    total_out = 0
    count = 0
    for e in read_entries(days=1):
        if not e.get("ts", "").startswith(today):
            continue
        if e.get("status") == "ok":
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
    }
