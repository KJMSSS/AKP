"""API 키(provider)별 사용량·한도 + 직원 개인 한도 회귀 테스트.

- Gemini 이미지 재작도 과금(장당) 집계·드레인
- usage.jsonl provider 귀속(옛 항목=claude)
- settings 키 한도 저장/조회
- _check_cost_cap: 키 한도(관리자 포함) + 개인 한도(직원) 강제
- 관리자 전용 엔드포인트 인증 가드
"""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import HTTPException
from starlette.testclient import TestClient

import scripts.web.engine_api as ea
import scripts.web.settings as st
import scripts.web.usage_log as ul
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


# ── usage.jsonl provider 귀속 ─────────────────────────────────────────
def test_provider_today_cost_attribution(tmp_path, monkeypatch):
    monkeypatch.setattr(ul, "_LOG_FILE", tmp_path / "usage.jsonl")
    now = datetime.now().isoformat(timespec="seconds")
    ul.append_entry({"ts": now, "provider": "claude", "cost_usd": 1.0})
    ul.append_entry({"ts": now, "provider": "gemini", "cost_usd": 0.5})
    ul.append_entry({"ts": now, "cost_usd": 0.25})   # provider 필드 없는 옛 항목 → claude

    p = ul.provider_today_cost()
    assert p["claude"] == 1.25
    assert p["gemini"] == 0.5
    # today_summary 에도 분해가 실린다
    assert ul.today_summary()["providers"]["gemini"] == 0.5


# ── settings: 키 한도 저장/조회 ───────────────────────────────────────
def test_settings_cap_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "_SETTINGS_FILE", tmp_path / "api_settings.json")
    caps = st.get_caps()
    assert set(caps) >= {"claude", "gemini"}       # env 기본 시드
    st.set_cap("gemini", 7.5)
    assert st.get_cap("gemini") == 7.5
    assert "claude" in st.get_caps()               # 다른 provider 는 유지
    with pytest.raises(ValueError):
        st.set_cap("openai", 1.0)                   # 알 수 없는 provider
    with pytest.raises(ValueError):
        st.set_cap("claude", -1.0)                  # 음수 금지
    with pytest.raises(ValueError):
        st.set_cap("claude", float("inf"))          # inf 금지(캡 영구 무력화 방어)
    with pytest.raises(ValueError):
        st.set_cap("claude", float("nan"))          # NaN 금지


# ── _check_cost_cap: 키 한도(직원) + 개인 한도(직원), 관리자 전면 면제 ──
def test_key_cap_blocks_staff(monkeypatch):
    monkeypatch.setattr(ea, "get_caps", lambda: {"claude": 20.0, "gemini": 10.0})
    monkeypatch.setattr(ea, "provider_today_cost", lambda: {"claude": 21.0, "gemini": 0.0})
    monkeypatch.setattr(ea, "is_admin", lambda e: False)          # 직원
    monkeypatch.setattr(ea, "get_user", lambda e: {"cap_usd": 0})  # 개인 한도 무제한
    monkeypatch.setattr(ea, "user_today_cost", lambda e: 0.0)
    with pytest.raises(HTTPException) as exc:
        ea._check_cost_cap("staff@x.com", need=("claude",))
    assert exc.value.status_code == 429
    # gemini 는 여유 → gemini-only 검사는 통과
    ea._check_cost_cap("staff@x.com", need=("gemini",))


def test_admin_exempt_from_key_cap(monkeypatch):
    # 관리자(학원장)는 키 한도를 초과해도 면제 — 본인 작업이 막히면 안 됨
    monkeypatch.setattr(ea, "get_caps", lambda: {"claude": 20.0, "gemini": 10.0})
    monkeypatch.setattr(ea, "provider_today_cost", lambda: {"claude": 999.0, "gemini": 999.0})
    monkeypatch.setattr(ea, "is_admin", lambda e: True)
    ea._check_cost_cap("admin@x.com", need=("claude", "gemini"))   # 예외 없음


def test_key_cap_zero_means_unlimited(monkeypatch):
    monkeypatch.setattr(ea, "get_caps", lambda: {"claude": 0, "gemini": 0})
    monkeypatch.setattr(ea, "provider_today_cost", lambda: {"claude": 999.0, "gemini": 999.0})
    monkeypatch.setattr(ea, "is_admin", lambda e: False)          # 직원이라도 0=무제한이면 통과
    monkeypatch.setattr(ea, "get_user", lambda e: {"cap_usd": 0})
    monkeypatch.setattr(ea, "user_today_cost", lambda e: 0.0)
    ea._check_cost_cap("staff@x.com", need=("claude", "gemini"))   # 예외 없음


def test_user_cap_blocks_staff(monkeypatch):
    monkeypatch.setattr(ea, "get_caps", lambda: {"claude": 0, "gemini": 0})  # 키 무제한
    monkeypatch.setattr(ea, "provider_today_cost", lambda: {"claude": 0.0, "gemini": 0.0})
    monkeypatch.setattr(ea, "is_admin", lambda e: False)
    monkeypatch.setattr(ea, "get_user", lambda e: {"cap_usd": 2.0})
    monkeypatch.setattr(ea, "user_today_cost", lambda e: 2.5)
    with pytest.raises(HTTPException) as exc:
        ea._check_cost_cap("staff@x.com")
    assert exc.value.status_code == 429


def test_admin_personal_uncapped(monkeypatch):
    monkeypatch.setattr(ea, "get_caps", lambda: {"claude": 0, "gemini": 0})
    monkeypatch.setattr(ea, "provider_today_cost", lambda: {"claude": 0.0, "gemini": 0.0})
    monkeypatch.setattr(ea, "is_admin", lambda e: True)
    ea._check_cost_cap("admin@x.com")   # 개인 한도 검사 자체를 건너뜀 → 예외 없음


# ── 관리자 전용 엔드포인트 인증 가드 ──────────────────────────────────
def test_admin_usage_requires_login():
    r = _client().get("/api/admin/usage", follow_redirects=False)
    assert r.status_code == 401   # /api/* 는 401 JSON(307 아님)


def test_admin_caps_requires_login():
    r = _client().patch("/api/admin/caps/claude", json={"cap_usd": 5}, follow_redirects=False)
    assert r.status_code == 401


def test_admin_routes_registered():
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/admin/usage" in paths
    assert "/api/admin/caps/{provider}" in paths
