"""
AKP 웹 변환 서버 — FastAPI + Google OAuth + examconv 엔진

실행:
    python -m uvicorn scripts.web.app:app --host 0.0.0.0 --port 8080

구성:
    - 인증/사용자 관리: Google OAuth (scripts/web/auth.py, users.py)
    - 학교×과목 매트릭스: matrix_config.json / matrix_registry.json (scripts/web/store.py)
    - 변환 엔진: backend/ (examconv 이식) — 라우트는 scripts/web/engine_api.py
    - 검수 UI: frontend/dist (React) — /converter 에 마운트
    - Google Drive 업로드: scripts/web/gdrive_uploader.py

환경변수:
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET  — Google OAuth2
    SECRET_KEY       — 세션 서명 키
    ADMIN_EMAIL      — 관리자 이메일
    ANTHROPIC_API_KEY — 분석(비전 구조화)·검증
    GEMINI_API_KEY   — 그림 재작도
    DAILY_COST_CAP   — 전체 일일 비용 한도 (기본 5.0)
    DATA_DIR         — 데이터 저장 경로 (Railway Volume)
"""
from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from authlib.integrations.starlette_client import OAuth
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_ROOT))

from scripts.web.auth import current_email, require_admin, require_login   # noqa: E402
from scripts.web.engine_api import cleanup_old_jobs, router as engine_router  # noqa: E402
from scripts.web.gdrive_uploader import (                                  # noqa: E402
    TOKEN_FILE, is_configured, save_refresh_token,
)
from scripts.web.store import (                                            # noqa: E402
    DATA_DIR, UPLOADS_DIR, WORK_DIR,
    load_mconfig, load_registry, save_mconfig, save_registry,
    validate_safe_key,
)
from scripts.web.usage_log import read_entries, today_summary              # noqa: E402
from scripts.web.users import (                                            # noqa: E402
    ROLE_DISPLAY, SELECTABLE_ROLES,
    add_user, get_allowed_stages, get_role, is_admin, is_allowed,
    list_users, remove_user, update_user,
)

_MANUAL_STAGES = {"hangeul", "typer", "solution"}

# 영속 경로 확인용 — 재배포 후 railway logs 에서 볼륨 경로가 맞는지 한눈에.
print(f"  [경로] DATA_DIR={DATA_DIR}  WORK_DIR={WORK_DIR}"
      f"  (볼륨마운트={os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', '') or '없음'})")

# ── Google OAuth 설정 ─────────────────────────────────────────────────
GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
SECRET_KEY           = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    SECRET_KEY = "akp-default-secret-change-me"
    print("  [경고] SECRET_KEY 미설정 — 공개된 기본 키로 세션을 서명합니다. "
          "세션 쿠키 위조(로그인 우회)가 가능하니 운영 환경에서는 반드시 설정하세요.")

oauth = OAuth()
oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={
        "scope": "openid email profile https://www.googleapis.com/auth/drive.file",
    },
)

# ── FastAPI 앱 ────────────────────────────────────────────────────────
app = FastAPI(title="AKP 변환기")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")


@app.on_event("startup")
async def _startup():
    cleanup_old_jobs()   # 3일 지난 변환 작업 폴더 정리


# ══════════════════════════════════════════════════════════════════════
# 인증 라우트
# ══════════════════════════════════════════════════════════════════════

@app.get("/auth/login")
async def auth_login(request: Request):
    redirect_uri = str(request.url_for("auth_callback"))
    params: dict = {"access_type": "offline"}
    if not is_configured() or request.query_params.get("gdrive"):
        params["prompt"] = "consent"   # 첫 로그인 또는 Drive 재인증
    return await oauth.google.authorize_redirect(request, redirect_uri, **params)


