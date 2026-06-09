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
_UPLOADS_DIR   = _DATA_DIR / "uploads"
_UPLOADS_DIR.mkdir(exist_ok=True, parents=True)

_MANUAL_STAGES = {"hangeul", "typer", "solution"}
_FIGQ_DIR      = _DATA_DIR / "figure_queue"
_FIGQ_DIR.mkdir(exist_ok=True, parents=True)
_config_lock   = threading.Lock()
_registry_lock = threading.Lock()
_figq_lock     = threading.Lock()

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


def _validate_safe_key(key: str) -> str:
    """레지스트리 키/잡ID에 경로 탈출 문자가 없는지 확인."""
    if ".." in key or "/" in key or "\\" in key:
        raise HTTPException(400, "잘못된 키 값입니다.")
    return key

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


def _figq_key_dir(key: str) -> Path:
    """검증된 key에 대한 큐 디렉토리. _validate_safe_key 통과 후에만 호출."""
    return _FIGQ_DIR / key

def _figq_load(key: str) -> dict:
    """{key}/items.json 로드. 없으면 빈 dict."""
    f = _figq_key_dir(key) / "items.json"
    if not f.exists():
        return {"items": {}}
    try:
        with _figq_lock:
            return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {"items": {}}

def _figq_save(key: str, data: dict) -> None:
    d = _figq_key_dir(key)
    d.mkdir(parents=True, exist_ok=True)
    with _figq_lock:
        (d / "items.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def _figq_clamp_bbox_pct(bbox: list) -> tuple[float, float, float, float]:
    """[x0,y0,x1,y1] % 값을 0~100으로 클램프 + 정렬."""
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise HTTPException(400, "bbox는 [x0,y0,x1,y1] %  배열이어야 합니다.")
    try:
        vals = [float(v) for v in bbox]
    except (TypeError, ValueError):
        raise HTTPException(400, "bbox 값이 숫자가 아닙니다.")
    x0 = max(0.0, min(100.0, vals[0]));  y0 = max(0.0, min(100.0, vals[1]))
    x1 = max(0.0, min(100.0, vals[2]));  y1 = max(0.0, min(100.0, vals[3]))
    if x1 < x0: x0, x1 = x1, x0
    if y1 < y0: y0, y1 = y1, y0
    if (x1 - x0) < 1.0 or (y1 - y0) < 1.0:
        raise HTTPException(400, "bbox 영역이 너무 작습니다.")
    return (x0, y0, x1, y1)

sys.path.insert(0, str(_ROOT))

from src.ocr.claude_pdf_reader import read_pdf_as_markdown          # noqa: E402
from src.ocr.cost_guard import CostGuard, CostCapError              # noqa: E402
from src.ocr.latex_corrector import correct_latex                   # noqa: E402
from src.text_only.text_builder import build_from_markdown           # noqa: E402
from src.text_only.typer_builder import build_typer_hwpx             # noqa: E402
from src.text_only.ocr_fallback import apply_fallback               # noqa: E402
from src.text_only.problem_segmenter import parse_problems, rebuild_markdown  # noqa: E402
from src.common.image_extractor import (                            # noqa: E402
    extract_images, extract_figures_by_vision,
    crop_problems_by_bbox, extract_with_confidence, FigureCandidate,
)
from src.common.hwpx_image_inserter import insert_figure_placeholder  # noqa: E402
from src.common.hwpx_table_inserter import (                        # noqa: E402
    replace_condition_tables, replace_boilerplate_tables,
)
from src.common.hwpx_namespace_fixer import fix_hwpx_namespaces    # noqa: E402
from src.common.hwpx_validator import validate_hwpx                 # noqa: E402
from src.common.pdf_utils import normalize_pdf_rotation             # noqa: E402
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
    add_user, get_user, is_admin, is_allowed, list_users,
    remove_user, update_user,
    get_role, get_allowed_stages, ROLE_DISPLAY, SELECTABLE_ROLES,
    user_today_cost, ADMIN_EMAIL,
)
from scripts.web.gdrive_uploader import (                           # noqa: E402
    save_refresh_token, upload_hwpx, delete_file as drive_delete_file,
    is_configured, TOKEN_FILE,
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
    client_kwargs={
        "scope": "openid email profile https://www.googleapis.com/auth/drive.file",
    },
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

    # Drive 업로드용 refresh_token 저장 (처음 consent 동의 때만 포함됨)
    refresh_token = token.get("refresh_token", "")
    if refresh_token:
        save_refresh_token(refresh_token)

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


@app.post("/api/pdf/preview")
async def api_pdf_preview(request: Request, file: UploadFile = File(...)):
    """PDF 첫 페이지 미리보기 + 회전 감지. 업로드 확인 전 호출용."""
    _require_login(request)
    data = await file.read()
    try:
        import base64
        doc = fitz.open(stream=data, filetype="pdf")
        n   = doc.page_count
        page = doc[0]
        rotation = page.rotation
        # 첫 페이지를 400px 폭으로 축소 렌더링
        scale = min(400 / page.rect.width, 1.5)
        pix   = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
        b64   = base64.b64encode(pix.tobytes("png")).decode()
        doc.close()
        return JSONResponse({
            "pages":    n,
            "rotation": rotation,
            "needs_fix": rotation != 0,
            "preview":  f"data:image/png;base64,{b64}",
        })
    except Exception as e:
        raise HTTPException(400, f"PDF 읽기 실패: {e}")


@app.get("/api/drive/status")
async def api_drive_status(request: Request):
    _require_login(request)
    return JSONResponse({
        "configured": is_configured(),
        "token_path": str(TOKEN_FILE),
    })


@app.get("/auth/gdrive")
async def auth_gdrive(request: Request):
    """Drive refresh_token 강제 재취득 — /auth/login?gdrive=1 으로 위임."""
    _require_login(request)
    request.session["gdrive_reauth"] = "1"
    return RedirectResponse("/auth/login?gdrive=1")


@app.get("/api/me")
async def api_me(request: Request):
    email = _current_email(request)
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
async def download(job_id: str, request: Request):
    _require_login(request)
    _validate_safe_key(job_id)
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


@app.delete("/api/jobs/{job_id}")
async def api_delete_job(job_id: str, request: Request):
    """잡 삭제 — 로컬 임시파일 + Drive 파일 + 레지스트리 항목 제거."""
    _require_login(request)
    base_id = job_id.removesuffix("_reviewed")

    # Drive 파일 삭제
    review_file = _TMP_DIR / f"{base_id}_review.json"
    if review_file.exists():
        try:
            meta = json.loads(review_file.read_text(encoding="utf-8"))
            fid  = meta.get("drive_file_id", "")
            if fid:
                ok = drive_delete_file(fid)
                if not ok:
                    print(f"  [Drive] {fid} 삭제 실패 (계속 진행)")
        except Exception:
            pass

    # 로컬 임시 파일 전체 삭제
    patterns = [
        f"{base_id}.pdf", f"{base_id}.hwpx", f"{base_id}_reviewed.hwpx",
        f"{base_id}_review.json", f"{base_id}_rotfix.pdf",
    ]
    for name in patterns:
        (_TMP_DIR / name).unlink(missing_ok=True)
    for p in _TMP_DIR.glob(f"{base_id}_page*.png"):
        p.unlink(missing_ok=True)
    for p in _TMP_DIR.glob(f"{base_id}_figs"):
        import shutil
        shutil.rmtree(p, ignore_errors=True)

    # 메모리 잡 제거
    _jobs.pop(base_id, None)
    _jobs.pop(f"{base_id}_reviewed", None)

    # 레지스트리에서 제거
    reg = _load_registry()
    keys_to_del = [k for k, v in reg.items() if v.get("job_id") == base_id]
    for k in keys_to_del:
        del reg[k]
    if keys_to_del:
        _save_registry(reg)

    return JSONResponse({"ok": True, "deleted_keys": keys_to_del})


# ══════════════════════════════════════════════════════════════════════
# 파이프라인 수동 단계 (한글완성본·타이퍼·해설)
# ══════════════════════════════════════════════════════════════════════

@app.post("/api/pipeline/{key}/stages/{stage}")
async def pipeline_stage_upload(
    key: str, stage: str, request: Request,
    file: UploadFile = File(...),
):
    _require_login(request)
    _validate_safe_key(key)
    if stage not in _MANUAL_STAGES:
        raise HTTPException(400, f"지원하지 않는 단계: {stage}")

    stage_dir = _UPLOADS_DIR / key / stage
    stage_dir.mkdir(parents=True, exist_ok=True)
    for old in stage_dir.iterdir():
        old.unlink(missing_ok=True)

    dest = stage_dir / (file.filename or f"{stage}.hwpx")
    dest.write_bytes(await file.read())

    reg   = _load_registry()
    entry = reg.get(key, {})
    entry.setdefault("stages", {})[stage] = {
        "filename":    dest.name,
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
    }
    reg[key] = entry
    _save_registry(reg)
    return JSONResponse({"ok": True, "filename": dest.name})


@app.get("/api/pipeline/{key}/stages/{stage}/download")
async def pipeline_stage_download(key: str, stage: str, request: Request):
    _require_login(request)
    _validate_safe_key(key)
    if stage not in _MANUAL_STAGES:
        raise HTTPException(400)
    stage_dir = _UPLOADS_DIR / key / stage
    files = list(stage_dir.iterdir()) if stage_dir.exists() else []
    if not files:
        raise HTTPException(404, "파일 없음")
    f = files[0]
    return FileResponse(str(f), media_type="application/octet-stream", filename=f.name)


@app.delete("/api/pipeline/{key}/stages/{stage}")
async def pipeline_stage_delete(key: str, stage: str, request: Request):
    _require_login(request)
    _validate_safe_key(key)
    if stage not in _MANUAL_STAGES:
        raise HTTPException(400)
    stage_dir = _UPLOADS_DIR / key / stage
    if stage_dir.exists():
        import shutil
        shutil.rmtree(stage_dir)
    reg   = _load_registry()
    entry = reg.get(key, {})
    entry.get("stages", {}).pop(stage, None)
    reg[key] = entry
    _save_registry(reg)
    return JSONResponse({"ok": True})


@app.post("/api/pipeline/{key}/typer/generate")
async def pipeline_typer_generate(key: str, request: Request):
    """
    1단 HWPX(검수완 > 검수전)를 2단 타이퍼 양식으로 자동 변환.
    변환 결과는 _UPLOADS_DIR/key/typer/ 에 저장하고 registry에 반영.
    """
    _require_login(request)
    _validate_safe_key(key)
    reg   = _load_registry()
    entry = reg.get(key)
    if not entry:
        raise HTTPException(404, "등록된 키가 없습니다.")
    job_id = entry.get("job_id", "")
    if not job_id:
        raise HTTPException(400, "job_id가 없습니다. 먼저 PDF 변환을 완료하세요.")

    # 1단 HWPX 파일 결정: 검수완 우선, 없으면 검수전
    reviewed = _TMP_DIR / f"{job_id}_reviewed.hwpx"
    original = _TMP_DIR / f"{job_id}.hwpx"
    one_dan = reviewed if reviewed.exists() else original if original.exists() else None
    if not one_dan:
        raise HTTPException(404, "1단 HWPX 파일을 찾을 수 없습니다.")

    stage_dir = _UPLOADS_DIR / key / "typer"
    stage_dir.mkdir(parents=True, exist_ok=True)
    for old in stage_dir.iterdir():
        old.unlink(missing_ok=True)

    out_name = f"{key.strip('[]')}_타이퍼양식.hwpx"
    out_path = stage_dir / out_name

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: build_typer_hwpx(
                one_dan_path=one_dan,
                registry_key=key,
                out_path=out_path,
            ),
        )
    except Exception as e:
        raise HTTPException(500, f"타이퍼 양식 생성 실패: {e}")

    entry.setdefault("stages", {})["typer"] = {
        "filename":    out_path.name,
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        "auto":        True,
    }
    reg[key] = entry
    _save_registry(reg)
    return JSONResponse({"ok": True, "filename": out_path.name})


