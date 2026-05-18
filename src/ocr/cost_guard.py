"""
비용 cap 안전장치 — 일일 $5 한도 초과 시 API 호출 자동 차단.

사용:
    from src.ocr.cost_guard import CostGuard
    guard = CostGuard()
    guard.check_or_raise("clova")   # 한도 초과 시 CostCapError
    guard.record("clova", 0.003)    # 비용 기록
"""
from __future__ import annotations

import json
import time
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "log" / "cycle_15h"
_COST_FILE = _LOG_DIR / "cost_summary.json"
_DAILY_CAP = 5.0  # USD


class CostCapError(RuntimeError):
    pass


class CostGuard:
    def __init__(self, cap_usd: float = _DAILY_CAP) -> None:
        self.cap_usd = cap_usd
        self._today = time.strftime("%Y-%m-%d")

    def _load(self) -> dict:
        if not _COST_FILE.exists():
            return {}
        try:
            return json.loads(_COST_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, data: dict) -> None:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        _COST_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def total_today(self) -> float:
        data = self._load()
        day = data.get(self._today, {})
        return sum(day.values()) if isinstance(day, dict) else float(day)

    def check_or_raise(self, service: str = "") -> None:
        total = self.total_today()
        if total >= self.cap_usd:
            raise CostCapError(
                f"일일 비용 상한 ${self.cap_usd} 도달 (현재 ${total:.4f})"
                + (f" [{service}]" if service else "")
            )

    def record(self, service: str, cost_usd: float) -> float:
        data = self._load()
        day = data.setdefault(self._today, {})
        if not isinstance(day, dict):
            day = {}
            data[self._today] = day
        day[service] = round(day.get(service, 0.0) + cost_usd, 6)
        self._save(data)
        return self.total_today()

    def summary(self) -> dict:
        data = self._load()
        return data.get(self._today, {})
