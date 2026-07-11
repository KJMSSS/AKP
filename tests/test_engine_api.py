"""엔진 API 통합 스모크 — 라우트 등록·인증 가드·비용 계산·작업 정리."""
from __future__ import annotations

import time

from starlette.testclient import TestClient

import scripts.web.engine_api as ea
from scripts.web.app import app


def _client() -> TestClient:
    return TestClient(app)


def test_health_open():
    r = _client().get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert set(body) >= {"claude", "gemini", "mathpix", "opencv"}
    assert body["opencv"] is True   # cv2 네이티브 로드 실패(libGL 등) 감지


def test_engine_routes_registered():
    paths = {getattr(r, "path", "") for r in ea.router.routes}
    assert "/api/analyze" in paths
    assert "/api/jobs/{job_id}/build" in paths
    assert "/api/figure/crop" in paths
    assert "/api/figure/erase" in paths
    assert "/api/figure/redraw" in paths


def test_jobs_requires_login():
    # /api/* 는 401 JSON — 307 이면 fetch 가 로그인 HTML 을 추종해 프론트가 조용히 죽는다
    r = _client().get("/api/jobs/whatever", follow_redirects=False)
    assert r.status_code == 401


def test_analyze_requires_login():
    r = _client().post("/api/analyze", files={"file": ("a.pdf", b"%PDF-")},
                       follow_redirects=False)
    assert r.status_code == 401


def test_page_still_redirects_to_login():
    # 페이지 내비게이션(/admin 등 비 /api/ 경로)은 기존대로 로그인 리다이렉트
    r = _client().get("/admin", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/login"


def test_usage_cost_opus_pricing():
    # claude-opus-4-8: in $5 / out $25 / cache-read $0.5 / cache-write $6.25 per MTok
    u = {"input_tokens": 1_000_000, "output_tokens": 0,
         "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    assert ea._usage_cost(u) == 5.0
    u = {"input_tokens": 100_000, "output_tokens": 20_000,
         "cache_read_input_tokens": 200_000, "cache_creation_input_tokens": 40_000}
    # 0.5 + 0.5 + 0.1 + 0.25 = 1.35
    assert ea._usage_cost(u) == 1.35


def test_cleanup_old_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(ea, "WORK", tmp_path)
    old = tmp_path / "oldjob"
    new = tmp_path / "newjob"
    old.mkdir(); new.mkdir()
    (old / "state.json").write_text("{}")
    (new / "state.json").write_text("{}")
    ancient = time.time() - 4 * 86400   # 4일 전 (보관 3일 초과)
    import os
    os.utime(old / "state.json", (ancient, ancient))
    os.utime(old, (ancient, ancient))
    removed = ea.cleanup_old_jobs()
    assert removed == 1
    assert not old.exists()
    assert new.exists()


def test_converter_ui_served():
    r = _client().get("/converter/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