# ══════════════════════════════════════════════════════════════════════
# 그림 수동 검수 큐 (P1+P2: extract_with_confidence 미달 → 수동 드래그)
# ══════════════════════════════════════════════════════════════════════

def _register_figure_queue(
    reg_key: str,
    job_id: str,
    figure_items: set[str],
    figure_map: dict[str, Path],
    prob_crop_map: dict[str, Path] | None = None,
    conf_map: dict[str, "FigureCandidate"] | None = None,
    threshold: float = 0.7,
) -> None:
    """변환 완료 직후 그림 검수 큐 자동 등록.

    figure_items:  Claude OCR이 마킹한 그림 문제 번호 집합
    figure_map:    HWPX 삽입에 쓴 {prob_no: fig_png_path} (extract_images/vision)
    prob_crop_map: BBoxDetector로 자른 {prob_no: 문제별_crop_png} — 검수 화면 원본
    conf_map:      {prob_no: FigureCandidate} — extract_with_confidence 실측 결과
    threshold:     이 값 이상이면 auto_selected, 미만이면 pending(검수 권장)

    crop_path는 문제별 crop을 우선 사용하고, 없으면 첫 페이지 전체로 폴백한다.
    """
    from PIL import Image as PILImage

    prob_crop_map = prob_crop_map or {}
    conf_map = conf_map or {}

    page_pngs_sorted: list[Path] = sorted(
        _TMP_DIR.glob(f"{job_id}_page*.png"),
        key=lambda p: int(re.search(r"page(\d+)", p.stem).group(1)),
    )
    fallback_crop = str(page_pngs_sorted[0]) if page_pngs_sorted else None

    qdata = _figq_load(reg_key)
    items: dict = qdata.get("items", {})
    added = 0

    for prob_no in sorted(figure_items, key=lambda x: int(x) if x.isdigit() else 999):
        if prob_no in items:
            continue  # 이미 등록된 항목은 건드리지 않음

        crop = prob_crop_map.get(prob_no)
        crop_path = str(crop) if crop else fallback_crop

        cand = conf_map.get(prob_no)
        if cand is not None and crop is not None:
            # ── 실측 신뢰도 (Tesseract×Density IoU) ──────────────────────
            confidence = cand.confidence
            strategy   = cand.strategy
            auto_path  = str(cand.image_path)
            auto_bbox_pct = None
            try:
                with PILImage.open(crop) as im:
                    W, H = im.size
                x0, y0, x1, y1 = cand.bbox
                if W and H:
                    auto_bbox_pct = [
                        round(x0 / W * 100, 1), round(y0 / H * 100, 1),
                        round(x1 / W * 100, 1), round(y1 / H * 100, 1),
                    ]
            except Exception:
                pass
            status = "auto_selected" if confidence >= threshold else "pending"
        else:
            # ── 신뢰도 측정 불가 → figure_map 파일명 추정 폴백 ────────────
            auto_img = figure_map.get(prob_no)
            if auto_img and auto_img.exists():
                strategy   = "vision" if "vision" in auto_img.name else "pymupdf"
                confidence = 0.6 if strategy == "vision" else 0.7
                auto_path  = str(auto_img)
                status     = "auto_selected" if confidence > threshold else "pending"
            else:
                strategy   = "none"
                confidence = 0.0
                auto_path  = None
                status     = "pending"
            auto_bbox_pct = None

        items[prob_no] = {
            "prob_no":       prob_no,
            "page_no":       None,
            "status":        status,
            "strategy":      strategy,
            "confidence":    confidence,
            "crop_path":     crop_path,
            "auto_path":     auto_path,
            "auto_bbox_pct": auto_bbox_pct,
            "manual_path":   None,
            "created_at":    datetime.now().isoformat(timespec="seconds"),
        }
        added += 1

    if added == 0:
        return

    qdata["items"] = items
    _figq_save(reg_key, qdata)

    pending_cnt = sum(1 for v in items.values() if v["status"] == "pending")
    auto_cnt    = sum(1 for v in items.values() if v["status"] == "auto_selected")
    print(f"  [그림큐] +{added}건 등록 (자동={auto_cnt}, 검수필요={pending_cnt}) → /figure/{reg_key}")

