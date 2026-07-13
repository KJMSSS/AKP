"""직원별 provider(Claude/Gemini) 한도 + Gemini 과금 회귀 테스트.

2026-07-13 재설계: 글로벌 키 한도(settings.py) 폐기 → 직원별 Claude/Gemini 한도.
- Gemini 이미지 재작도 과금(장당) 집계·드레인
- usage.jsonl provider 귀속(옛 항목=claude) + 사용자별 provider 비용
- user_cap: 신 필드(cap_claude_usd/cap_gemini_usd) + 레거시 cap_usd 폴백
- _check_cost_cap: 직원별 provider 한도 강제, 관리자 전면 면제
- 관리자 전용 엔드포인트 인증 가드 + 폐기된 글로벌 라우트 부재
"""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import HTTPException
from starlette.testclient import TestClient

import scripts.web.engine_api as ea
import scripts.web.usage_log as ul
import scripts.web.users as users
from scripts.web.app import app


def _client() -> TestClient:
    return TestClient(app)


# ── Gemini 과금 집계 ──────────────────────────────────────────────────
def test_gemini_drain_and_cost():
    import pipeline.redraw_gemini as rg
    from pipeline.redraw_gemini import DEFAULT_IMAGE_MODEL, PRO_IMAGE_MODEL

    rg._GEMINI_USAGE_LOG.clear()
    ea._gemini_consumed = 0
    rg._GEMINI_USAGE_LOG.append({"model": DEFAULT_IMAGE_MODEL, "images": 1})
    rg._GEMINI_USAGE_LOG.append({"model": DEFAULT_IMAGE_MODEL, "images": 1})
    rg._GEMINI_USAGE_LOG.append({"model": PRO_IMAGE_MODEL, "images": 1})

    g = ea._drain_gemini_usage()
    assert g["images"] == 3
    assert g["flash_images"] == 2
    assert g["pro_images"] == 1
    assert g["cost_usd"] == round(2 * ea._GEMINI_PRICE_FLASH + ea._GEMINI_PRICE_PRO, 4)
    # 드레인은 멱등 — 재호출 시 미집계분 없음
    assert ea._drain_gemini_usage()["images"] == 0


def test_log_gemini_usage_writes_provider(monkeypatch):
    import pipeline.redraw_gemini as rg
    from pipeline.redraw_gemini import DEFAULT_IMAGE_MODEL

    rg._GEMINI_USAGE_LOG.clear()
    ea._gemini_consumed = 0
    rg._GEMINI_USAGE_LOG.append({"model": DEFAULT_IMAGE_MODEL, "images": 1})

    captured: list[dict] = []
    monkeypatch.setattr(ea, "append_entry", lambda e: captured.append(e))
    cost = ea._log_gemini_usage("u@x.com", "test.pdf")
    assert cost > 0
    assert captured and captured[0]["provider"] == "gemini"
    assert captured[0]["mode"] == "redraw"
    assert captured[0]["images"] == 1
    # 생성 이미지가 없으면 로그 기록하지 않음
    captured.clear()
    assert ea._log_gemini_usage("u@x.com", "test.pdf") == 0.0
    assert not captured


# ── usage.jsonl provider 귀속 (전체 + 사용자별) ───────────────────────
def test_provider_today_cost_attribution(tmp_path, monkeypatch):
    monkeypatch.setattr(ul, "_LOG_FILE", tmp_path / "usage.jsonl")
    now = datetime.now().isoformat(timespec="seconds")
    ul.append_entry({"ts": now, "token": "a@x.com", "provider": "claude", "cost_usd": 1.0})
    ul.append_entry({"ts": now, "token": "a@x.com", "provider": "gemini", "cost_usd": 0.5})
    ul.append_entry({"ts": now, "token": "b@x.com", "provider": "claude", "cost_usd": 9.0})
    ul.append_entry({"ts": now, "token": "a@x.com", "cost_usd": 0.25})   # provider 없는 옛 항목 → claude

    # 전체 합계
    p = ul.provider_today_cost()
    assert p["claude"] == 10.25
    assert p["gemini"] == 0.5
    assert ul.today_summary()["providers"]["gemini"] == 0.5

    # 사용자별 — a 만 집계, b 는 제외
    ua = ul.user_provider_today_cost("a@x.com")
    assert ua["claude"] == 1.25
    assert ua["gemini"] == 0.5
    ub = ul.user_provider_today_cost("b@x.com")
    assert ub["claude"] == 9.0
    assert ub["gemini"] == 0.0


