"""
AKP 웹 변환 서버 — FastAPI + SSE + 검수

실행:
    py -m uvicorn scripts.web.app:app --host 0.0.0.0 --port 8000

접속:
    내 PC  : http://localhost:8000
    학원 PC: http://[집IP]:8000
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
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

# ── 경로 설정 ──────────────────────────────────────────────────────────
_HERE    = Path(__file__).resolve().parent
_ROOT    = _HERE.parent.parent

# Railway 등 클라우드에서는 /tmp 사용, 로컬은 scripts/web/tmp/
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
from scripts.web.tokens import (                                    # noqa: E402
    validate as validate_token, token_today_cost,
    all_token_stats, create_token, revoke_token, activate_token,
    delete_token, update_cap, ADMIN_PASSWORD,
)
from scripts.web.corrections_log import (                           # noqa: E402
    append_correction, read_corrections, revert_correction,
    corrections_summary,
)

# ── 템플릿 HWPX (header.xml 폰트 참조용) ─────────────────────────────
_SAMPLES = _ROOT / "samples"
_TEMPLATE = next(
    (f for f in _SAMPLES.glob("*.hwpx") if "워드초벌" in f.name and "]1." not in f.name),
    next(_SAMPLES.glob("*.hwpx"), None),
)

# ── 진행 중인 job 저장소 ───────────────────────────────────────────────
_jobs: dict[str, dict] = {}

app = FastAPI(title="AKP 변환기")
app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")


# ══════════════════════════════════════════════════════════════════════
# 기본 라우트
# ══════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse((_HERE / "static" / "index.html").read_text(encoding="utf-8"))


@app.get("/api/usage")
async def api_usage():
    return JSONResponse({"summary": today_summary(), "recent": read_entries(days=7)[:10]})


@app.post("/convert")
async def convert(
    file: UploadFile = File(...),
    full_content: str = Form("false"),
    token: str = Form(""),
):
    """PDF 업로드 → job_id 반환. 백그라운드에서 변환 시작."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "PDF 파일만 업로드 가능합니다.")

    # 토큰 검증
    name, info = validate_token(token)
    if name is None:
        raise HTTPException(401, "유효하지 않은 토큰입니다. 학원장에게 문의하세요.")

    # 토큰별 한도 체크 (cap=0 이면 무제한)
    today_cost = token_today_cost(name)
    cap = info.get("cap_usd", DAILY_CAP_USD)
    if cap > 0 and today_cost >= cap:
        raise HTTPException(429, f"오늘 사용 한도 ${cap:.2f}에 도달했습니다 (현재 ${today_cost:.2f}). 학원장에게 문의하세요.")

    job_id  = uuid.uuid4().hex[:12]
    pdf_dst = _TMP_DIR / f"{job_id}.pdf"
    pdf_dst.write_bytes(await file.read())

    q: queue.Queue[str | None] = queue.Queue()
    _jobs[job_id] = {"queue": q, "hwpx": None, "meta": {}}

    full = full_content.lower() in ("true", "1", "yes")
    threading.Thread(
        target=_run_conversion,
        args=(job_id, pdf_dst, file.filename, full, name),
        daemon=True,
    ).start()
    return JSONResponse({"job_id": job_id})


@app.get("/stream/{job_id}")
async def stream(job_id: str):
    """SSE 실시간 로그 스트림."""
    if job_id not in _jobs:
        raise HTTPException(404, "job을 찾을 수 없습니다.")

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
                f"data: {json.dumps({'file': fname, 'cost': meta.get('cost_usd', 0), 'tok_in': meta.get('in_tok', 0), 'tok_out': meta.get('out_tok', 0)})}\n\n"
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
        raise HTTPException(404, "파일을 찾을 수 없습니다.")
    hwpx: Path = job["hwpx"]
    return FileResponse(str(hwpx), media_type="application/octet-stream", filename=hwpx.name)