@app.get("/figure/{key}", response_class=HTMLResponse)
async def figure_crop_page(key: str, request: Request):
    _require_login(request)
    _validate_safe_key(key)
    html = (_HERE / "static" / "figure_crop.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/api/figure/{key}/queue")
async def api_figure_queue(key: str, request: Request):
    """수동 검수 대기 그림 목록."""
    _require_login(request)
    _validate_safe_key(key)
    data = _figq_load(key)
    items = data.get("items", {})
    queue = []
    for prob_no, e in sorted(items.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 999):
        queue.append({
            "prob_no":        prob_no,
            "page_no":        e.get("page_no"),
            "status":         e.get("status", "pending"),
            "strategy":       e.get("strategy"),
            "confidence":     e.get("confidence"),
            "auto_bbox_pct":  e.get("auto_bbox_pct"),
        })
    return JSONResponse({"items": queue})


@app.get("/api/figure/{key}/{prob_no}/image")
async def api_figure_image(
    key: str, prob_no: str, request: Request,
    which: str = "crop", overlay: bool = False,
):
    """그림 검수용 이미지 응답.

    which: "crop"=문제 크롭, "auto"=자동 결과, "manual"=수동 결과
    overlay: True+which=crop이면 auto_bbox_pct를 빨간 박스로 오버레이
    """
    _require_login(request)
    _validate_safe_key(key)
    if which not in ("crop", "auto", "manual"):
        raise HTTPException(400, "which는 crop|auto|manual")

    data = _figq_load(key)
    entry = data.get("items", {}).get(prob_no)
    if not entry:
        raise HTTPException(404, f"{prob_no}번 그림 큐 없음")

    path_key = {"crop": "crop_path", "auto": "auto_path", "manual": "manual_path"}[which]
    raw_path = entry.get(path_key)
    if not raw_path:
        raise HTTPException(404, f"{which} 이미지 없음")

    src = Path(raw_path)
    if not src.exists():
        raise HTTPException(404, f"파일 없음: {src.name}")

    if not overlay or which != "crop":
        return FileResponse(str(src), media_type="image/png")

    bbox_pct = entry.get("auto_bbox_pct")
    if not bbox_pct:
        return FileResponse(str(src), media_type="image/png")

    from PIL import Image as PILImage, ImageDraw
    img = PILImage.open(src).convert("RGB")
    W, H = img.size
    x0 = int(W * bbox_pct[0] / 100);  y0 = int(H * bbox_pct[1] / 100)
    x1 = int(W * bbox_pct[2] / 100);  y1 = int(H * bbox_pct[3] / 100)
    draw = ImageDraw.Draw(img)
    draw.rectangle((x0, y0, x1, y1), outline="red", width=4)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