# ── user_cap: 신 필드 + 레거시 폴백 ──────────────────────────────────
def test_user_cap_resolution():
    u = {"cap_claude_usd": 5.0, "cap_gemini_usd": 3.0}
    assert users.user_cap(u, "claude") == 5.0
    assert users.user_cap(u, "gemini") == 3.0
    # 레거시 단일 cap_usd → 두 provider 모두에 적용(한도가 조용히 풀리지 않게)
    legacy = {"cap_usd": 2.0}
    assert users.user_cap(legacy, "claude") == 2.0
    assert users.user_cap(legacy, "gemini") == 2.0
    # 아무 필드도 없으면 0(무제한)
    assert users.user_cap({}, "claude") == 0.0


def test_add_user_stores_provider_caps(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "_USERS_FILE", tmp_path / "users.json")
    users.add_user("t@x.com", "김선생", cap_claude_usd=7.0, cap_gemini_usd=4.0, role="staff")
    u = users.get_user("t@x.com")
    assert u["cap_claude_usd"] == 7.0
    assert u["cap_gemini_usd"] == 4.0
    assert u["role"] == "staff"
    # provider 하나만 변경 — 다른 하나는 유지
    users.update_user("t@x.com", cap_gemini_usd=1.5)
    u2 = users.get_user("t@x.com")
    assert u2["cap_gemini_usd"] == 1.5
    assert u2["cap_claude_usd"] == 7.0


# ── _check_cost_cap: 직원별 provider 한도, 관리자 전면 면제 ────────────
def test_key_cap_blocks_staff_per_provider(monkeypatch):
    monkeypatch.setattr(ea, "is_admin", lambda e: False)
    monkeypatch.setattr(ea, "get_user", lambda e: {"cap_claude_usd": 5.0, "cap_gemini_usd": 3.0})
    # Claude 초과, Gemini 여유
    monkeypatch.setattr(ea, "user_provider_today_cost", lambda e: {"claude": 6.0, "gemini": 0.0})
    with pytest.raises(HTTPException) as exc:
        ea._check_cost_cap("staff@x.com", need=("claude",))
    assert exc.value.status_code == 429
    # gemini-only 검사는 통과
    ea._check_cost_cap("staff@x.com", need=("gemini",))


def test_admin_exempt_from_cap(monkeypatch):
    # 관리자는 한도 검사 자체를 건너뜀 — get_user/spend 를 보지 않는다
    monkeypatch.setattr(ea, "is_admin", lambda e: True)
    monkeypatch.setattr(ea, "get_user", lambda e: {"cap_claude_usd": 0.01, "cap_gemini_usd": 0.01})
    monkeypatch.setattr(ea, "user_provider_today_cost", lambda e: {"claude": 999.0, "gemini": 999.0})
    ea._check_cost_cap("admin@x.com", need=("claude", "gemini"))   # 예외 없음


def test_zero_cap_means_unlimited(monkeypatch):
    monkeypatch.setattr(ea, "is_admin", lambda e: False)
    monkeypatch.setattr(ea, "get_user", lambda e: {"cap_claude_usd": 0, "cap_gemini_usd": 0})
    monkeypatch.setattr(ea, "user_provider_today_cost", lambda e: {"claude": 999.0, "gemini": 999.0})
    ea._check_cost_cap("staff@x.com", need=("claude", "gemini"))   # 예외 없음


def test_legacy_cap_usd_still_enforced(monkeypatch):
    # 대전환 이전 단일 cap_usd 사용자도 계속 막혀야 한다(폴백)
    monkeypatch.setattr(ea, "is_admin", lambda e: False)
    monkeypatch.setattr(ea, "get_user", lambda e: {"cap_usd": 2.0})
    monkeypatch.setattr(ea, "user_provider_today_cost", lambda e: {"claude": 2.5, "gemini": 0.0})
    with pytest.raises(HTTPException) as exc:
        ea._check_cost_cap("old@x.com", need=("claude",))
    assert exc.value.status_code == 429


def test_unregistered_user_no_cap(monkeypatch):
    monkeypatch.setattr(ea, "is_admin", lambda e: False)
    monkeypatch.setattr(ea, "get_user", lambda e: None)
    ea._check_cost_cap("ghost@x.com", need=("claude", "gemini"))   # 예외 없음


# ── 관리자 전용 엔드포인트 인증 가드 + 폐기된 라우트 부재 ─────────────
def test_admin_users_requires_login():
    r = _client().get("/api/admin/users", follow_redirects=False)
    assert r.status_code == 401   # /api/* 는 401 JSON(307 아님)


def test_admin_add_user_requires_login():
    r = _client().post("/api/admin/users", json={"email": "x@y.com", "name": "x"},
                       follow_redirects=False)
    assert r.status_code == 401


def test_global_cap_routes_removed():
    paths = {getattr(r, "path", "") for r in app.routes}
    # 폐기: 글로벌 키 사용량/한도
    assert "/api/admin/usage" not in paths
    assert "/api/admin/caps/{provider}" not in paths
    # 유지: 사용자 관리
    assert "/api/admin/users" in paths
    assert "/api/admin/users/{email:path}" in paths
