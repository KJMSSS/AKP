"""
Google 이메일 기반 사용자 관리.

users.json 형식:
{
  "teacher@gmail.com": {
    "name": "김선생",
    "cap_usd": 2.0,
    "active": true,
    "added": "2026-06-03"
  }
}

관리자는 ADMIN_EMAIL 환경변수로 지정. users.json에 없어도 관리자 접근 가능.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from scripts.web.usage_log import read_entries

ADMIN_EMAIL: str = os.environ.get("ADMIN_EMAIL", "")


def _data_dir() -> Path:
    d = os.environ.get("DATA_DIR", "")
    return Path(d) if d else Path(__file__).resolve().parent


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


def add_user(email: str, name: str, cap_usd: float = 2.0) -> None:
    data = _load()
    data[email] = {
        "name": name,
        "cap_usd": cap_usd,
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


def list_users() -> list[dict]:
    """모든 사용자 + 오늘 사용량."""
    data = _load()
    today = datetime.now().strftime("%Y-%m-%d")
    entries = read_entries(days=7)
    result = []

    # 관리자 계정 (users.json에 없어도 표시)
    if ADMIN_EMAIL and ADMIN_EMAIL not in data:
        today_cost = sum(
            e.get("cost_usd", 0.0) for e in entries
            if e.get("token") == ADMIN_EMAIL and e.get("ts", "").startswith(today)
        )
        result.append({
            "email": ADMIN_EMAIL, "name": "관리자", "cap_usd": 0,
            "active": True, "role": "admin",
            "today_cost": round(today_cost, 4),
            "total_cost": round(sum(
                e.get("cost_usd", 0.0) for e in entries
                if e.get("token") == ADMIN_EMAIL
            ), 4),
        })

    for email, info in data.items():
        today_cost = sum(
            e.get("cost_usd", 0.0) for e in entries
            if e.get("token") == email and e.get("ts", "").startswith(today)
        )
        total_cost = sum(
            e.get("cost_usd", 0.0) for e in entries
            if e.get("token") == email
        )
        result.append({
            "email":      email,
            "name":       info.get("name", email),
            "cap_usd":    info.get("cap_usd", 2.0),
            "active":     info.get("active", True),
            "role":       info.get("role", "user"),
            "added":      info.get("added", ""),
            "today_cost": round(today_cost, 4),
            "total_cost": round(total_cost, 4),
        })
    return result


def user_today_cost(email: str) -> float:
    today = datetime.now().strftime("%Y-%m-%d")
    return round(sum(
        e.get("cost_usd", 0.0)
        for e in read_entries(days=1)
        if e.get("token") == email and e.get("ts", "").startswith(today)
    ), 4)
