"""
AKP 웹 변환 서버 — FastAPI + Google OAuth + SSE

실행:
    py -m uvicorn scripts.web.app:app --host 0.0.0.0 --port 8080

환경변수:
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET  — Google OAuth2
    SECRET_KEY       — 세션 서명 키
    ADMIN_EMAIL      — 관리자 이메일
    ANTHROPIC_API_KEY
    DAILY_COST_CAP   — 전체 일일 비용 한도 (기본 5.0)
    DATA_DIR         — 데이터 저장 경로 (Railway Volume)
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import queue
import re
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import fitz  # PyMuPDF
from authlib.integrations.starlette_client import OAuth
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

# ── 경로 설정 ──────────────────────────────────────────────────────────
_HERE    = Path(__file__).resolve().parent
_ROOT    = _HERE.parent.parent
_TMP_DIR = Path(os.environ.get("TMP_DIR", str(_HERE / "tmp")))
_TMP_DIR.mkdir(exist_ok=True, parents=True)

_DATA_DIR = Path(os.environ.get("DATA_DIR", str(_HERE / "data")))
_DATA_DIR.mkdir(exist_ok=True, parents=True)
_CONFIG_FILE   = _DATA_DIR / "matrix_config.json"
_REGISTRY_FILE = _DATA_DIR / "matrix_registry.json"
_config_lock   = threading.Lock()
_registry_lock = threading.Lock()

_DEFAULT_SUBJECTS = [
    {"id": "공수1", "name": "공통수학1", "grade": "1", "sem": "1"},
    {"id": "공수2", "name": "공통수학2", "grade": "1", "sem": "2"},
    {"id": "대수",  "name": "대수",      "grade": "2", "sem": "1"},
    {"id": "확통",  "name": "확률과 통계","grade": "2", "sem": "2"},
    {"id": "기하",  "name": "기하",      "grade": "2", "sem": "2"},
    {"id": "미적1", "name": "미적분1",   "grade": "2", "sem": "2"},
    {"id": "미적2", "name": "미적분2",   "grade": "3", "sem": "1"},
]

def _load_mconfig() -> dict:
    with _config_lock:
        if _CONFIG_FILE.exists():
            try:
                return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        cfg = {"subjects": _DEFAULT_SUBJECTS, "schools": []}
        _CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return cfg

def _save_mconfig(cfg: dict) -> None:
    with _config_lock:
        _CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

def _load_registry() -> dict:
    with _registry_lock:
        if _REGISTRY_FILE.exists():
            try:
                return json.loads(_REGISTRY_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

def _save_registry(reg: dict) -> None:
    with _registry_lock:
        _REGISTRY_FILE.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")

sys.path.insert(0, str(_ROOT))

from src.ocr.claude_pdf_reader import read_pdf_as_markdown          # noqa: E402
from src.ocr.cost_guard import CostGuard, CostCapError              # noqa: E402
from src.text_only.text_builder import build_from_markdown           # noqa: E402
from src.text_only.ocr_fallback import apply_fallback               # noqa: E402
from src.text_only.problem_segmenter import parse_problems, rebuild_markdown  # noqa: E402
from src.common.image_extractor import extract_images, extract_figures_by_vision  # noqa: E402
from src.common.hwpx_image_inserter import insert_figure_placeholder  # noqa: E402
from src.common.hwpx_table_inserter import (                        # noqa: E402
    replace_condition_tables, replace_boilerplate_tables,
)
from src.common.hwpx_namespace_fixer import fix_hwpx_namespaces    # noqa: E402
from src.common.hwpx_validator import validate_hwpx                 # noqa: E402
from scripts.web.usage_log import (                                 # noqa: E402
    append_entry, read_entries, today_summary, DAILY_CAP_USD,
)
from scripts.web.corrections_log import (                           # noqa: E402
    append_correction, read_corrections, revert_correction,
    corrections_summary,
    approve_as_pattern, get_active_patterns,
    list_patterns, toggle_pattern, delete_pattern,
)
from scripts.web.users import (                                     # noqa: E402
    is_admin, is_allowed, get_user, add_user, update_user,
    remove_user, list_users, user_today_cost, ADMIN_EMAIL,
)

# ── 템플릿 HWPX ───────────────────────────────────────────────────────
_SAMPLES = _ROOT / "samples"
_TEMPLATE = next(
    (f for f in _SAMPLES.glob("*.hwpx") if "워드초벌" in f.name and "]1." not in f.name),
    next(_SAMPLES.glob("*.hwpx"), None),
)

# ── Job 저장소 ────────────────────────────────────────────────────────
_jobs: dict[str, dict] = {}

# ── Google OAuth 설정 ─────────────────────────────────────────────────
GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
SECRET_KEY           = os.environ.get("SECRET_KEY", "akp-default-secret-change-me")

oauth = OAuth()
oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

# ── FastAPI 앱 ────────────────────────────────────────────────────────
app = FastAPI(title="AKP 변환기")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")


# ══════════════════════════════════════════════════════════════════════
# 인증 헬퍼
# ══════════════════════════════════════════════════════════════════════

def _current_email(request: Request) -> str | None:
    return request.session.get("email")


def _require_login(request: Request) -> str:
    email = _current_email(request)
    if not email:
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    if not is_allowed(email):
        raise HTTPException(status_code=403, detail="접근 권한이 없습니다. 관리자에게 문의하세요.")
    return email


def _require_admin(request: Request) -> str:
    email = _require_login(request)
    if not is_admin(email):
        raise HTTPException(status_code=403, detail="관리자만 접근 가능합니다.")
    return email


# ══════════════════════════════════════════════════════════════════════
# 인증 라우트
# ══════════════════════════════════════════════════════════════════════

@app.get("/auth/login")
async def auth_login(request: Request):
    redirect_uri = str(request.url_for("auth_callback"))
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/auth/callback")
async def auth_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception:
        return RedirectResponse("/auth/login")

    user_info = token.get("userinfo") or {}
    email = user_info.get("email", "")
    name  = user_info.get("name", email)

    if not email:
        return RedirectResponse("/auth/login")

    if not is_allowed(email) and not is_admin(email):
        return HTMLResponse(
            f"<h2 style='font-family:sans-serif;padding:40px'>접근 권한이 없습니다.</h2>"
            f"<p style='font-family:sans-serif;padding:0 40px'>{email} 계정은 등록되지 않았습니다.<br>"
            f"학원장에게 등록을 요청하세요.</p>",
            status_code=403,
        )

    request.session["email"] = email
    request.session["name"]  = name
    return RedirectResponse("/")


@app.get("/auth/logout")
async def auth_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/auth/login")


# ══════════════════════════════════════════════════════════════════════
# 기본 라우트
# ══════════════════════════════════════════════════════════════════════

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _current_email(request):
        return RedirectResponse("/")
    return HTMLResponse((_HERE / "static" / "login.html").read_text(encoding="utf-8"))


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    email = _current_email(request)
    if not email or not is_allowed(email):
        return RedirectResponse("/login")
    return HTMLResponse((_HERE / "static" / "matrix.html").read_text(encoding="utf-8"))


@app.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    email = _current_email(request)
    if not email or not is_allowed(email):
        return RedirectResponse("/login")
    return HTMLResponse((_HERE / "static" / "index.html").read_text(encoding="utf-8"))


@app.get("/api/usage")
async def api_usage(request: Request):
    return JSONResponse({"summary": today_summary(), "recent": read_entries(days=7)[:10]})


@app.get("/api/me")
async def api_me(request: Request):
    email = _current_email(request)
    if not email:
        return JSONResponse({"authenticated": False})
    return JSONResponse({
        "authenticated": True,
        "email": email,
        "name": request.session.get("name", email),
        "is_admin": is_admin(email),
    })


@app.post("/convert")
async def convert(
    request: Request,
    file: UploadFile = File(...),
    full_content: str = Form("false"),
    custom_filename: str = Form(""),
):
    email = _require_login(request)

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "PDF 파일만 업로드 가능합니다.")

    # 사용자별 한도 체크 (관리자는 무제한)
    if not is_admin(email):
        user = get_user(email)
        cap  = user.get("cap_usd", DAILY_CAP_USD) if user else DAILY_CAP_USD
        if cap > 0:
            today_cost = user_today_cost(email)
            if today_cost >= cap:
                raise HTTPException(
                    429,
                    f"오늘 사용 한도 ${cap:.2f}에 도달했습니다 (현재 ${today_cost:.2f}). "
                    "관리자에게 문의하세요.",
                )

    job_id  = uuid.uuid4().hex[:12]
    pdf_dst = _TMP_DIR / f"{job_id}.pdf"
    pdf_dst.write_bytes(await file.read())

    q: queue.Queue[str | None] = queue.Queue()
    _jobs[job_id] = {"queue": q, "hwpx": None, "meta": {}}

    full = full_content.lower() in ("true", "1", "yes")
    threading.Thread(
        target=_run_conversion,
        args=(job_id, pdf_dst, file.filename, full, email, custom_filename.strip()),
        daemon=True,
    ).start()
    return JSONResponse({"job_id": job_id})


@app.get("/stream/{job_id}")
async def stream(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404)

    async def event_gen():
        q = _jobs[job_id]["queue"]
        while True:
            try:
                msg = q.get(timeout=0.3)
            except queue.Empty:
                await asyncio.sleep(0.1)
                continue
            if msg is None:
                break
            yield msg
        meta = _jobs[job_id].get("meta", {})
        if meta.get("error"):
            yield f"event: error\ndata: {meta['error']}\n\n"
        else:
            hwpx  = _jobs[job_id].get("hwpx")
            fname = hwpx.name if hwpx else ""
            yield (
                f"event: done\n"
                f"data: {json.dumps({'file': fname, 'cost': meta.get('cost_usd', 0)})}\n\n"
            )

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/download/{job_id}")
async def download(job_id: str):
    # 메모리에 있으면 바로 사용
    job = _jobs.get(job_id)
    if job and job.get("hwpx") and job["hwpx"].exists():
        hwpx = job["hwpx"]
    else:
        # 서버 재시작 후에도 파일시스템에서 복원
        base_id = job_id.removesuffix("_reviewed")
        for candidate in [
            _TMP_DIR / f"{job_id}_reviewed.hwpx",
            _TMP_DIR / f"{job_id}.hwpx",
            _TMP_DIR / f"{base_id}_reviewed.hwpx",
            _TMP_DIR / f"{base_id}.hwpx",
        ]:
            if candidate.exists():
                hwpx = candidate
                break
        else:
            raise HTTPException(404)

    # 파일명 결정: custom_filename 우선, 없으면 PDF 이름
    base_id = job_id.removesuffix("_reviewed")
    review_file = _TMP_DIR / f"{base_id}_review.json"
    dl_name = hwpx.name  # 기본값
    if review_file.exists():
        try:
            meta = json.loads(review_file.read_text(encoding="utf-8"))
            custom = meta.get("custom_filename", "")
            pdf_name = meta.get("pdf_name", "")
            is_reviewed = "_reviewed" in hwpx.name
            if custom:
                stem   = Path(custom).stem
                suffix = "_검수" if is_reviewed else ""
                dl_name = f"{stem}{suffix}.hwpx"
            elif pdf_name:
                stem   = Path(pdf_name).stem
                suffix = "_검수" if is_reviewed else ""
                dl_name = f"{stem}{suffix}.hwpx"
        except Exception:
            pass

    return FileResponse(str(hwpx), media_type="application/octet-stream", filename=dl_name)


@app.get("/api/jobs/{job_id}")
async def api_job_info(job_id: str, request: Request):
    """저장된 job의 상태 확인 — 페이지 복귀 시 사용."""
    _require_login(request)
    has_hwpx   = (_TMP_DIR / f"{job_id}.hwpx").exists() or \
                 (_TMP_DIR / f"{job_id}_reviewed.hwpx").exists()
    has_review = (_TMP_DIR / f"{job_id}_review.json").exists()
    if not has_hwpx and not has_review:
        raise HTTPException(404)
    pdf_name = ""
    if has_review:
        try:
            pdf_name = json.loads(
                (_TMP_DIR / f"{job_id}_review.json").read_text(encoding="utf-8")
            ).get("pdf_name", "")
        except Exception:
            pass
    return JSONResponse({
        "job_id":     job_id,
        "pdf_name":   pdf_name,
        "has_hwpx":   has_hwpx,
        "has_review": has_review,
        "download_url": f"/download/{job_id}_reviewed" if (_TMP_DIR / f"{job_id}_reviewed.hwpx").exists()
                        else f"/download/{job_id}",
        "review_url": f"/review/{job_id}" if has_review else None,
    })


# ══════════════════════════════════════════════════════════════════════
# 관리자 라우트
# ══════════════════════════════════════════════════════════════════════

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    _require_admin(request)
    return HTMLResponse((_HERE / "static" / "admin.html").read_text(encoding="utf-8"))


@app.get("/api/admin/users")
async def api_admin_users(request: Request):
    _require_admin(request)
    return JSONResponse(list_users())


@app.post("/api/admin/users")
async def api_add_user(request: Request):
    _require_admin(request)
    body = await request.json()
    email   = body.get("email", "").strip().lower()
    name    = body.get("name", "").strip()
    cap_usd = float(body.get("cap_usd", 2.0))
    if not email or not name:
        raise HTTPException(400, "이메일과 이름을 입력하세요.")
    add_user(email, name, cap_usd)
    return JSONResponse({"ok": True, "email": email})


@app.patch("/api/admin/users/{email:path}")
async def api_update_user(email: str, request: Request):
    _require_admin(request)
    body   = await request.json()
    action = body.get("action", "")
    if action == "deactivate":
        update_user(email, active=False)
    elif action == "activate":
        update_user(email, active=True)
    elif action == "delete":
        remove_user(email)
    elif action == "cap":
        update_user(email, cap_usd=float(body.get("cap_usd", 2.0)))
    else:
        raise HTTPException(400, f"알 수 없는 액션: {action}")
    return JSONResponse({"ok": True})


@app.get("/api/admin/corrections")
async def api_corrections(request: Request, days: int = 30):
    _require_admin(request)
    return JSONResponse({
        "corrections": read_corrections(days=days),
        "summary":     corrections_summary(days=7),
    })


@app.patch("/api/admin/corrections/{cid}/revert")
async def api_revert_correction(cid: str, request: Request):
    _require_admin(request)
    if not revert_correction(cid):
        raise HTTPException(404)
    return JSONResponse({"ok": True})


@app.post("/api/admin/corrections/{cid}/approve-pattern")
async def api_approve_pattern(cid: str, request: Request):
    _require_admin(request)
    body          = await request.json()
    scope         = body.get("scope", "global")
    scope_value   = body.get("scope_value", "").strip()
    original_text = body.get("original_text", "").strip()
    corrected_text= body.get("corrected_text", "").strip()
    note          = body.get("note", "").strip()
    if not original_text and not corrected_text:
        raise HTTPException(400, "original_text 또는 corrected_text를 입력하세요.")
    pid = approve_as_pattern(cid, scope, scope_value, original_text, corrected_text, note)
    return JSONResponse({"ok": True, "pid": pid})


@app.get("/api/admin/patterns")
async def api_list_patterns(request: Request):
    _require_admin(request)
    return JSONResponse(list_patterns())


@app.patch("/api/admin/patterns/{pid}")
async def api_toggle_pattern(pid: str, request: Request):
    _require_admin(request)
    body = await request.json()
    active = bool(body.get("active", True))
    if not toggle_pattern(pid, active):
        raise HTTPException(404)
    return JSONResponse({"ok": True})


@app.delete("/api/admin/patterns/{pid}")
async def api_delete_pattern(pid: str, request: Request):
    _require_admin(request)
    if not delete_pattern(pid):
        raise HTTPException(404)
    return JSONResponse({"ok": True})


# ══════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════
# Matrix 라우트
# ══════════════════════════════════════════════════════════════════════

@app.get("/matrix", response_class=HTMLResponse)
async def matrix_page(request: Request):
    email = _current_email(request)
    if not email or not is_allowed(email):
        return RedirectResponse("/login")
    return HTMLResponse((_HERE / "static" / "matrix.html").read_text(encoding="utf-8"))


@app.get("/api/config")
async def api_get_config(request: Request):
    _require_login(request)
    return JSONResponse(_load_mconfig())


@app.post("/api/config/schools")
async def api_add_school(request: Request):
    _require_login(request)
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, "학교명을 입력하세요.")
    cfg = _load_mconfig()
    if name in cfg["schools"]:
        raise HTTPException(409, f'"{name}"은 이미 등록된 학교입니다.')
    cfg["schools"].append(name)
    _save_mconfig(cfg)
    return JSONResponse({"ok": True, "name": name})


@app.delete("/api/config/schools/{school:path}")
async def api_delete_school(school: str, request: Request):
    _require_login(request)
    cfg = _load_mconfig()
    if school not in cfg["schools"]:
        raise HTTPException(404)
    cfg["schools"].remove(school)
    _save_mconfig(cfg)
    return JSONResponse({"ok": True})


@app.post("/api/config/subjects")
async def api_add_subject(request: Request):
    _require_login(request)
    body = await request.json()
    sid  = body.get("id", "").strip()
    name = body.get("name", "").strip()
    if not sid or not name:
        raise HTTPException(400, "id와 name을 입력하세요.")
    cfg = _load_mconfig()
    if any(s["id"] == sid for s in cfg["subjects"]):
        raise HTTPException(409, f'"{sid}"는 이미 등록된 과목입니다.')
    cfg["subjects"].append({"id": sid, "name": name, "grade": "", "sem": ""})
    _save_mconfig(cfg)
    return JSONResponse({"ok": True, "id": sid, "name": name})


@app.patch("/api/config/subjects/{subj_id}")
async def api_update_subject(subj_id: str, request: Request):
    _require_login(request)
    body = await request.json()
    cfg  = _load_mconfig()
    subj = next((s for s in cfg["subjects"] if s["id"] == subj_id), None)
    if not subj:
        raise HTTPException(404)
    if "grade" in body: subj["grade"] = body["grade"]
    if "sem"   in body: subj["sem"]   = body["sem"]
    _save_mconfig(cfg)
    return JSONResponse(subj)


@app.get("/api/registry")
async def api_get_registry(request: Request):
    _require_login(request)
    return JSONResponse(_load_registry())


@app.post("/api/registry/register")
async def api_registry_register(request: Request):
    _require_login(request)
    body         = await request.json()
    registry_key = body.get("registry_key", "").strip()
    job_id       = body.get("job_id", "").strip()
    subject      = body.get("subject", "")
    school       = body.get("school", "")
    status       = body.get("status", "converting")
    if not registry_key or not job_id:
        raise HTTPException(400, "registry_key와 job_id는 필수입니다.")
    reg      = _load_registry()
    existing = reg.get(registry_key, {})
    review_status = existing.get("review_status")
    reviewer_name = existing.get("reviewer_name")
    reviewed_at   = existing.get("reviewed_at")
    if status == "done":
        rf = _TMP_DIR / f"{job_id}_review.json"
        if rf.exists():
            try:
                rv = json.loads(rf.read_text(encoding="utf-8"))
                if rv.get("review_status") == "completed":
                    review_status = "completed"
                    reviewer_name = rv.get("reviewer_name", reviewer_name)
                    reviewed_at   = rv.get("reviewed_at", reviewed_at)
            except Exception:
                pass
    entry = {
        "job_id": job_id, "status": status,
        "review_status": review_status,
        "reviewer_name": reviewer_name, "reviewed_at": reviewed_at,
        "subject": subject, "school": school,
        "created_at": existing.get("created_at", datetime.now().isoformat(timespec="seconds")),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    reg[registry_key] = entry
    _save_registry(reg)
    return JSONResponse(entry)


# 검수 라우트
# ══════════════════════════════════════════════════════════════════════

@app.get("/review/{job_id}", response_class=HTMLResponse)
async def review_page(job_id: str, request: Request):
    _require_login(request)
    if not (_TMP_DIR / f"{job_id}_review.json").exists():
        raise HTTPException(404, "검수 데이터가 없습니다.")
    return HTMLResponse((_HERE / "static" / "review.html").read_text(encoding="utf-8"))


@app.get("/api/review/{job_id}")
async def api_review(job_id: str, request: Request):
    _require_login(request)
    f = _TMP_DIR / f"{job_id}_review.json"
    if not f.exists():
        raise HTTPException(404)
    return JSONResponse(json.loads(f.read_text(encoding="utf-8")))


@app.get("/page/{job_id}/{page_num}")
async def page_image(job_id: str, page_num: int, request: Request):
    _require_login(request)
    img = _TMP_DIR / f"{job_id}_page{page_num}.png"
    if not img.exists():
        raise HTTPException(404)
    return FileResponse(str(img), media_type="image/png")


@app.post("/api/review/{job_id}/corrections")
async def save_corrections(job_id: str, request: Request):
    email = _require_login(request)
    body  = await request.json()

    review_file = _TMP_DIR / f"{job_id}_review.json"
    pdf_name = ""
    if review_file.exists():
        try:
            pdf_name = json.loads(review_file.read_text(encoding="utf-8")).get("pdf_name", "")
        except Exception:
            pass

    saved = []
    for c in body.get("corrections", []):
        cid = append_correction({
            "employee":        request.session.get("name", email),
            "token":           email,
            "job_id":          job_id,
            "pdf_name":        pdf_name,
            "problem_number":  c.get("problem_number"),
            "problem_text":    c.get("problem_text", ""),
            "correction_note": c.get("correction_note", ""),
            "corrected_text":  c.get("corrected_text", ""),
        })
        saved.append(cid)
    return JSONResponse({"saved": len(saved)})


@app.post("/api/review/{job_id}/submit")
async def review_submit(job_id: str, request: Request):
    _require_login(request)
    review_file = _TMP_DIR / f"{job_id}_review.json"
    if not review_file.exists():
        raise HTTPException(404)

    body         = await request.json()
    problems     = body.get("problems", [])
    overall_note = body.get("overall_note", "").strip()
    if not problems:
        raise HTTPException(400, "문제 데이터가 없습니다.")

    review_data = json.loads(review_file.read_text(encoding="utf-8"))
    header      = review_data.get("header", "")
    new_md      = header + "\n\n" + "\n\n".join(p["full_text"] for p in problems)

    reviewed_id = f"{job_id}_reviewed"
    out_hwpx    = _TMP_DIR / f"{reviewed_id}.hwpx"

    try:
        if not _TEMPLATE:
            raise RuntimeError("템플릿 없음")
        build_from_markdown(new_md, out_hwpx, _TEMPLATE)
        replace_condition_tables(out_hwpx)
        replace_boilerplate_tables(out_hwpx)
        fix_hwpx_namespaces(str(out_hwpx))
        errs = validate_hwpx(str(out_hwpx))
        if errs:
            raise RuntimeError(f"검증 실패: {errs[0]}")
    except Exception as e:
        raise HTTPException(500, str(e))

    _jobs[reviewed_id] = {"queue": queue.Queue(), "hwpx": out_hwpx, "meta": {}}
    edited   = sum(1 for p in problems if p.get("status") == "edited")

    # review.json 갱신 — 수정 내용 + 검수 완료 정보
    edit_map = {p["number"]: p["full_text"] for p in problems}
    for p in review_data.get("problems", []):
        if p["number"] in edit_map:
            p["full_text"] = edit_map[p["number"]]
            p["status"]    = "pending"
    review_data["review_status"]   = "completed"
    review_data["reviewer_name"]   = request.session.get("name", email)
    review_data["reviewer_email"]  = email
    review_data["reviewed_at"]     = datetime.now().strftime("%Y-%m-%d %H:%M")
    review_file.write_text(
        json.dumps(review_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    email    = request.session.get("email", "")
    pdf_name = review_data.get("pdf_name", "")

    append_entry({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "pdf": pdf_name, "mode": "review",
        "in_tok": 0, "out_tok": 0, "cost_usd": 0, "duration_s": 0,
        "status": "ok", "edited": edited, "token": email,
    })

    # 전체 메모 저장
    if overall_note:
        append_correction({
            "employee":        request.session.get("name", email),
            "token":           email,
            "job_id":          job_id,
            "pdf_name":        pdf_name,
            "problem_number":  "전체",
            "problem_text":    "",
            "correction_note": overall_note,
            "corrected_text":  "",
        })

    # registry 검수완료 반영
    reg = _load_registry()
    for rk, ent in reg.items():
        if ent.get("job_id") == job_id:
            ent["review_status"] = "completed"
            ent["reviewer_name"] = request.session.get("name", email)
            ent["reviewed_at"]   = datetime.now().strftime("%Y-%m-%d %H:%M")
            ent["updated_at"]    = datetime.now().isoformat(timespec="seconds")
    _save_registry(reg)

    return JSONResponse({"download_url": f"/download/{reviewed_id}", "edited": edited})


# ══════════════════════════════════════════════════════════════════════
# 변환 워커
# ══════════════════════════════════════════════════════════════════════

class _QueueWriter(io.TextIOBase):
    def __init__(self, q):
        self._q = q
    def write(self, s):
        for line in s.splitlines():
            line = line.strip()
            if line:
                self._q.put(f"data: {line}\n\n")
        return len(s)
    def flush(self):
        pass


def _render_pdf_pages(pdf_path: Path, job_id: str) -> int:
    doc = fitz.open(str(pdf_path))
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=150)
        pix.save(str(_TMP_DIR / f"{job_id}_page{i}.png"))
    n = doc.page_count
    doc.close()
    return n


def _save_review_data(
    job_id: str, original_name: str, md: str, n_pages: int,
    custom_filename: str = "",
) -> None:
    header, segments = parse_problems(md)
    data = {
        "job_id":          job_id,
        "pdf_name":        original_name,
        "custom_filename": custom_filename,
        "header":          header,
        "pages":           n_pages,
        "problems": [
            {"number": seg.number, "full_text": seg.raw_block,
             "is_subjective": seg.is_subjective, "status": "pending"}
            for seg in segments
        ],
    }
    (_TMP_DIR / f"{job_id}_review.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _run_conversion(
    job_id: str,
    pdf_path: Path,
    original_name: str,
    full_content: bool,
    email: str = "",
    custom_filename: str = "",
) -> None:
    q    = _jobs[job_id]["queue"]
    meta = _jobs[job_id]["meta"]

    orig_stdout = sys.stdout
    sys.stdout  = _QueueWriter(q)
    ts_start    = datetime.now().isoformat(timespec="seconds")
    t0          = time.time()

    try:
        guard = CostGuard(cap_usd=DAILY_CAP_USD)
        guard.check_or_raise("web")

        print("  [검수] PDF 페이지 렌더링 중...")
        n_pages     = _render_pdf_pages(pdf_path, job_id)
        cost_before = guard.total_today()

        # custom_filename에서 학교/과목 파싱 (2026_2_1_a_공수1_경신여고.hwpx)
        _school, _subject = "", ""
        if custom_filename:
            parts = Path(custom_filename).stem.split("_")
            if len(parts) >= 6:
                _subject = parts[4]
                _school  = "_".join(parts[5:])
        patterns = get_active_patterns(school=_school, subject=_subject)
        if patterns:
            print(f"  [패턴] {len(patterns)}건 프롬프트 주입 (학교:{_school} 과목:{_subject})")

        md = read_pdf_as_markdown(
            pdf_path, full_content=full_content, correction_patterns=patterns
        )
        md = apply_fallback(md, pdf_path)

        header, segments = parse_problems(md)
        fig_dir = _TMP_DIR / f"{job_id}_figs"
        fig_dir.mkdir(exist_ok=True)

        # Claude OCR이 이미 【★ 그림:N번】 마커를 출력 → 세그먼트에서 감지
        figure_items_from_claude: set[str] = set()
        for seg in segments:
            m = re.search(r'【★ 그림:(\d+)번】', seg.problem_text)
            if m:
                figure_items_from_claude.add(m.group(1))

        # figure_map: item_no → image_path (PyMuPDF 또는 Vision)
        figure_map: dict[str, Path] = {}

        try:
            figures = extract_images(pdf_path, fig_dir, dpi=150)
            for f in figures:
                if f.item_no:
                    figure_map[f.item_no] = f.image_path
        except Exception as e:
            print(f"  [그림] PyMuPDF 감지 실패: {e}")

        # Vision 폴백: Claude 마커가 있는데 PyMuPDF가 못 찾은 경우만 실행
        # ★ Vision은 추출 전용 — Claude 미마킹 문제를 새로 추가하지 않음
        unresolved = figure_items_from_claude - set(figure_map)
        if unresolved:
            print(f"  [그림] Vision 폴백 ({len(unresolved)}건): {sorted(unresolved)}")
            page_pngs = sorted(
                _TMP_DIR.glob(f"{job_id}_page*.png"),
                key=lambda p: int(re.search(r'page(\d+)', p.stem).group(1)),
            )
            try:
                api_key = os.environ.get("ANTHROPIC_API_KEY", "")
                vision_map = extract_figures_by_vision(page_pngs, fig_dir, api_key=api_key)
                # Claude 마킹된 문제만 수용 (false-positive 차단)
                for no, path in vision_map.items():
                    if no in figure_items_from_claude:
                        figure_map[no] = path
            except Exception as e:
                print(f"  [그림] Vision 감지 실패: {e}")

        # Claude 마커는 problem_text에 이미 있으므로 rebuild_markdown에 추가 전달 없음
        md = rebuild_markdown(header, segments)

        out_hwpx = _TMP_DIR / f"{job_id}.hwpx"
        if not _TEMPLATE:
            raise RuntimeError("samples/ 폴더에 .hwpx 파일이 없습니다.")

        build_from_markdown(md, out_hwpx, _TEMPLATE)
        replace_condition_tables(out_hwpx)
        replace_boilerplate_tables(out_hwpx)
        fix_hwpx_namespaces(str(out_hwpx))
        errs = validate_hwpx(str(out_hwpx))
        if errs:
            raise RuntimeError(f"HWPX 검증 실패: {errs[0]}")

        # 그림 삽입: Claude 마커 기준만 (Vision 감지 추가분 배제)
        for item_no in sorted(figure_items_from_claude, key=lambda x: int(x)):
            if item_no not in figure_map:
                print(f"  [그림] {item_no}번 PNG 없음 — 플레이스홀더 유지")
                continue
            try:
                insert_figure_placeholder(out_hwpx, item_no, figure_map[item_no])
                print(f"  [그림] {item_no}번 삽입 완료")
            except Exception as e:
                print(f"  [그림] {item_no}번 삽입 실패: {e}")

        print("  [검수] 문제 파싱 및 검수 데이터 저장 중...")
        _save_review_data(job_id, original_name, md, n_pages, custom_filename)

        duration = round(time.time() - t0, 1)
        cost_usd = round(guard.total_today() - cost_before, 4)

        append_entry({
            "ts": ts_start, "pdf": original_name,
            "mode": "full" if full_content else "questions",
            "in_tok": 0, "out_tok": 0,
            "cost_usd": cost_usd, "duration_s": duration,
            "status": "ok", "token": email,
        })
        guard.record("web", cost_usd)

        _jobs[job_id]["hwpx"] = out_hwpx
        meta["cost_usd"] = cost_usd

    except CostCapError as e:
        append_entry({
            "ts": ts_start, "pdf": original_name,
            "mode": "full" if full_content else "questions",
            "in_tok": 0, "out_tok": 0, "cost_usd": 0,
            "duration_s": round(time.time() - t0, 1),
            "status": "cap_exceeded", "token": email,
        })
        meta["error"] = str(e)

    except Exception as e:
        import traceback
        print(f"  [오류] {e}")
        traceback.print_exc()
        append_entry({
            "ts": ts_start, "pdf": original_name,
            "mode": "full" if full_content else "questions",
            "in_tok": 0, "out_tok": 0, "cost_usd": 0,
            "duration_s": round(time.time() - t0, 1),
            "status": "error", "token": email,
        })
        meta["error"] = str(e)

    finally:
        sys.stdout = orig_stdout
        try:
            pdf_path.unlink(missing_ok=True)
        except Exception:
            pass
        q.put(None)