@app.get("/auth/callback")
async def auth_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception:
        return RedirectResponse("/auth/login")

    user_info = token.get("userinfo") or {}
    email = user_info.get("email", "")
    name  = user_info.get("name", email)
    refresh_token = token.get("refresh_token", "")

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

    # Drive 업로드용 refresh_token 저장 — 관리자(학원장) 계정만.
    # 아무 계정이나 저장하면 직원 첫 로그인의 토큰이 학원 Drive 토큰을 덮어써
    # 이후 빌드 결과물이 그 직원 개인 Drive 로 업로드된다.
    if refresh_token and is_admin(email):
        save_refresh_token(refresh_token)

    # Drive 재인증 경로: 성공/실패 결과 페이지로 이동
    if request.session.pop("gdrive_reauth", None):
        if refresh_token:
            return HTMLResponse(
                "<p style='font-family:sans-serif;padding:40px;color:green;font-size:1.2em'>"
                "✓ Google Drive 연동 완료</p>"
                "<script>setTimeout(()=>location.href='/',2000)</script>"
            )
        return HTMLResponse(
            "<p style='font-family:sans-serif;padding:40px;color:#c00'>"
            "refresh_token을 받지 못했습니다.<br><br>"
            "<a href='https://myaccount.google.com/permissions'>Google 계정 → 앱 권한</a>에서 "
            "AKP 앱 액세스를 취소 후 "
            "<a href='/auth/gdrive'>다시 시도</a>하세요.</p>"
        )

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
    if current_email(request):
        return RedirectResponse("/")
    return HTMLResponse((_HERE / "static" / "login.html").read_text(encoding="utf-8"))


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    email = current_email(request)
    if not email or not is_allowed(email):
        return RedirectResponse("/login")
    return HTMLResponse((_HERE / "static" / "matrix.html").read_text(encoding="utf-8"))


@app.get("/matrix", response_class=HTMLResponse)
async def matrix_page(request: Request):
    email = current_email(request)
    if not email or not is_allowed(email):
        return RedirectResponse("/login")
    return HTMLResponse((_HERE / "static" / "matrix.html").read_text(encoding="utf-8"))


@app.get("/guide", response_class=HTMLResponse)
async def guide_page():
    """직원용 사용설명서 (로그인 없이도 열람 가능)."""
    return HTMLResponse((_HERE / "static" / "guide.html").read_text(encoding="utf-8"))


@app.get("/api/usage")
async def api_usage(request: Request):
    require_login(request)
    return JSONResponse({"summary": today_summary(), "recent": read_entries(days=7)[:10]})


@app.get("/api/drive/status")
async def api_drive_status(request: Request):
    require_login(request)
    return JSONResponse({
        "configured": is_configured(),
        "token_path": str(TOKEN_FILE),
    })


@app.get("/auth/gdrive")
async def auth_gdrive(request: Request):
    """Drive refresh_token 강제 재취득 — /auth/login?gdrive=1 으로 위임.

    토큰은 학원 공용 Drive 자격이므로 관리자(학원장)만 갱신할 수 있다.
    """
    require_admin(request)
    request.session["gdrive_reauth"] = "1"
    return RedirectResponse("/auth/login?gdrive=1")


@app.get("/api/me")
async def api_me(request: Request):
    email = current_email(request)
    if not email:
        return JSONResponse({"authenticated": False})
    role = get_role(email)
    return JSONResponse({
        "authenticated": True,
        "email": email,
        "name": request.session.get("name", email),
        "is_admin": is_admin(email),
        "role": role,
        "role_display": ROLE_DISPLAY.get(role, role),
        "allowed_stages": get_allowed_stages(email),
    })


# ══════════════════════════════════════════════════════════════════════
# 파이프라인 수동 단계 (한글완성본·타이퍼·해설 파일 업로드 슬롯)
# ══════════════════════════════════════════════════════════════════════