# ══════════════════════════════════════════════════════════════════════
# 검수 라우트
# ══════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════
# 관리자 라우트
# ══════════════════════════════════════════════════════════════════════

@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    return HTMLResponse((_HERE / "static" / "admin.html").read_text(encoding="utf-8"))


def _check_admin(password: str) -> None:
    if password != ADMIN_PASSWORD:
        raise HTTPException(403, "관리자 비밀번호가 틀렸습니다.")


@app.get("/api/admin/tokens")
async def api_admin_tokens(password: str = ""):
    _check_admin(password)
    return JSONResponse(all_token_stats())


@app.post("/api/admin/tokens")
async def api_create_token(request: Request):
    body = await request.json()
    _check_admin(body.get("password", ""))
    name    = body.get("name", "").strip()
    cap_usd = float(body.get("cap_usd", 2.0))
    if not name:
        raise HTTPException(400, "이름을 입력하세요.")
    token = create_token(name, cap_usd)
    return JSONResponse({"name": name, "token": token, "cap_usd": cap_usd})


@app.patch("/api/admin/tokens/{name}")
async def api_update_token(name: str, request: Request):
    body = await request.json()
    _check_admin(body.get("password", ""))
    action = body.get("action", "")
    if action == "revoke":
        revoke_token(name)
    elif action == "activate":
        activate_token(name)
    elif action == "delete":
        delete_token(name)
    elif action == "cap":
        update_cap(name, float(body.get("cap_usd", 2.0)))
    else:
        raise HTTPException(400, f"알 수 없는 액션: {action}")
    return JSONResponse({"ok": True})


@app.post("/api/review/{job_id}/corrections")
async def save_corrections(job_id: str, request: Request):
    """직원이 표시한 오류 목록을 수정 로그에 저장."""
    body = await request.json()
    token     = body.get("token", "")
    name, _   = validate_token(token)
    employee  = name or "알 수 없음"

    review_file = _TMP_DIR / f"{job_id}_review.json"
    pdf_name = ""
    if review_file.exists():
        try:
            pdf_name = json.loads(review_file.read_text(encoding="utf-8")).get("pdf_name", "")
        except Exception:
            pass

    corrections: list[dict] = body.get("corrections", [])
    saved_ids = []
    for c in corrections:
        cid = append_correction({
            "employee":       employee,
            "job_id":         job_id,
            "pdf_name":       pdf_name,
            "problem_number": c.get("problem_number"),
            "problem_text":   c.get("problem_text", ""),
            "correction_note": c.get("correction_note", ""),
            "corrected_text": c.get("corrected_text", ""),
        })
        saved_ids.append(cid)

    return JSONResponse({"saved": len(saved_ids), "ids": saved_ids})


@app.get("/api/admin/corrections")
async def api_corrections(password: str = "", days: int = 30):
    _check_admin(password)
    entries  = read_corrections(days=days)
    summary  = corrections_summary(days=7)
    return JSONResponse({"corrections": entries, "summary": summary})


@app.patch("/api/admin/corrections/{cid}/revert")
async def api_revert_correction(cid: str, request: Request):
    body = await request.json()
    _check_admin(body.get("password", ""))
    ok = revert_correction(cid)
    if not ok:
        raise HTTPException(404, "해당 수정 항목을 찾을 수 없습니다.")
    return JSONResponse({"ok": True})


@app.get("/review/{job_id}", response_class=HTMLResponse)
async def review_page(job_id: str):
    """검수 전용 페이지."""
    review_file = _TMP_DIR / f"{job_id}_review.json"
    if not review_file.exists():
        raise HTTPException(404, "검수 데이터가 없습니다. 먼저 변환을 실행하세요.")
    return HTMLResponse((_HERE / "static" / "review.html").read_text(encoding="utf-8"))