@app.post("/api/figure/{key}/{prob_no}/decision")
async def api_figure_decision(key: str, prob_no: str, request: Request):
    """검수 결정. body: {action: "auto"|"manual"|"skip", bbox_pct?: [x0,y0,x1,y1]}

    - auto:  자동 결과 채택 (auto_path가 최종 path)
    - manual: bbox_pct 필수, 큐 디렉토리에 manual_path 생성
    - skip:  건너뜀 (그림 없음으로 처리)
    """
    _require_login(request)
    _validate_safe_key(key)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "JSON 본문 필요")
    action = body.get("action")
    if action not in ("auto", "manual", "skip"):
        raise HTTPException(400, "action은 auto|manual|skip")

    data = _figq_load(key)
    entry = data.get("items", {}).get(prob_no)
    if not entry:
        raise HTTPException(404, f"{prob_no}번 그림 큐 없음")

    if action == "manual":
        bbox_pct = _figq_clamp_bbox_pct(body.get("bbox_pct") or [])
        crop_path = entry.get("crop_path")
        if not crop_path or not Path(crop_path).exists():
            raise HTTPException(404, "원본 크롭 파일 없음")

        from PIL import Image as PILImage
        img = PILImage.open(crop_path)
        W, H = img.size
        x0 = int(W * bbox_pct[0] / 100);  y0 = int(H * bbox_pct[1] / 100)
        x1 = int(W * bbox_pct[2] / 100);  y1 = int(H * bbox_pct[3] / 100)
        if (x1 - x0) < 4 or (y1 - y0) < 4:
            raise HTTPException(400, "픽셀 영역이 너무 작습니다.")

        out_dir = _figq_key_dir(key)
        out_dir.mkdir(parents=True, exist_ok=True)
        manual_path = out_dir / f"{prob_no}_manual.png"
        img.crop((x0, y0, x1, y1)).save(str(manual_path))

        # last-wins: 동시 요청 시 마지막 호출이 덮어쓴다 (로그만 남김)
        prev_status = entry.get("status")
        if prev_status and prev_status != "pending":
            print(f"  [figq] {key}/{prob_no} 상태 덮어쓰기: {prev_status} → manual_selected")

        entry["manual_bbox_pct"] = list(bbox_pct)
        entry["manual_path"] = str(manual_path)
        entry["status"] = "manual_selected"
    else:
        # auto / skip 모두 last-wins 단순 갱신
        entry["status"] = "auto_selected" if action == "auto" else "skipped"

    entry["updated_at"] = datetime.now().isoformat(timespec="seconds")
    data.setdefault("items", {})[prob_no] = entry
    _figq_save(key, data)
    return JSONResponse({"ok": True, "status": entry["status"]})


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
    role = body.get("role", "tier1")
    add_user(email=email, name=name, cap_usd=cap_usd, role=role)
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
    elif action == "role":
        new_role = body.get("role", "tier1")
        if new_role not in SELECTABLE_ROLES:
            raise HTTPException(400, f"유효하지 않은 역할: {new_role}")
        update_user(email, role=new_role)
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


