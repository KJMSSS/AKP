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

sys.path.insert(0, str(_ROOT))

from src.ocr.claude_pdf_reader import read_pdf_as_markdown          # noqa: E402
from src.ocr.cost_guard import CostGuard, CostCapError              # noqa: E402
from src.text_only.text_builder import build_from_markdown           # noqa: E402
from src.text_only.ocr_fallback import apply_fallback               # noqa: E402
from src.text_only.problem_segmenter import parse_problems, rebuild_markdown  # noqa: E402
from src.common.image_extractor import extract_images               # noqa: E402
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
        args=(job_id, pdf_dst, file.filename, full, email),
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
    job = _jobs.get(job_id)
    if not job or not job.get("hwpx") or not job["hwpx"].exists():
        raise HTTPException(404)
    hwpx: Path = job["hwpx"]
    return FileResponse(str(hwpx), media_type="application/octet-stream", filename=hwpx.name)


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


# ══════════════════════════════════════════════════════════════════════
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

    body     = await request.json()
    problems = body.get("problems", [])
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
    edited = sum(1 for p in problems if p.get("status") == "edited")
    append_entry({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "pdf": review_data.get("pdf_name", ""),
        "mode": "review", "in_tok": 0, "out_tok": 0,
        "cost_usd": 0, "duration_s": 0, "status": "ok",
        "edited": edited, "token": request.session.get("email", ""),
    })
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


def _save_review_data(job_id: str, original_name: str, md: str, n_pages: int) -> None:
    header, segments = parse_problems(md)
    data = {
        "job_id":   job_id,
        "pdf_name": original_name,
        "header":   header,
        "pages":    n_pages,
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

        md = read_pdf_as_markdown(pdf_path, full_content=full_content)
        md = apply_fallback(md, pdf_path)

        header, segments = parse_problems(md)
        fig_dir = _TMP_DIR / f"{job_id}_figs"
        fig_dir.mkdir(exist_ok=True)
        try:
            figures    = extract_images(pdf_path, fig_dir, dpi=150)
            figure_map = {f.item_no: f for f in figures if f.item_no}
        except Exception as e:
            print(f"  [그림] 감지 실패: {e}")
            figure_map = {}

        md = rebuild_markdown(header, segments, figure_items=set(figure_map) or None)

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

        for item_no, fig in sorted(figure_map.items()):
            try:
                insert_figure_placeholder(out_hwpx, item_no, fig.image_path)
                print(f"  [그림] {item_no}번 삽입 완료")
            except Exception as e:
                print(f"  [그림] {item_no}번 삽입 실패: {e}")

        print("  [검수] 문제 파싱 및 검수 데이터 저장 중...")
        _save_review_data(job_id, original_name, md, n_pages)

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