@app.get("/api/review/{job_id}")
async def api_review(job_id: str):
    """검수 데이터 JSON 반환."""
    review_file = _TMP_DIR / f"{job_id}_review.json"
    if not review_file.exists():
        raise HTTPException(404, "검수 데이터가 없습니다.")
    return JSONResponse(json.loads(review_file.read_text(encoding="utf-8")))


@app.get("/page/{job_id}/{page_num}")
async def page_image(job_id: str, page_num: int):
    """PDF 페이지 이미지 반환."""
    img = _TMP_DIR / f"{job_id}_page{page_num}.png"
    if not img.exists():
        raise HTTPException(404, f"{page_num}페이지 이미지가 없습니다.")
    return FileResponse(str(img), media_type="image/png")


@app.post("/api/review/{job_id}/submit")
async def review_submit(job_id: str, request: Request):
    """수정된 문제 텍스트로 HWPX 재빌드 → 다운로드 URL 반환."""
    review_file = _TMP_DIR / f"{job_id}_review.json"
    if not review_file.exists():
        raise HTTPException(404, "검수 데이터가 없습니다.")

    body = await request.json()
    problems: list[dict] = body.get("problems", [])
    if not problems:
        raise HTTPException(400, "문제 데이터가 없습니다.")

    # 수정된 텍스트로 마크다운 재조립
    review_data = json.loads(review_file.read_text(encoding="utf-8"))
    header = review_data.get("header", "")
    edited_blocks = [p["full_text"] for p in problems]
    new_md = header + "\n\n" + "\n\n".join(edited_blocks)

    # HWPX 재빌드
    reviewed_id  = f"{job_id}_reviewed"
    out_hwpx     = _TMP_DIR / f"{reviewed_id}.hwpx"

    try:
        if not _TEMPLATE:
            raise RuntimeError("템플릿 HWPX가 없습니다.")
        build_from_markdown(new_md, out_hwpx, _TEMPLATE)
        replace_condition_tables(out_hwpx)
        replace_boilerplate_tables(out_hwpx)
        fix_hwpx_namespaces(str(out_hwpx))
        errs = validate_hwpx(str(out_hwpx))
        if errs:
            raise RuntimeError(f"HWPX 검증 실패: {errs[0]}")
    except Exception as e:
        raise HTTPException(500, str(e))

    # 수정 건수 계산
    edited_count = sum(1 for p in problems if p.get("status") == "edited")

    # 검수 결과 로그
    append_entry({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "pdf": review_data.get("pdf_name", ""),
        "mode": "review",
        "in_tok": 0, "out_tok": 0, "cost_usd": 0,
        "duration_s": 0,
        "status": "ok",
        "edited": edited_count,
    })

    # job에 검수본 HWPX 등록
    if job_id not in _jobs:
        _jobs[job_id] = {"queue": queue.Queue(), "hwpx": None, "meta": {}}
    _jobs[reviewed_id] = {"queue": queue.Queue(), "hwpx": out_hwpx, "meta": {}}

    return JSONResponse({
        "download_url": f"/download/{reviewed_id}",
        "filename": out_hwpx.name,
        "edited": edited_count,
    })


# ══════════════════════════════════════════════════════════════════════
# 변환 워커 (백그라운드 스레드)
# ══════════════════════════════════════════════════════════════════════

class _QueueWriter(io.TextIOBase):
    """sys.stdout → Queue 리다이렉트."""
    def __init__(self, q: "queue.Queue[str | None]"):
        self._q = q

    def write(self, s: str) -> int:
        for line in s.splitlines():
            line = line.strip()
            if line:
                self._q.put(f"data: {line}\n\n")
        return len(s)

    def flush(self):
        pass


def _render_pdf_pages(pdf_path: Path, job_id: str) -> int:
    """PDF 전 페이지를 150 DPI PNG로 저장. 페이지 수 반환."""
    doc = fitz.open(str(pdf_path))
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=150)
        pix.save(str(_TMP_DIR / f"{job_id}_page{i}.png"))
    n = doc.page_count
    doc.close()
    return n