@app.post("/api/pipeline/{key}/stages/{stage}")
async def pipeline_stage_upload(
    key: str, stage: str, request: Request,
    file: UploadFile = File(...),
):
    require_login(request)
    validate_safe_key(key)
    if stage not in _MANUAL_STAGES:
        raise HTTPException(400, f"지원하지 않는 단계: {stage}")

    stage_dir = UPLOADS_DIR / key / stage
    stage_dir.mkdir(parents=True, exist_ok=True)
    for old in stage_dir.iterdir():
        old.unlink(missing_ok=True)

    # 경로 성분 제거 — filename 은 클라이언트 값이라 "../.." 탈출을 막는다
    dest = stage_dir / (Path(file.filename or "").name or f"{stage}.hwpx")
    dest.write_bytes(await file.read())

    reg   = load_registry()
    entry = reg.get(key, {})
    stage_info = {
        "filename":    dest.name,
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
    }
    entry.setdefault("stages", {})[stage] = stage_info
    reg[key] = entry
    save_registry(reg)
    # stage 필드: matrix.html 이 셀 갱신에 사용(서버 저장 파일명·시각의 단일 출처)
    return JSONResponse({"ok": True, "filename": dest.name, "stage": stage_info})


@app.get("/api/pipeline/{key}/stages/{stage}/download")
async def pipeline_stage_download(key: str, stage: str, request: Request):
    require_login(request)
    validate_safe_key(key)
    if stage not in _MANUAL_STAGES:
        raise HTTPException(400)
    stage_dir = UPLOADS_DIR / key / stage
    files = list(stage_dir.iterdir()) if stage_dir.exists() else []
    if not files:
        raise HTTPException(404, "파일 없음")
    f = files[0]
    return FileResponse(str(f), media_type="application/octet-stream", filename=f.name)


@app.delete("/api/pipeline/{key}/stages/{stage}")
async def pipeline_stage_delete(key: str, stage: str, request: Request):
    require_login(request)
    validate_safe_key(key)
    if stage not in _MANUAL_STAGES:
        raise HTTPException(400)
    stage_dir = UPLOADS_DIR / key / stage
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    reg   = load_registry()
    entry = reg.get(key, {})
    entry.get("stages", {}).pop(stage, None)
    reg[key] = entry
    save_registry(reg)
    return JSONResponse({"ok": True})


# ══════════════════════════════════════════════════════════════════════
# 관리자 (사용자 관리)
# ══════════════════════════════════════════════════════════════════════

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    require_admin(request)
    return HTMLResponse((_HERE / "static" / "admin.html").read_text(encoding="utf-8"))


@app.get("/api/admin/users")
async def api_admin_users(request: Request):
    require_admin(request)
    return JSONResponse(list_users())


@app.post("/api/admin/users")
async def api_add_user(request: Request):
    require_admin(request)
    body = await request.json()
    email   = body.get("email", "").strip().lower()
    name    = body.get("name", "").strip()
    cap_usd = float(body.get("cap_usd", 2.0))
    if not email or not name:
        raise HTTPException(400, "이메일과 이름을 입력하세요.")
    # 이메일 형식 검증 — 따옴표·괄호 등이 섞인 값이 admin UI 의 onclick 속성으로
    # 되돌아가 XSS 벡터가 되는 것을 서버에서도 차단한다.
    import re
    if not re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", email):
        raise HTTPException(400, "올바른 이메일 형식이 아닙니다.")
    role = body.get("role", "tier1")
    if role not in SELECTABLE_ROLES:
        raise HTTPException(400, f"유효하지 않은 역할: {role}")
    add_user(email=email, name=name, cap_usd=cap_usd, role=role)
    return JSONResponse({"ok": True, "email": email})


@app.patch("/api/admin/users/{email:path}")
async def api_update_user(email: str, request: Request):
    require_admin(request)
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
    elif action == "role":
        new_role = body.get("role", "tier1")
        if new_role not in SELECTABLE_ROLES:
            raise HTTPException(400, f"유효하지 않은 역할: {new_role}")
        update_user(email, role=new_role)
    else:
        raise HTTPException(400, f"알 수 없는 액션: {action}")
    return JSONResponse({"ok": True})