@app.post("/api/config/schools/bulk")
async def api_bulk_add_schools(request: Request):
    _require_login(request)
    body  = await request.json()
    names = [n.strip() for n in body.get("names", []) if n.strip()]
    if not names:
        raise HTTPException(400, "학교명을 입력하세요.")
    cfg   = _load_mconfig()
    added, skipped = [], []
    for name in names:
        if name not in cfg["schools"]:
            cfg["schools"].append(name)
            added.append(name)
        else:
            skipped.append(name)
    _save_mconfig(cfg)
    return JSONResponse({"ok": True, "added": added, "skipped": skipped})


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
    cfg["subjects"].append({"id": sid, "name": name, "grade": "", "sem": "", "exam_type": ""})
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
    if "grade"     in body: subj["grade"]     = body["grade"]
    if "sem"       in body: subj["sem"]       = body["sem"]
    if "exam_type" in body: subj["exam_type"] = body["exam_type"]
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
    pdf_name     = body.get("pdf_name", "")
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
        "pdf_name": pdf_name or existing.get("pdf_name", ""),
        "created_at": existing.get("created_at", datetime.now().isoformat(timespec="seconds")),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    reg[registry_key] = entry
    _save_registry(reg)
    return JSONResponse(entry)


@app.post("/api/registry/move")
async def api_registry_move(request: Request):
    """잡(PDF + 변환결과)을 다른 레지스트리 키로 이동."""
    _require_login(request)
    body     = await request.json()
    from_key = body.get("from_key", "").strip()
    to_key   = body.get("to_key", "").strip()
    if not from_key or not to_key:
        raise HTTPException(400, "from_key와 to_key는 필수입니다.")
    _validate_safe_key(from_key)
    _validate_safe_key(to_key)
    if from_key == to_key:
        raise HTTPException(400, "같은 위치입니다.")

    reg = _load_registry()
    if from_key not in reg:
        raise HTTPException(404, f"원본 키에 잡이 없습니다: {from_key}")
    if to_key in reg and reg[to_key].get("job_id"):
        raise HTTPException(409, "대상 위치에 이미 잡이 있습니다. 먼저 삭제하세요.")

    entry = reg.pop(from_key)
    entry["updated_at"] = datetime.now().isoformat(timespec="seconds")
    reg[to_key] = entry
    _save_registry(reg)

    # custom_filename 갱신 (_review.json 파일명 업데이트)
    job_id = entry.get("job_id", "")
    if job_id:
        review_file = _TMP_DIR / f"{job_id}_review.json"
        if review_file.exists():
            try:
                rv = json.loads(review_file.read_text(encoding="utf-8"))
                rv["custom_filename"] = to_key + ".hwpx"
                review_file.write_text(
                    json.dumps(rv, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception:
                pass

    # stages 디렉토리 이동 (업로드된 한글완성본·타이퍼·해설)
    from_stage_dir = _UPLOADS_DIR / from_key
    to_stage_dir   = _UPLOADS_DIR / to_key
    if from_stage_dir.exists():
        import shutil
        if to_stage_dir.exists():
            shutil.rmtree(to_stage_dir)
        shutil.move(str(from_stage_dir), str(to_stage_dir))

    return JSONResponse({"ok": True, "from": from_key, "to": to_key, "entry": entry})


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
    drive_file_id: str = "",
) -> None:
    header, segments = parse_problems(md)
    data = {
        "job_id":          job_id,
        "pdf_name":        original_name,
        "custom_filename": custom_filename,
        "drive_file_id":   drive_file_id,
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
        pdf_path = normalize_pdf_rotation(pdf_path)

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
            pdf_path,
            full_content=full_content,
            correction_patterns=patterns,
            subject=_subject,
        )
        md = correct_latex(md, subject=_subject)
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

        # ── 문제별 crop + 신뢰도 측정 (그림 마커가 있을 때만 = 추가 비용 0) ──
        # BBoxDetector.detect_all()이 Claude API를 쓰므로 그림 문제가 있을 때만 호출.
        prob_crop_map: dict[str, Path] = {}
        conf_map: dict[str, FigureCandidate] = {}
        if figure_items_from_claude:
            try:
                prob_crop_map = crop_problems_by_bbox(
                    pdf_path, figure_items_from_claude, fig_dir
                )
                for _no, _crop in prob_crop_map.items():
                    cand = extract_with_confidence(_crop, _no, fig_dir)
                    if cand is not None:
                        conf_map[_no] = cand
            except Exception as _ce:
                print(f"  [그림] BBox crop·신뢰도 측정 실패 (큐 폴백): {_ce}")

        # ── 그림 검수 큐 자동 등록 ──────────────────────────────────────
        _reg_key = Path(custom_filename).stem if custom_filename else ""
        if _reg_key and figure_items_from_claude:
            try:
                _validate_safe_key(_reg_key)
                _register_figure_queue(
                    _reg_key, job_id, figure_items_from_claude, figure_map,
                    prob_crop_map=prob_crop_map, conf_map=conf_map,
                )
            except HTTPException:
                print(f"  [그림큐] 유효하지 않은 키: {_reg_key!r}")
            except Exception as _qe:
                print(f"  [그림큐] 등록 실패 (무시): {_qe}")

        # ── Google Drive 업로드 ──────────────────────────────────────────
        _drive_file_id = ""
        if custom_filename:
            parts = Path(custom_filename).stem.split("_")
            if len(parts) >= 5:
                _year, _subj = parts[0], parts[4]
                try:
                    _drive_file_id = upload_hwpx(out_hwpx, _year, _subj) or ""
                    if _drive_file_id:
                        print(f"  [Drive] AKP/{_year}/{_subj}/{out_hwpx.name} 저장 완료")
                    elif is_configured():
                        print("  [Drive] 업로드 실패 (계속 진행)")
                except Exception as _e:
                    print(f"  [Drive] 업로드 오류 (무시): {_e}")

        print("  [검수] 문제 파싱 및 검수 데이터 저장 중...")
        _save_review_data(job_id, original_name, md, n_pages, custom_filename, _drive_file_id)

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
