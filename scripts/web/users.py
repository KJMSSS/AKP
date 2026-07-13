"""
Google 이메일 기반 사용자 관리.

users.json 형식:
{
  "teacher@gmail.com": {
    "name": "김선생",
    "cap_claude_usd": 5.0,
    "cap_gemini_usd": 3.0,
    "active": true,
    "added": "2026-06-03"
  }
}
(레거시 단일 필드 cap_usd 는 user_cap() 이 폴백으로 계속 인식한다.)

관리자는 ADMIN_EMAIL 환경변수로 지정. users.json에 없어도 관리자 접근 가능.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from scripts.web.usage_log import read_entries

ROLE_STAGES: dict[str, list[str]] = {
    "tier1": ["pdf", "hwpx_draft", "hwpx_review", "hangeul"],
    "tier2": ["pdf", "hwpx_draft", "hwpx_review", "hangeul"],
    "tier3": ["pdf", "hwpx_draft", "hwpx_review", "hangeul", "typer"],
    "tier4": ["pdf", "hwpx_draft", "hwpx_review", "hangeul", "typer", "solution"],
    "staff": ["pdf", "hwpx_draft", "hwpx_review", "hangeul", "typer", "solution"],
    "admin": ["pdf", "hwpx_draft", "hwpx_review", "hangeul", "typer", "solution"],
    "user":  ["pdf", "hwpx_draft", "hwpx_review", "hangeul"],
}

ROLE_DISPLAY: dict[str, str] = {
    "tier1": "기본", "tier2": "그림완성", "tier3": "타이퍼",
    "tier4": "해설", "staff": "직원", "admin": "관리자", "user": "기본",
}

SELECTABLE_ROLES = ["tier1", "tier2", "tier3", "tier4", "staff"]

ADMIN_EMAIL: str = os.environ.get("ADMIN_EMAIL", "")

# ── 직원별 provider 한도 ────────────────────────────────────────────────
# 각 직원이 하루에 Claude/Gemini 키에 쓸 수 있는 상한(USD, 0=무제한). 관리자는
# 전면 면제(is_admin). 글로벌 키 한도 대신 직원별로 두어, 한 명이 많이 써도 다른
# 직원은 안 막히게 한다(2026-07-13 학원장 결정 — 상단 글로벌 카드 폐기).
CAP_FIELD: dict[str, str] = {"claude": "cap_claude_usd", "gemini": "cap_gemini_usd"}
DEFAULT_CAP_CLAUDE: float = float(os.environ.get("STAFF_CAP_CLAUDE", "5.0"))
DEFAULT_CAP_GEMINI: float = float(os.environ.get("STAFF_CAP_GEMINI", "3.0"))


def user_cap(user: dict, provider: str) -> float:
    """직원 dict 에서 provider별 하루 한도(USD). 0=무제한.

    신 필드(cap_claude_usd/cap_gemini_usd)가 있으면 그 값을, 없으면 레거시
    단일 필드 cap_usd 로 폴백한다(대전환 이전 단일 한도 사용자의 한도가 조용히
    풀리지 않게 두 provider 모두에 동일 적용 — 관리자가 개별 조정할 때까지의 안전값).
    """
    field = CAP_FIELD.get(provider)
    raw = user.get(field) if field else None
    if raw is None:
        raw = user.get("cap_usd", 0)
    try:
        return float(raw or 0)
    except (TypeError, ValueError):
        return 0.0


def _data_dir() -> Path:
    # 경로 단일 출처(store.DATA_DIR) — 볼륨마운트>DATA_DIR>scripts/web/data.
    # 종전엔 DATA_DIR env 만 봐서 Railway 볼륨 마운트 시 휘발 디스크로 샜다(2026-07-06 감사).
    from scripts.web.store import DATA_DIR
    return DATA_DIR


_USERS_FILE = _data_dir() / "users.json"


def _load() -> dict:
    if not _USERS_FILE.exists():
        return {}
    try:
        return json.loads(_USERS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    _USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _USERS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def is_admin(email: str) -> bool:
    if not email:
        return False
    if ADMIN_EMAIL and email.lower() == ADMIN_EMAIL.lower():
        return True
    data = _load()
    return data.get(email, {}).get("role") == "admin"


def is_allowed(email: str) -> bool:
    """접근 가능한 사용자인지 확인."""
    if not email:
        return False
    if is_admin(email):
        return True
    data = _load()
    user = data.get(email)
    return bool(user and user.get("active", True))


def get_user(email: str) -> dict | None:
    data = _load()
    return data.get(email)


def add_user(
    email: str,
    name: str,
    cap_claude_usd: float = DEFAULT_CAP_CLAUDE,
    cap_gemini_usd: float = DEFAULT_CAP_GEMINI,
    role: str = "tier1",
) -> None:
    data = _load()
    data[email] = {
        "name": name,
        "cap_claude_usd": cap_claude_usd,
        "cap_gemini_usd": cap_gemini_usd,
        "role": role,
        "active": True,
        "added": datetime.now().strftime("%Y-%m-%d"),
    }
    _save(data)


def update_user(email: str, **kwargs) -> bool:
    data = _load()
    if email not in data:
        return False
    data[email].update(kwargs)
    _save(data)
    return True


def remove_user(email: str) -> bool:
    data = _load()
    if email not in data:
        return False
    del data[email]
    _save(data)
    return True


def _today_provider(entries: list[dict], email: str, today: str) -> dict[str, float]:
    """entries 중 해당 사용자의 오늘 provider별 비용 합계."""
    out = {"claude": 0.0, "gemini": 0.0}
    for e in entries:
        if e.get("token") == email and e.get("ts", "").startswith(today):
            p = e.get("provider", "claude")
            out[p] = out.get(p, 0.0) + e.get("cost_usd", 0.0)
    return out


def list_users() -> list[dict]:
    """모든 사용자 + 오늘 사용량(provider별 포함)."""
    data = _load()
    today = datetime.now().strftime("%Y-%m-%d")
    entries = read_entries(days=7)
    result = []

    # 관리자 계정 (users.json에 없어도 표시) — 한도 면제이므로 캡 0.
    if ADMIN_EMAIL and ADMIN_EMAIL not in data:
        tp = _today_provider(entries, ADMIN_EMAIL, today)
        result.append({
            "email": ADMIN_EMAIL, "name": "관리자",
            "cap_claude_usd": 0, "cap_gemini_usd": 0,
            "active": True, "role": "admin",
            "today_cost": round(tp["claude"] + tp["gemini"], 4),
            "today_claude": round(tp["claude"], 4),
            "today_gemini": round(tp["gemini"], 4),
            "total_cost": round(sum(
                e.get("cost_usd", 0.0) for e in entries
                if e.get("token") == ADMIN_EMAIL
            ), 4),
        })

    for email, info in data.items():
        tp = _today_provider(entries, email, today)
        total_cost = sum(
            e.get("cost_usd", 0.0) for e in entries
            if e.get("token") == email
        )
        result.append({
            "email":          email,
            "name":           info.get("name", email),
            "cap_claude_usd": user_cap(info, "claude"),
            "cap_gemini_usd": user_cap(info, "gemini"),
            "active":         info.get("active", True),
            "role":           info.get("role", "user"),
            "added":          info.get("added", ""),
            "today_cost":     round(tp["claude"] + tp["gemini"], 4),
            "today_claude":   round(tp["claude"], 4),
            "today_gemini":   round(tp["gemini"], 4),
            "total_cost":     round(total_cost, 4),
        })
    return result


def get_role(email: str) -> str:
    if is_admin(email):
        return "admin"
    data = _load()
    return data.get(email, {}).get("role", "tier1")


def get_allowed_stages(email: str) -> list[str]:
    role = get_role(email)
    return list(ROLE_STAGES.get(role, ROLE_STAGES["tier1"]))