# ══════════════════════════════════════════════════════════════════════
# 매트릭스 설정 (학교·과목) + 레지스트리
# ══════════════════════════════════════════════════════════════════════

@app.get("/api/config")
async def api_get_config(request: Request):
    require_login(request)
    return JSONResponse(load_mconfig())


@app.post("/api/config/schools")
async def api_add_school(request: Request):
    require_login(request)
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, "학교명을 입력하세요.")
    cfg = load_mconfig()
    if name in cfg["schools"]:
        raise HTTPException(409, f'"{name}"은 이미 등록된 학교입니다.')
    cfg["schools"].append(name)
    save_mconfig(cfg)
    return JSONResponse({"ok": True, "name": name})


@app.post("/api/config/schools/bulk")
async def api_bulk_add_schools(request: Request):
    require_login(request)
    body  = await request.json()
    names = [n.strip() for n in body.get("names", []) if n.strip()]
    if not names:
        raise HTTPException(400, "학교명을 입력하세요.")
    cfg   = load_mconfig()
    added, skipped = [], []
    for name in names:
        if name not in cfg["schools"]:
            cfg["schools"].append(name)
            added.append(name)
        else:
            skipped.append(name)
    save_mconfig(cfg)
    return JSONResponse({"ok": True, "added": added, "skipped": skipped})


@app.delete("/api/config/schools/{school:path}")
async def api_delete_school(school: str, request: Request):
    require_login(request)
    cfg = load_mconfig()
    if school not in cfg["schools"]:
        raise HTTPException(404)
    cfg["schools"].remove(school)
    save_mconfig(cfg)
    return JSONResponse({"ok": True})


@app.post("/api/config/subjects")
async def api_add_subject(request: Request):
    require_login(request)
    body = await request.json()
    sid  = body.get("id", "").strip()
    name = body.get("name", "").strip()
    if not sid or not name:
        raise HTTPException(400, "id와 name을 입력하세요.")
    cfg = load_mconfig()
    if any(s["id"] == sid for s in cfg["subjects"]):
        raise HTTPException(409, f'"{sid}"는 이미 등록된 과목입니다.')
    cfg["subjects"].append({"id": sid, "name": name, "grade": "", "sem": "", "exam_type": ""})
    save_mconfig(cfg)
    return JSONResponse({"ok": True, "id": sid, "name": name})


@app.patch("/api/config/subjects/{subj_id}")
async def api_update_subject(subj_id: str, request: Request):
    require_login(request)
    body = await request.json()
    cfg  = load_mconfig()
    subj = next((s for s in cfg["subjects"] if s["id"] == subj_id), None)
    if not subj:
        raise HTTPException(404)
    if "grade"     in body: subj["grade"]     = body["grade"]
    if "sem"       in body: subj["sem"]       = body["sem"]
    if "exam_type" in body: subj["exam_type"] = body["exam_type"]
    save_mconfig(cfg)
    return JSONResponse(subj)


@app.get("/api/registry")
async def api_get_registry(request: Request):
    require_login(request)
    return JSONResponse(load_registry())


