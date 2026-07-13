"""API 키(provider)별 일일 비용 한도 — 관리자 UI 에서 조정한다.

저장 파일: DATA_DIR/api_settings.json
{
  "caps": { "claude": 5.0, "gemini": 3.0 }
}

- env 기본값(시드): DAILY_COST_CAP(=claude), GEMINI_DAILY_CAP(=gemini).
- 파일에 값이 있으면 파일이 우선(관리자가 UI 에서 저장하면 파일에 기록).
- 캡 0 = 무제한(사용자별 cap_usd 규칙과 동일).

키 자체의 청구를 지키는 '천장'이라 사용자 등급과 무관하게 적용된다
(users.cap_usd 는 직원 개인 한도, 여기 caps 는 API 키 전체 한도).
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

# 지원 provider — 사용량 로그의 provider 필드·엔진 과금 경로와 일치해야 한다.
PROVIDERS: tuple[str, ...] = ("claude", "gemini")

PROVIDER_LABEL: dict[str, str] = {"claude": "Claude", "gemini": "Gemini"}


def _data_dir() -> Path:
    # 경로 단일 출처(store.DATA_DIR) — 볼륨마운트>DATA_DIR>scripts/web/data.
    from scripts.web.store import DATA_DIR
    return DATA_DIR


_SETTINGS_FILE = _data_dir() / "api_settings.json"


def _env_default_caps() -> dict[str, float]:
    """파일이 없을 때 쓰는 env 기본 한도(직원 계정에 적용, 관리자는 면제).

    기본값 Claude $20 / Gemini $10 (2026-07-13 학원장 결정). 레거시 DAILY_COST_CAP
    env 가 설정돼 있으면 claude 기본으로 존중한다(하위호환)."""
    def _f(names: tuple[str, ...], default: str) -> float:
        for n in names:
            v = os.environ.get(n)
            if v not in (None, ""):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return float(default)
    return {
        "claude": _f(("CLAUDE_DAILY_CAP", "DAILY_COST_CAP"), "20.0"),
        "gemini": _f(("GEMINI_DAILY_CAP",), "10.0"),
    }


def _load() -> dict:
    if not _SETTINGS_FILE.exists():
        return {}
    try:
        return json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SETTINGS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_caps() -> dict[str, float]:
    """provider -> 일일 한도(USD). 저장값 우선, 없으면 env 기본."""
    caps = _env_default_caps()
    saved = _load().get("caps", {})
    for p in PROVIDERS:
        if p in saved:
            try:
                caps[p] = float(saved[p])
            except (TypeError, ValueError):
                pass
    return caps


def get_cap(provider: str) -> float:
    return get_caps().get(provider, 0.0)


def set_cap(provider: str, cap_usd: float) -> None:
    """provider 한도 저장. 알 수 없는 provider 는 ValueError."""
    if provider not in PROVIDERS:
        raise ValueError(f"알 수 없는 provider: {provider}")
    cap = float(cap_usd)
    # NaN/Infinity 거부 — inf 가 저장되면 spend>=inf 가 항상 False 라 캡이 영구
    # 무력화된다(비용 천장 기능의 신뢰성 방어). UI 는 이미 막지만 API 직접 호출 대비.
    if not math.isfinite(cap):
        raise ValueError("유효한 금액이 아닙니다.")
    if cap < 0:
        raise ValueError("한도는 0 이상이어야 합니다.")
    data = _load()
    data.setdefault("caps", {})[provider] = cap
    _save(data)
