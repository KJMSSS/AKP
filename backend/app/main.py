"""
FastAPI 백엔드 — 시험지 변환 API.

흐름:
  1) POST /api/analyze       파일 업로드 -> 백그라운드 분석(OCR+구조화+그림후보)
  2) GET  /api/jobs/{id}     분석 상태/결과(문제수, 문제 JSON, 그림 후보)
  3) GET  /api/jobs/{id}/pages/{idx}.png   페이지 이미지(드래그크롭 UI 표시용)
  4) POST /api/figure/crop   드래그 bbox -> 크롭 이미지 생성
  5) POST /api/figure/erase  지우개 마스크 -> 낙서 인페인팅
  6) POST /api/jobs/{id}/build   (편집된)문제 JSON -> hwpx 다운로드

상태는 인메모리(JOBS) + 작업 디렉토리. 단일 사용자/로컬 우선.
"""
from __future__ import annotations

import threading
import traceback
import uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from config import load_env  # noqa: E402
from pipeline.convert import analyze, analysis_to_problems  # noqa: E402
from pipeline.build_exam import build_exam  # noqa: E402
from pipeline.figure import crop_region, erase_with_mask  # noqa: E402

load_env()

BASE = Path(__file__).resolve().parents[1]
TEMPLATE = str(BASE / "templates" / "base.hwpx")
WORK = BASE / ".work"
WORK.mkdir(exist_ok=True)

app = FastAPI(title="시험지 변환 API")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

JOBS: dict[str, dict] = {}

# --- 작업 상태 지속화 (서버 재시작/--reload 로 인메모리 JOBS 초기화돼도 그림·발문 복구) ---
# 인메모리 JOBS 는 --reload(코드 저장 시 자동 재시작)로 날아간다. 그러면 .work 에 그림
# 파일이 남아 있어도 JOBS 매핑이 사라져 /api/figure/... 가 404(broken image)가 된다.
# → 상태를 .work/{job}/state.json 에 저장하고, 조회 시 JOBS 에 없으면 디스크에서 복구한다.
_PERSIST_KEYS = ("status", "dir", "src", "school", "code", "region", "subject", "exam_range",
                 "page_paths", "problems", "figures", "originals", "kinds", "usage", "pages",
                 "problem_count", "error", "rotations")


def _save_state(job_id: str) -> None:
    import json
    j = _job(job_id)
    if not j:
        return
    st = {k: j[k] for k in _PERSIST_KEYS if k in j}
    try:
        (WORK / job_id / "state.json").write_text(json.dumps(st, ensure_ascii=False))
    except Exception:  # noqa: BLE001 (지속화 실패는 치명적 아님)
        pass


def _rehydrate(job_id: str) -> dict | None:
    """JOBS 가 비었을 때 .work/{job_id} 에서 복구. state.json 우선, 없으면 파일명으로 그림만."""
    import json
    d = WORK / job_id
    if not d.is_dir():
        return None
    sf = d / "state.json"
    if sf.is_file():
        try:
            j = json.loads(sf.read_text())
            JOBS[job_id] = j
            return j
        except Exception:  # noqa: BLE001
            pass
    # 폴백: state.json 이 없던 옛 job — 파일명에서 figures/pages 만이라도 복구(그림 표시용)
    figs: dict[str, str] = {}
    for pat, cut in (("crop_*.png", 5), ("erased_*.png", 7), ("redrawn_*.png", 8)):
        for f in sorted(d.glob(pat)):
            figs[f.stem[cut:]] = str(f)   # crop < erased < redrawn 순으로 덮어써 최신 우선
    pages = [str(p) for p in sorted(d.glob("page-*.png"))]
    if not figs and not pages:
        return None
    j = {"status": "done", "dir": str(d), "page_paths": pages, "figures": figs}
    JOBS[job_id] = j
    return j


def _job(job_id: str) -> dict | None:
    return JOBS.get(job_id) or _rehydrate(job_id)