def _save_review_data(job_id: str, original_name: str, md: str, n_pages: int) -> None:
    """변환된 마크다운을 문제 단위로 파싱해 검수 JSON 저장."""
    header, segments = parse_problems(md)
    data = {
        "job_id":   job_id,
        "pdf_name": original_name,
        "header":   header,
        "pages":    n_pages,
        "problems": [
            {
                "number":       seg.number,
                "full_text":    seg.raw_block,
                "is_subjective": seg.is_subjective,
                "status":       "pending",
            }
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
    token_name: str = "",
) -> None:
    q    = _jobs[job_id]["queue"]
    meta = _jobs[job_id]["meta"]

    orig_stdout = sys.stdout
    sys.stdout  = _QueueWriter(q)

    ts_start = datetime.now().isoformat(timespec="seconds")
    t0       = time.time()

    try:
        # 일일 한도 체크
        guard = CostGuard(cap_usd=DAILY_CAP_USD)
        guard.check_or_raise("web")

        # PDF 페이지 렌더링 (삭제 전에 수행)
        print("  [검수] PDF 페이지 렌더링 중...")
        n_pages = _render_pdf_pages(pdf_path, job_id)

        cost_before = guard.total_today()

        # ── 변환 파이프라인 ──
        md = read_pdf_as_markdown(pdf_path, full_content=full_content)
        md = apply_fallback(md, pdf_path)

        # ① 문제 파싱 + 그림 감지 (PDF 삭제 전)
        header, segments = parse_problems(md)
        fig_dir = _TMP_DIR / f"{job_id}_figs"
        fig_dir.mkdir(exist_ok=True)
        try:
            figures   = extract_images(pdf_path, fig_dir, dpi=150)
            figure_map = {f.item_no: f for f in figures if f.item_no}
        except Exception as e:
            print(f"  [그림] 감지 실패 (무시): {e}")
            figure_map = {}

        # ② 마커 삽입 (조건·보기·그림)
        md = rebuild_markdown(header, segments,
                              figure_items=set(figure_map) or None)

        out_hwpx = _TMP_DIR / f"{job_id}.hwpx"
        if not _TEMPLATE:
            raise RuntimeError("samples/ 폴더에 .hwpx 파일이 없습니다.")

        # ③ HWPX 빌드
        build_from_markdown(md, out_hwpx, _TEMPLATE)
        replace_condition_tables(out_hwpx)
        replace_boilerplate_tables(out_hwpx)
        fix_hwpx_namespaces(str(out_hwpx))
        errs = validate_hwpx(str(out_hwpx))
        if errs:
            raise RuntimeError(f"HWPX 검증 실패: {errs[0]}")

        # ④ 그림 삽입
        for item_no, fig in sorted(figure_map.items()):
            try:
                insert_figure_placeholder(out_hwpx, item_no, fig.image_path)
                print(f"  [그림] {item_no}번 삽입 완료")
            except Exception as e:
                print(f"  [그림] {item_no}번 삽입 실패: {e}")

        # 검수 데이터 저장
        print("  [검수] 문제 파싱 및 검수 데이터 저장 중...")
        _save_review_data(job_id, original_name, md, n_pages)

        duration     = round(time.time() - t0, 1)
        cost_usd     = round(guard.total_today() - cost_before, 4)

        append_entry({
            "ts": ts_start, "pdf": original_name,
            "mode": "full" if full_content else "questions",
            "in_tok": 0, "out_tok": 0,
            "cost_usd": cost_usd, "duration_s": duration,
            "status": "ok", "token": token_name,
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
            "status": "cap_exceeded", "token": token_name,
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
            "status": "error", "token": token_name,
        })
        meta["error"] = str(e)

    finally:
        sys.stdout = orig_stdout
        try:
            pdf_path.unlink(missing_ok=True)
        except Exception:
            pass
        q.put(None)
