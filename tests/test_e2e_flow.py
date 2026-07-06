"""인증 세션 E2E — 업로드→렌더→크롭→지우개→빌드→다운로드→삭제 전 구간.

과금 구간(Claude 분석)만 스킵하고 문제를 직접 주입한다. Drive 업로드는 목 처리.
2026-07-06 cv2 5.0 크롭 500 사고 같은 '엔진-웹 접합부' 회귀를 API 레벨에서 잡기 위한 테스트.
"""
from __future__ import annotations

import base64
import io
import json
import time

import pytest
from starlette.testclient import TestClient

import scripts.web.engine_api as ea
import scripts.web.store as store

QA_KEY = "9999_1_1_a_QA_테스트고"


def _make_pdf(path):
    """fitz 로 텍스트 든 1페이지 PDF 생성 (외부 파일 의존 없음)."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "1. Find x such that x^2 = 4.", fontsize=14)
    page.insert_text((72, 160), "(1) 1  (2) 2  (3) 3  (4) 4  (5) 5", fontsize=12)
    page.draw_rect(fitz.Rect(72, 220, 300, 380), width=1.2)   # 크롭 대상 '그림'
    doc.save(str(path))
    doc.close()


def _session_cookie(email: str) -> str:
    """SessionMiddleware(itsdangerous TimestampSigner) 형식의 서명 쿠키 생성."""
    from itsdangerous import TimestampSigner
    from scripts.web.app import SECRET_KEY
    data = base64.b64encode(json.dumps({"email": email, "name": "QA"}).encode("utf-8"))
    return TimestampSigner(str(SECRET_KEY)).sign(data).decode("utf-8")


def _red_mask_png(w: int, h: int) -> bytes:
    """FigureCard 가 보내는 것과 같은 빨간 브러시 마스크 PNG (작게 — TELEA 경로)."""
    from PIL import Image
    m = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for x in range(5, min(25, w)):
        for y in range(5, min(25, h)):
            m.putpixel((x, y), (255, 69, 58, 255))
    buf = io.BytesIO()
    m.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """작업/레지스트리 경로 격리 + Drive 목 + 관리자 세션 클라이언트."""
    from scripts.web.app import app
    from scripts.web.users import ADMIN_EMAIL

    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(ea, "WORK", work)
    monkeypatch.setattr(store, "REGISTRY_FILE", tmp_path / "registry.json")

    uploaded, deleted = [], []
    monkeypatch.setattr(ea, "upload_hwpx", lambda p, y, s: (uploaded.append((p.name, y, s)), "drv_qa1")[1])
    monkeypatch.setattr(ea, "drive_delete_file", lambda fid: (deleted.append(fid), True)[1])
    monkeypatch.setattr(ea, "_log_usage", lambda *a, **k: 0.0)   # usage.jsonl 오염 방지

    email = ADMIN_EMAIL or "qa@example.com"
    if not ADMIN_EMAIL:   # 관리자 미설정 환경(CI 등)이면 허용 사용자로 등록
        import scripts.web.users as users
        monkeypatch.setattr(users, "ADMIN_EMAIL", email)
        monkeypatch.setattr(ea, "is_admin", lambda e: True)

    client = TestClient(app)
    client.cookies.set("session", _session_cookie(email))
    return client, work, uploaded, deleted, tmp_path


def test_full_flow(env, tmp_path):
    client, work, uploaded, deleted, _ = env

    # 1) 업로드 (registry_key 포함) → 레지스트리 converting 등록
    pdf = tmp_path / "qa.pdf"
    _make_pdf(pdf)
    r = client.post("/api/analyze",
                    files={"file": ("qa.pdf", pdf.read_bytes(), "application/pdf")},
                    data={"school": "테스트고", "subject": "수학", "registry_key": QA_KEY})
    assert r.status_code == 200, r.text
    job = r.json()["job_id"]
    assert store.load_registry()[QA_KEY]["status"] == "converting"

    # 2) 페이지 렌더 완료 대기 (백그라운드 스레드)
    for _ in range(100):
        j = client.get(f"/api/jobs/{job}").json()
        if j["status"] == "preview":
            break
        assert j["status"] != "error", j.get("error")
        time.sleep(0.1)
    assert j["status"] == "preview" and j["pages"], "렌더 미완료"

    # 페이지 이미지 서빙
    assert client.get(f"/api/jobs/{job}/pages/0.png").status_code == 200

    # 3) 분석(과금)은 스킵 — 검수 화면이 받는 형태로 문제를 직접 주입
    ea.JOBS[job].update(status="done", problems=[{
        "number": "1", "score": "4",
        "stem": [{"type": "text", "text": "x^2 = 4 의 양수 해는?"}],
        "choices": [[{"type": "text", "text": str(n)}] for n in range(1, 6)],
    }])
    ea._save_state(job)

    # 4) 그림 크롭 (2026-07-06 cv2 사고 지점 — deskew 경로 포함)
    r = client.post("/api/figure/crop",
                    json={"job_id": job, "page": 0, "bbox": [0.1, 0.25, 0.55, 0.5]})
    assert r.status_code == 200, r.text
    fid = r.json()["figure_id"]
    assert client.get(f"/api/figure/{job}/{fid}.png").status_code == 200

    # 5) 낙서 지우개 (인페인팅)
    crop_img = client.get(f"/api/figure/{job}/{fid}.png").content
    from PIL import Image
    w, h = Image.open(io.BytesIO(crop_img)).size
    r = client.post("/api/figure/erase",
                    data={"job_id": job, "figure_id": fid},
                    files={"mask": ("m.png", _red_mask_png(w, h), "image/png")})
    assert r.status_code == 200, r.text

    # 6) 빌드 → Drive 업로드 목 + 레지스트리 done
    r = client.post(f"/api/jobs/{job}/build",
                    json={"problems": ea.JOBS[job]["problems"],
                          "figures": [{"figure_id": fid, "problem_index": 0}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok" and body["problems"] == 1
    assert body["figures_inserted"] == 1, "그림이 HWPX 에 안 들어감"
    assert body["drive_uploaded"] is True
    assert uploaded and uploaded[0] == (f"[{QA_KEY}].hwpx", "9999", "QA")
    reg_entry = store.load_registry()[QA_KEY]
    assert reg_entry["status"] == "done"
    assert reg_entry["drive_file_id"] == "drv_qa1"
    assert reg_entry["needs_review"] is False   # 폴백 0 → 확인 필요 없음

    # 7) 다운로드 — 레지스트리 키 파일명 (한글은 RFC5987 percent-인코딩되므로 ASCII 부분으로 확인)
    r = client.get(f"/api/jobs/{job}/download")
    assert r.status_code == 200
    cd = r.headers.get("content-disposition", "")
    assert "9999_1_1_a_QA_" in cd and ".hwpx" in cd, cd
    assert r.content[:2] == b"PK", "zip(HWPX) 아님"

    # 8) 서버 재시작 복구(_rehydrate) — 인메모리 소실 시 state.json 에서 복원
    ea.JOBS.pop(job)
    j = client.get(f"/api/jobs/{job}").json()
    assert j["status"] == "done" and j.get("registry_key") == QA_KEY

    # 9) 삭제 — 작업폴더 + Drive + 레지스트리 정리
    r = client.delete(f"/api/jobs/{job}")
    assert r.status_code == 200
    assert QA_KEY in r.json()["deleted_keys"]
    assert deleted == ["drv_qa1"]
    assert not (work / job).exists()
    assert QA_KEY not in store.load_registry()


def test_needs_review_flag_and_clear(env):
    """🔴 확인 필요 — 수식 폴백 빌드 시 배지 설정, PATCH 로 해제."""
    client, *_ = env
    # 폴백이 나는 수식(\unknowncmd)으로 빌드 → needs_review 배지
    pdf_stub = {"number": "1", "score": "3",
                "stem": [{"type": "text", "text": "값은? "},
                         {"type": "eqn", "latex": r"\unknowncmd{x}"}],
                "choices": []}
    ea.JOBS["rvjob"] = {"status": "done", "dir": str(ea.WORK / "rvjob"),
                        "registry_key": QA_KEY, "school": "테스트고", "subject": "수학",
                        "pdf_name": "qa.pdf", "problems": [pdf_stub]}
    (ea.WORK / "rvjob").mkdir(exist_ok=True)
    r = client.post("/api/jobs/rvjob/build", json={"problems": [pdf_stub], "figures": []})
    assert r.status_code == 200, r.text
    assert r.json()["equations_fallback"] == 1
    entry = store.load_registry()[QA_KEY]
    assert entry["needs_review"] is True
    assert "수식 변환 실패 1개" in entry["review_note"]
    # 문서 안에 검색용 마커가 실제로 들어갔는지
    import zipfile
    sec = zipfile.ZipFile(ea.WORK / "rvjob" / "result.hwpx").read("Contents/section0.xml").decode()
    assert "【확인필요】" in sec

    # 확인 완료 처리 → 배지 해제 + 확인자 기록
    r = client.patch(f"/api/registry/{QA_KEY}/review")
    assert r.status_code == 200, r.text
    entry = store.load_registry()[QA_KEY]
    assert entry["needs_review"] is False
    assert entry["reviewed_by"] == "QA"

    # 정리
    client.delete("/api/jobs/rvjob")


def test_cost_cap_blocks_non_admin(env, monkeypatch, tmp_path):
    """일일 캡 초과 시 분석 시작이 429 로 막히는지 (관리자는 통과)."""
    client, *_ = env
    import scripts.web.users as users
    monkeypatch.setattr(ea, "is_admin", lambda e: False)
    monkeypatch.setattr(ea, "today_summary", lambda: {"cost_usd": 999.0})
    pdf = tmp_path / "cap.pdf"
    _make_pdf(pdf)
    r = client.post("/api/analyze", files={"file": ("cap.pdf", pdf.read_bytes(), "application/pdf")})
    assert r.status_code == 429