def _render_pages(job_id: str) -> None:
    """업로드 직후: 페이지 이미지만 렌더(OCR 전). 사용자가 회전을 맞춘 뒤 분석을 돌린다."""
    try:
        j = JOBS[job_id]
        from pipeline.ingest import ingest
        # do_preprocess=False: deskew 는 누운 스캔(841x595 landscape 등)을 minAreaRect 로
        # ~90° 오판해 warpAffine(원본 프레임 유지) 이 이미지를 자른다(2번 선택지 유실 원인).
        # 방향 교정은 아래 수동 회전 단계에서 사용자가 직접 하므로 자동 보정은 끈다.
        pages = ingest(j["src"], work_dir=j["dir"], do_preprocess=False)
        j["_pages"] = pages   # PageImage 객체(내부용) — api_job 직렬화에서 제외됨
        j.update(
            status="preview",
            pages=[{"index": p.index, "width": p.width, "height": p.height} for p in pages],
            page_paths=[p.path for p in pages],
        )
    except Exception as e:  # noqa: BLE001
        JOBS[job_id].update(status="error", error=f"{e}", trace=traceback.format_exc())


def _run_analysis(job_id: str) -> None:
    """분석 전 단계에서 맞춘 회전을 적용한 뒤 OCR/구조화한다."""
    try:
        j = JOBS[job_id]
        pages = j["_pages"]
        rot = j.get("rotations", {}) or {}
        from PIL import Image
        for pg in pages:
            ang = int(rot.get(str(pg.index), 0) or 0) % 360
            if ang:
                with Image.open(pg.path) as im:
                    im.rotate(-ang, expand=True).save(pg.path)   # 시계방향 ang
                with Image.open(pg.path) as im:
                    pg.width, pg.height = im.size
        # 그림은 '사용자가 드래그로 선택'한 것만 넣는다 → 자동 감지/삽입 끔.
        # 구조화(발문/보기/수식/자모)는 정확도가 최우선이라 opus 사용(충실도>비용 방침).
        # detect_figs=False 라 그림/표 감지는 스킵 → 실질 structure_problems_vision 만 opus.
        from pipeline.vision_claude import reset_usage, get_usage
        reset_usage()   # 이 분석 1건의 실측 청구 토큰 측정
        a = analyze(j["src"], pages=pages, school=j.get("school", ""),
                    code=j.get("code", ""), detect_figs=False, auto_figures=False,
                    work_dir=j["dir"], model="claude-opus-4-8")
        _usage = get_usage()
        j.update(
            status="done",
            pages=[{"index": p.index, "width": p.width, "height": p.height} for p in a.pages],
            page_paths=[p.path for p in a.pages],
            problems=analysis_to_problems(a),
            usage=_usage,
            figure_candidates=[], auto_figures=[], figure_warnings=[],
        )
        _save_state(job_id)
    except Exception as e:  # noqa: BLE001
        JOBS[job_id].update(status="error", error=f"{e}", trace=traceback.format_exc())


@app.post("/api/analyze")
async def api_analyze(file: UploadFile = File(...), school: str = Form(""), code: str = Form(""),
                      region: str = Form(""), subject: str = Form(""), exam_range: str = Form("")):
    job_id = uuid.uuid4().hex[:12]
    jdir = WORK / job_id
    jdir.mkdir(parents=True, exist_ok=True)
    src = jdir / file.filename
    src.write_bytes(await file.read())
    JOBS[job_id] = {"status": "rendering", "dir": str(jdir), "src": str(src),
                    "school": school, "code": code, "region": region,
                    "subject": subject, "exam_range": exam_range, "rotations": {}}
    threading.Thread(target=_render_pages, args=(job_id,), daemon=True).start()
    return {"job_id": job_id, "status": "rendering"}