@app.post("/api/registry/register")
async def api_registry_register(request: Request):
    """수동 등록/상태 갱신 (변환 완료 등록은 엔진이 서버 측에서 자동 수행)."""
    require_login(request)
    body         = await request.json()
    registry_key = body.get("registry_key", "").strip()
    job_id       = body.get("job_id", "").strip()
    if not registry_key or not job_id:
        raise HTTPException(400, "registry_key와 job_id는 필수입니다.")
    validate_safe_key(registry_key)
    reg      = load_registry()
    existing = reg.get(registry_key, {})
    entry = {
        **existing,
        "job_id": job_id,
        "status": body.get("status", "converting"),
        "subject": body.get("subject", existing.get("subject", "")),
        "school": body.get("school", existing.get("school", "")),
        "pdf_name": body.get("pdf_name") or existing.get("pdf_name", ""),
        "created_at": existing.get("created_at", datetime.now().isoformat(timespec="seconds")),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    reg[registry_key] = entry
    save_registry(reg)
    return JSONResponse(entry)


@app.patch("/api/registry/{key}/review")
async def api_registry_mark_reviewed(key: str, request: Request):
    """🔴 확인 필요 해제 — 직원이 한글에서 【확인필요】 자리를 채운 뒤 누른다."""
    email = require_login(request)
    validate_safe_key(key)
    reg = load_registry()
    if key not in reg:
        raise HTTPException(404, "레지스트리에 없는 키입니다.")
    entry = reg[key]
    entry["needs_review"] = False
    entry["reviewed_by"] = request.session.get("name", email)
    entry["reviewed_at"] = datetime.now().isoformat(timespec="seconds")
    entry["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_registry(reg)
    return JSONResponse(entry)


@app.post("/api/registry/move")
async def api_registry_move(request: Request):
    """잡(변환 결과 + 수동 업로드)을 다른 레지스트리 키로 이동."""
    require_login(request)
    body     = await request.json()
    from_key = body.get("from_key", "").strip()
    to_key   = body.get("to_key", "").strip()
    if not from_key or not to_key:
        raise HTTPException(400, "from_key와 to_key는 필수입니다.")
    validate_safe_key(from_key)
    validate_safe_key(to_key)
    if from_key == to_key:
        raise HTTPException(400, "같은 위치입니다.")

    reg = load_registry()
    if from_key not in reg:
        raise HTTPException(404, f"원본 키에 잡이 없습니다: {from_key}")
    to_entry = reg.get(to_key) or {}
    if to_entry.get("job_id"):
        raise HTTPException(409, "대상 위치에 이미 잡이 있습니다. 먼저 삭제하세요.")
    to_stage_dir = UPLOADS_DIR / to_key
    if to_entry.get("stages") or (to_stage_dir.exists() and any(to_stage_dir.iterdir())):
        # 대상 셀의 수동 업로드(한글완성본 등)를 조용히 지우고 덮어쓰지 않는다
        raise HTTPException(409, "대상 위치에 업로드된 파일이 있습니다. 먼저 삭제하세요.")

    entry = reg.pop(from_key)
    entry["updated_at"] = datetime.now().isoformat(timespec="seconds")
    reg[to_key] = entry
    save_registry(reg)

    # 잡 상태의 registry_key 도 갱신 (재빌드 시 Drive 파일명·업로드 경로 일치)
    job_id = entry.get("job_id", "")
    if job_id:
        from scripts.web.engine_api import _job, _save_state
        j = _job(job_id)
        if j:
            j["registry_key"] = to_key
            _save_state(job_id)

    # stages 디렉토리 이동 (업로드된 한글완성본·타이퍼·해설)
    from_stage_dir = UPLOADS_DIR / from_key
    if from_stage_dir.exists():
        if to_stage_dir.exists():
            shutil.rmtree(to_stage_dir)   # 위에서 내용물 있으면 409 — 빈 껍데기만 제거
        shutil.move(str(from_stage_dir), str(to_stage_dir))

    return JSONResponse({"ok": True, "from": from_key, "to": to_key, "entry": entry})


# ══════════════════════════════════════════════════════════════════════
# 변환 엔진 (examconv) — /api/analyze, /api/jobs/*, /api/figure/*
# ══════════════════════════════════════════════════════════════════════

app.include_router(engine_router)


# ── 변환 검수 UI (React, frontend/dist) — 모든 API 라우트 뒤에 마운트 ──────
class _NoCacheStatic(StaticFiles):
    """프론트 번들 갱신이 새로고침만으로 바로 반영되도록 캐시를 끈다."""
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        return resp


_FRONTEND = _ROOT / "frontend" / "dist"
if _FRONTEND.exists():
    app.mount("/converter", _NoCacheStatic(directory=str(_FRONTEND), html=True), name="converter")
else:
    print("  [경고] frontend/dist 없음 — 변환 UI 비활성 (frontend/ 에서 npm run build)")
