"""
토큰 관리 모듈.

tokens.json 형식:
{
  "선생님A": {
    "token": "akp-abc123",
    "cap_usd": 2.0,
    "active": true,
    "created": "2026-06-03"
  }
}

환경변수:
  ADMIN_PASSWORD  — 관리자 페이지 비밀번호 (기본: "akp-admin")
"""
from __future__ import annotations

import json
import os
import secrets
import string
from datetime import datetime
from pathlib import Path

from scripts.web.usage_log import read_entries

def _data_dir() -> Path:
    d = os.environ.get("DATA_DIR", "")
    return Path(d) if d else Path(__file__).resolve().parent

_TOKEN_FILE = _data_dir() / "tokens.json"
ADMIN_PASSWORD: str = os.environ.get("ADMIN_PASSWORD", "akp-admin")


# ── 파일 I/O ──────────────────────────────────────────────────────────

def _load() -> dict:
    if not _TOKEN_FILE.exists():
        return {}
    try:
        return json.loads(_TOKEN_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    _TOKEN_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── 공개 API ──────────────────────────────────────────────────────────

def generate_token() -> str:
    """akp- 접두사 + 8자리 영숫자 토큰 생성."""
    chars = string.ascii_lowercase + string.digits
    return "akp-" + "".join(secrets.choice(chars) for _ in range(8))


def create_token(name: str, cap_usd: float = 2.0) -> str:
    """새 토큰 발급. 이미 존재하면 기존 토큰 반환."""
    data = _load()
    if name in data:
        return data[name]["token"]
    token = generate_token()
    data[name] = {
        "token": token,
        "cap_usd": cap_usd,       # 0 = 무제한
        "active": True,
        "created": datetime.now().strftime("%Y-%m-%d"),
    }
    _save(data)
    return token


def revoke_token(name: str) -> bool:
    """토큰 비활성화. 없으면 False 반환."""
    data = _load()
    if name not in data:
        return False
    data[name]["active"] = False
    _save(data)
    return True


def activate_token(name: str) -> bool:
    data = _load()
    if name not in data:
        return False
    data[name]["active"] = True
    _save(data)
    return True


def delete_token(name: str) -> bool:
    data = _load()
    if name not in data:
        return False
    del data[name]
    _save(data)
    return True


def update_cap(name: str, cap_usd: float) -> bool:
    data = _load()
    if name not in data:
        return False
    data[name]["cap_usd"] = cap_usd
    _save(data)
    return True


def validate(token: str) -> tuple[str | None, dict | None]:
    """
    토큰 검증.
    반환: (name, info) 또는 (None, None)
    오류 문자열이 필요하면 info가 None일 때 별도 체크.
    """
    data = _load()
    for name, info in data.items():
        if info["token"] == token:
            if not info.get("active", True):
                return None, None  # 비활성
            return name, info
    return None, None


def token_today_cost(name: str) -> float:
    """해당 토큰 오늘 누적 비용."""
    today = datetime.now().strftime("%Y-%m-%d")
    total = 0.0
    for e in read_entries(days=1):
        if e.get("token") == name and e.get("ts", "").startswith(today):
            total += e.get("cost_usd", 0.0)
    return round(total, 4)


def all_token_stats() -> list[dict]:
    """모든 토큰의 정보 + 오늘 사용량."""
    data = _load()
    result = []
    today = datetime.now().strftime("%Y-%m-%d")
    # 오늘 로그를 한 번만 읽어 캐싱
    entries = read_entries(days=7)
    for name, info in data.items():
        today_cost = sum(
            e.get("cost_usd", 0.0)
            for e in entries
            if e.get("token") == name and e.get("ts", "").startswith(today)
        )
        total_cost = sum(
            e.get("cost_usd", 0.0)
            for e in entries
            if e.get("token") == name
        )
        result.append({
            "name":       name,
            "token":      info["token"],
            "cap_usd":    info["cap_usd"],
            "active":     info.get("active", True),
            "created":    info.get("created", ""),
            "today_cost": round(today_cost, 4),
            "total_cost": round(total_cost, 4),
        })
    return result