@app.post("/api/jobs/{job_id}/run")
def api_run(job_id: str, payload: dict = Body(default={})):
    """분석 전 회전 단계에서 정한 각도를 받아 OCR/구조화를 시작한다.

    payload: {"rotations": {"0": 90, "1": 0, ...}}  (페이지index → 시계방향 각도)
    """
    j = _job(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    if "_pages" not in j:
        raise HTTPException(409, "아직 페이지 렌더가 끝나지 않았습니다.")
    j["rotations"] = (payload or {}).get("rotations", {}) or {}
    j["status"] = "processing"
    threading.Thread(target=_run_analysis, args=(job_id,), daemon=True).start()
    return {"status": "processing"}


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str):
    j = _job(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    out = {k: v for k, v in j.items()
           if not k.startswith("_") and k not in ("page_paths", "trace")}
    if j.get("status") == "done":
        out["problem_count"] = len(j.get("problems", []))
    return out


@app.get("/api/jobs/{job_id}/pages/{idx}.png")
def api_page(job_id: str, idx: int):
    j = _job(job_id)
    if not j or "page_paths" not in j or idx >= len(j["page_paths"]):
        raise HTTPException(404, "page not found")
    return FileResponse(j["page_paths"][idx], media_type="image/png")


@app.post("/api/figure/crop")
def api_crop(job_id: str = Body(...), page: int = Body(...), bbox: list = Body(...)):
    j = _job(job_id)
    if not j or "page_paths" not in j:
        raise HTTPException(404, "job not found")
    fid = uuid.uuid4().hex[:8]
    out = str(Path(j["dir"]) / f"crop_{fid}.png")
    r = crop_region(j["page_paths"][page], bbox, out, pad=0.01)
    j.setdefault("figures", {})[fid] = out
    _save_state(job_id)
    return {"figure_id": fid, "width": r.width, "height": r.height}


@app.post("/api/figure/erase")
async def api_erase(job_id: str = Form(...), figure_id: str = Form(...), mask: UploadFile = File(...)):
    j = _job(job_id)
    if not j or figure_id not in j.get("figures", {}):
        raise HTTPException(404, "figure not found")
    src = j["figures"][figure_id]
    mask_path = str(Path(j["dir"]) / f"mask_{figure_id}.png")
    Path(mask_path).write_bytes(await mask.read())
    out = str(Path(j["dir"]) / f"erased_{figure_id}.png")
    erase_with_mask(src, mask_path, out)
    j["figures"][figure_id] = out
    _save_state(job_id)
    return {"figure_id": figure_id, "status": "erased"}

@app.post("/api/figure/redraw")
def api_redraw(job_id: str = Body(...), figure_id: str = Body(...), pro: bool = Body(False),
               kind: str = Body("figure")):
    """크롭 영역 재작도(Gemini image-to-image).

    kind="figure"(기본) 도형 재작도 / kind="table" 표(격자·실선) 재현.
    기본=저비용 3.1 Flash, pro=True 면 '다시 생성'으로 3-pro-image(고품질).
    폴백: 구조 스펙 → matplotlib (도형 한정, 표는 폴백 없이 에러).
    """
    j = _job(job_id)
    if not j or figure_id not in j.get("figures", {}):
        raise HTTPException(404, "figure not found")
    src = j["figures"][figure_id]
    # 재작도 전 이미지를 '원본 비교용'으로 보존(최초 1회) — 숫자/라벨 변조 검수용
    orig = j.setdefault("originals", {}).setdefault(figure_id, src)
    # 재작도 입력은 항상 '재작도 전' 이미지로 — 재작도본을 다시 입력하면 직전 시도의
    # 환각(없는 글자 등)이 다음 결과로 누적된다('Pro 로 다시 생성'이 이 경로였음).
    if Path(src).name.startswith("redrawn_"):
        src = orig
    out = str(Path(j["dir"]) / f"redrawn_{figure_id}.png")
    OPUS = "claude-opus-4-8"   # 구조 추출·검증은 가장 똑똑한 모델로(정확도 우선)

    # 1순위: Gemini image-to-image. kind 로 도형/표 프롬프트, 기본=Flash·pro=Pro
    from pipeline.redraw_gemini import (redraw_with_gemini, GeminiError,
                                        DEFAULT_IMAGE_MODEL, PRO_IMAGE_MODEL,
                                        REDRAW_TABLE_INSTRUCTION)
    model = PRO_IMAGE_MODEL if pro else DEFAULT_IMAGE_MODEL
    instr = REDRAW_TABLE_INSTRUCTION if kind == "table" else None
    gem_err = None
    try:
        path = redraw_with_gemini(src, out, model=model, instruction=instr)
        # 재작도 검증(Claude 비전): 라벨 변조·누락 + '원본에 없는 글자/범례 추가'(환각) 탈락.
        # 2026-07-02 실사고: 크롭 가장자리 자국을 'ㄱㄴㄷ…/ABC…' 글자표로 복원해 삽입 →
        # 검증 없이는 그대로 시험지에 들어간다. 반려 시 지적사항을 넣어 1회 재시도.
        from pipeline.vision_claude import verify_redraw
        verdict = None
        try:
            verdict = verify_redraw(orig, path, model=OPUS)
            if not verdict.get("ok", False):
                path = redraw_with_gemini(src, out, model=model, instruction=instr,
                                          feedback=verdict.get("issues"))
                verdict = verify_redraw(orig, path, model=OPUS)
        except GeminiError:
            raise
        except Exception:  # noqa: BLE001 — 검증 자체 실패(키/네트워크)면 미검증 수용
            verdict = None
        if verdict is not None and not verdict.get("ok", False):
            # 반려: 잘못된 재작도를 채택하지 않는다(그림은 재작도 전 상태 유지).
            # 파일은 rejected_ 로 보존(디버깅용, 파일명 글롭 복구에 안 걸리게 개명).
            import os
            os.replace(out, str(Path(j["dir"]) / f"rejected_{figure_id}.png"))
            _save_state(job_id)
            issues = " / ".join(map(str, verdict.get("issues", [])[:3])) or "원본과 불일치"
            raise HTTPException(422, f"재작도 반려(원본 유지) — 검증에서 차이 발견: {issues} "
                                     f"— 다시 시도하거나 '낙서 지우기'한 원본을 그대로 쓰세요.")
        j["figures"][figure_id] = path
        j.setdefault("kinds", {})[figure_id] = kind   # build 에서 표는 단 폭으로 삽입
        _save_state(job_id)
        return {"figure_id": figure_id, "status": "redrawn", "method": "gemini",
                "model": model, "pro": pro, "kind": kind,
                "verified": bool(verdict), "score": (verdict or {}).get("score"),
                "issues": (verdict or {}).get("issues", [])}
    except GeminiError as e:        # 키 미설정/안전차단 등 → 폴백
        gem_err = str(e)

    # 표는 구조스펙/matplotlib 폴백이 부적합 → Gemini 실패 시 에러로
    if kind == "table":
        raise HTTPException(422, f"표 재현 실패(Gemini): {gem_err}")

    # 폴백: 구조 스펙 → matplotlib 코드 재작도(검증 1회)
    from pipeline.figure_ai import redraw_via_spec, redraw_figure_verified, RedrawError
    OPUS = "claude-opus-4-8"   # 구조 추출은 가장 똑똑한 모델로(정확도 우선)
    try:
        path, spec = redraw_via_spec(src, out, model=OPUS)
        j["figures"][figure_id] = path
        _save_state(job_id)
        return {"figure_id": figure_id, "status": "redrawn", "method": "spec", "gemini_error": gem_err}
    except Exception:
        pass
    try:
        path, verdict = redraw_figure_verified(src, out, max_retries=1, model=OPUS)
    except RedrawError as e:
        raise HTTPException(422, f"재작도 실패: {e} (gemini: {gem_err})")
    j["figures"][figure_id] = path
    _save_state(job_id)
    return {"figure_id": figure_id, "status": "redrawn", "method": "code",
            "score": verdict.get("score"), "ok": verdict.get("ok"),
            "issues": verdict.get("issues", []), "gemini_error": gem_err}


@app.get("/api/figure/{job_id}/{figure_id}.png")
def api_figure_img(job_id: str, figure_id: str):
    j = _job(job_id)
    if not j or figure_id not in j.get("figures", {}):
        raise HTTPException(404, "figure not found")
    return FileResponse(j["figures"][figure_id], media_type="image/png")


@app.get("/api/figure/{job_id}/{figure_id}/original.png")
def api_figure_original(job_id: str, figure_id: str):
    """재작도 전 원본(크롭/낙서지움) 이미지 — 검수 UI 의 '원본↔재작도' 비교용.

    재작도 전이면 '원본' = 현재 그림(크롭/지움본) 그 자체 → 404 대신 그걸 준다.
    (2026-07-03: 배치 재작도 뒤 새로 크롭한 카드가 비교 패널을 열어 404 → 깨진 이미지.)
    """
    j = _job(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    orig = j.get("originals", {}).get(figure_id) or j.get("figures", {}).get(figure_id)
    if not orig or not Path(orig).exists():
        orig = j.get("figures", {}).get(figure_id)
    if not orig or not Path(orig).exists():
        raise HTTPException(404, "original not found")
    return FileResponse(orig, media_type="image/png")


@app.post("/api/jobs/{job_id}/build")
def api_build(job_id: str, payload: dict = Body(...)):
    """payload: {problems:[...], figures:[{figure_id, problem_index}], header:{...}}"""
    j = _job(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    import copy as _copy
    problems = _copy.deepcopy(payload.get("problems") or j.get("problems", []))
    # UI 갤러리(자동+수동)가 그림의 단일 출처 → 분석에서 붙은 figures 는 비우고
    # payload.figures(figure_id) 만 해당 문제에 매핑(이중삽입 방지)
    fig_map = j.get("figures", {})
    payload_figs = payload.get("figures")
    if payload_figs is not None:
        for p in problems:
            p["figures"] = []
        kinds = j.get("kinds", {})
        for f in payload_figs:
            idx, fid = f.get("problem_index"), f.get("figure_id")
            if fid in fig_map and isinstance(idx, int) and 0 <= idx < len(problems):
                # 표는 단 폭 가득(≈108mm), 그림은 기본 폭. (kind 는 재작도 시 기록됨)
                if kinds.get(fid) == "table":
                    item = {"path": fig_map[fid], "width_mm": 108.0, "max_height_mm": 200.0,
                            "kind": "table"}
                    # 표 내용이 발문에 텍스트로 중복되면 Claude 로 의미 대조 제거(패턴 무관:
                    # (가)/(나)·a,b·①② 등 어떤 표 형식이든). 표 내용 파악엔 재작도 전 원본
                    # 크롭이 정확. 실패하면 원본 발문 유지(build_exam 패턴 폴백이 추가로 동작).
                    tbl_img = j.get("originals", {}).get(fid) or fig_map[fid]
                    try:
                        from pipeline.vision_claude import clean_stem_against_table
                        problems[idx]["stem"] = clean_stem_against_table(
                            problems[idx].get("stem", []), tbl_img, model="claude-opus-4-8")
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    item = fig_map[fid]
                problems[idx].setdefault("figures", []).append(item)
    out = str(Path(j["dir"]) / "result.hwpx")
    # 문제 간 간격(빈 줄 수). 기본 20(2026-07-08 15→20 상향; 짧은 객관식 상단 몰림 완화), payload 로 조절 가능(UI 슬라이더 대비).
    gap = payload.get("gap_lines")
    gap = int(gap) if isinstance(gap, (int, float, str)) and str(gap).isdigit() else 20
    # 1페이지 상단 헤더 표(과목/범위/지역/학교/학기): 업로드 때 받은 값으로 채운다.
    # 빈 값은 템플릿 텍스트 유지. payload.header 가 오면 그 값이 우선(향후 UI 수정 대비).
    header = {"school": j.get("school", ""), "region": j.get("region", ""),
              "subject": j.get("subject", ""), "exam_range": j.get("exam_range", ""),
              "term": j.get("code", "")}
    header.update(payload.get("header") or {})
    rep = build_exam(TEMPLATE, problems, out, header=header, gap_lines=gap)
    j["result"] = out
    return {"status": "ok", "problems": rep.problems,
            "equations_ok": rep.equations_ok, "equations_fallback": rep.equations_fallback,
            "figures_inserted": rep.figures_inserted,
            "fallbacks": rep.fallbacks, "download": f"/api/jobs/{job_id}/download"}


@app.get("/api/jobs/{job_id}/download")
def api_download(job_id: str):
    j = _job(job_id)
    if not j or "result" not in j:
        raise HTTPException(404, "result not ready")
    return FileResponse(j["result"],
                        media_type="application/octet-stream",
                        filename="변환_시험지.hwpx")


@app.get("/api/health")
def health():
    import os
    return {"ok": True,
            "mathpix": bool(os.environ.get("MATHPIX_APP_ID")),
            "claude": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "gemini": bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))}

# 프런트엔드(정적) — 모든 API 라우트 뒤에 마운트
from fastapi.staticfiles import StaticFiles  # noqa: E402


class _NoCacheStatic(StaticFiles):
    """개발 중 프론트 변경(index.html)이 새로고침만으로 바로 반영되도록 캐시를 끈다.
    (StaticFiles 기본은 etag/last-modified 라 브라우저가 304 로 옛 화면을 보여준다.)"""
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        return resp


_FRONTEND = BASE.parent / "frontend" / "dist"
if _FRONTEND.exists():
    app.mount("/", _NoCacheStatic(directory=str(_FRONTEND), html=True), name="ui")
