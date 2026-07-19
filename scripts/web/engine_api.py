"""examconv 엔진 API 라우터 — AKP 웹 셸 통합판.

원본: /Users/joinsig/Desktop/test/examconv/backend/app/main.py (2026-07-05 기준)
흐름:
  1) POST /api/analyze            업로드 → 페이지 렌더(무과금)
  2) POST /api/jobs/{id}/run      회전 적용 → Claude 비전 구조화(과금)
  3) GET  /api/jobs/{id}          상태/결과
  4) POST /api/figure/crop|erase|redraw   그림 선택·낙서 지우개·Gemini 재작도
  5) POST /api/jobs/{id}/build    HWPX 빌드 → Drive 업로드 + 레지스트리 갱신
  6) GET  /api/jobs/{id}/download 다운로드

AKP 통합 사항(원본과의 차이):
  - 모든 라우트 로그인 필수 (scripts.web.auth)
  - 작업 디렉터리 = DATA_DIR/work/{job_id} (store.WORK_DIR)
  - 업로드 시 registry_key(매트릭스 셀)를 받아 레지스트리에 등록,
    빌드 완료 시 Google Drive 업로드 + 상태 done 갱신
  - Claude 사용량 → usage.jsonl 로깅(일일 비용 캡 검사 포함)
  - 3일 지난 작업 폴더 자동 정리 (cleanup_old_jobs)
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from scripts.web.auth import require_login
from scripts.web.gdrive_uploader import delete_file as drive_delete_file
from scripts.web.gdrive_uploader import last_error as drive_last_error
from scripts.web.gdrive_uploader import upload_hwpx
from scripts.web.store import (
    WORK_DIR,
    load_registry,
    save_registry,
    update_registry_entry,
    validate_safe_key,
)
from scripts.web.usage_log import PROVIDER_LABEL, append_entry, user_provider_today_cost
from scripts.web.users import get_user, is_admin, user_cap

# ── 엔진 임포트 (backend/ 를 sys.path 에 추가 — examconv 코드 무수정 사용) ──
_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from pipeline.build_exam import build_exam          # noqa: E402
from pipeline.convert import analyze, analysis_to_problems  # noqa: E402
from pipeline.figure import crop_region, erase_with_mask    # noqa: E402

TEMPLATE = str(_BACKEND / "templates" / "base.hwpx")
WORK = WORK_DIR

router = APIRouter()

JOBS: dict[str, dict] = {}

# ── Claude 비용 계산 (claude-opus-4-8, USD/MTok: in 5.00 / out 25.00 /
#    cache-read 0.50 / cache-write 6.25) ─────────────────────────────────
_PRICE = {"input_tokens": 5.00, "output_tokens": 25.00,
          "cache_read_input_tokens": 0.50, "cache_creation_input_tokens": 6.25}


def _usage_cost(u: dict) -> float:
    return round(sum(int(u.get(k, 0) or 0) * p for k, p in _PRICE.items()) / 1e6, 4)


# ── Gemini 이미지 재작도 비용 (이미지 모델은 '장당' 과금) ─────────────────
#    Flash(Nano Banana 2)·Pro(Nano Banana Pro)의 장당 단가. 실제 청구는 토큰
#    기반이지만 이미지→토큰 매핑이 흐릿해, 장당 추정 단가가 학원장에게 더
#    직관적이라 이 방식을 쓴다(env 로 조정 가능). Claude 는 토큰 정산, Gemini 는
#    장당 추정임을 관리자 UI 가 명시한다.
def _gem_price(name: str, default: str) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


_GEMINI_PRICE_FLASH = _gem_price("GEMINI_IMAGE_PRICE", "0.04")      # USD/이미지
_GEMINI_PRICE_PRO = _gem_price("GEMINI_PRO_IMAGE_PRICE", "0.14")    # USD/이미지(고품질)


# vision_claude._USAGE_LOG 는 전역 누적이다. 동시 요청(배치 재작도 등)에서
# reset_usage() 를 쓰면 서로의 측정을 지운다 → 전역 소비 인덱스로 "내가 아직
# 집계하지 않은 구간"만 drain 한다. 요청 간 귀속은 근사치지만 총합은 정확하다.
_usage_ix_lock = threading.Lock()
_usage_consumed = 0

# Gemini 사용량도 동일 패턴(엔진 append → 웹셸 drain)으로 인덱스 관리한다.
_gemini_ix_lock = threading.Lock()
_gemini_consumed = 0


def _drain_usage() -> dict:
    global _usage_consumed
    from pipeline.vision_claude import _USAGE_LOG
    with _usage_ix_lock:
        new = list(_USAGE_LOG[_usage_consumed:])
        # 집계 완료분은 리스트에서 제거(무한 증가 방지). append 는 뒤에만 붙으므로
        # 앞부분 del 은 동시 append 와 충돌하지 않는다. reset_usage() 는 이 프로세스
        # 어디서도 호출 금지(인덱스 불변식 깨짐 — 2026-07-06 QA).
        del _USAGE_LOG[:_usage_consumed + len(new)]
        _usage_consumed = 0
    out = {"calls": len(new)}
    for k in _PRICE:
        out[k] = sum(int(u.get(k, 0) or 0) for u in new)
    return out


def _log_usage(email: str, pdf: str, mode: str, usage: dict,
               duration_s: float = 0.0, status: str = "ok") -> float:
    """usage.jsonl 에 Claude 사용 1건 기록. cost 반환."""
    cost = _usage_cost(usage)
    if usage.get("calls", 0) == 0 and cost == 0:
        return 0.0
    append_entry({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "token": email, "pdf": pdf, "mode": mode, "provider": "claude",
        "in_tok": usage.get("input_tokens", 0), "out_tok": usage.get("output_tokens", 0),
        "cache_read_tok": usage.get("cache_read_input_tokens", 0),
        "cost_usd": cost, "duration_s": round(duration_s, 1), "status": status,
    })
    return cost


def _drain_gemini_usage() -> dict:
    """엔진의 Gemini 이미지 생성 로그에서 미집계분만 뽑아 장수·비용으로 환산."""
    global _gemini_consumed
    from pipeline.redraw_gemini import _GEMINI_USAGE_LOG, PRO_IMAGE_MODEL
    with _gemini_ix_lock:
        new = list(_GEMINI_USAGE_LOG[_gemini_consumed:])
        del _GEMINI_USAGE_LOG[:_gemini_consumed + len(new)]
        _gemini_consumed = 0
    pro = sum(int(u.get("images", 0) or 0) for u in new if u.get("model") == PRO_IMAGE_MODEL)
    flash = sum(int(u.get("images", 0) or 0) for u in new) - pro
    cost = round(flash * _GEMINI_PRICE_FLASH + pro * _GEMINI_PRICE_PRO, 4)
    return {"images": flash + pro, "flash_images": flash, "pro_images": pro, "cost_usd": cost}


def _log_gemini_usage(email: str, pdf: str, status: str = "ok") -> float:
    """usage.jsonl 에 Gemini 재작도(이미지 생성) 1건 기록. cost 반환.

    생성된 이미지가 없으면(=키 미설정·초기 실패 등 Gemini 호출 자체가 없던 경우)
    기록하지 않는다."""
    g = _drain_gemini_usage()
    if g["images"] == 0:
        return 0.0
    append_entry({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "token": email, "pdf": pdf, "mode": "redraw", "provider": "gemini",
        "images": g["images"], "flash_images": g["flash_images"],
        "pro_images": g["pro_images"],
        "cost_usd": g["cost_usd"], "status": status,
    })
    return g["cost_usd"]


def _check_cost_cap(email: str, need: tuple[str, ...] = ("claude",)) -> None:
    """직원별 provider 한도 검사. 초과 시 429.

    관리자(학원장)는 전면 면제한다 — 검사 자체를 건너뛴다(2026-07-13 학원장 결정:
    본인 작업이 한도에 걸리면 안 됨). 한도는 직원 계정에만, provider별로 적용한다
    (2026-07-13: 글로벌 키 한도 폐기 → 직원별 Claude/Gemini 한도. 한 명이 많이
    써도 다른 직원은 안 막힘). 이 요청이 쓸 provider(need)의 오늘 지출이 그 직원의
    해당 provider 한도를 넘었으면 차단(관리자 페이지에서 조정, 0=무제한).
    """
    if is_admin(email):
        return
    user = get_user(email)
    if not user:
        return  # 미등록 계정은 정상 흐름상 여기 도달 안 함(require_login 이 앞단에서 차단)
    spend = user_provider_today_cost(email)

    # 레거시 계정(신 provider 필드 없이 단일 cap_usd 만) — 예전처럼 '합산' 한도로
    # 검사한다. 구 cap_usd 는 총합(claude+gemini) 한도였는데, 이를 provider별로 독립
    # 적용하면 총 예산이 최대 2배로 조용히 완화된다(비용 통제 약화). 마이그레이션 전까지는
    # 합산으로 취급해 예전 의미를 보존한다. 관리자가 provider별 캡을 저장하면 아래 신 경로로.
    if "cap_claude_usd" not in user and "cap_gemini_usd" not in user:
        legacy = float(user.get("cap_usd", 0) or 0)
        if legacy > 0 and (spend.get("claude", 0.0) + spend.get("gemini", 0.0)) >= legacy:
            raise HTTPException(429, f"오늘 비용 한도(${legacy:g})를 초과했습니다. "
                                     f"관리자에게 한도 상향을 요청하세요.")
        return

    for prov in need:
        cap = user_cap(user, prov)
        if cap > 0 and spend.get(prov, 0.0) >= cap:
            label = PROVIDER_LABEL.get(prov, prov)
            raise HTTPException(429, f"오늘 {label} 한도(${cap:g})를 초과했습니다. "
                                     f"관리자에게 한도 상향을 요청하세요.")


# ── 작업 폴더 자동 정리 (3일 보관 정책 — 2026-07-06 학원장 결정) ────────────
_CLEANUP_AFTER_DAYS = 3

# 분석 1건당 페이지 상한 (시험지는 통상 4~12쪽 — 비용 폭주 방어선)
_MAX_ANALYZE_PAGES = 30


def cleanup_old_jobs() -> int:
    """WORK_DIR 에서 마지막 수정이 3일 지난 작업 폴더 삭제. 삭제 수 반환.

    결과물 영속 보관처는 Google Drive — 여기는 검수/재빌드용 임시 작업장이다.
    """
    cutoff = time.time() - _CLEANUP_AFTER_DAYS * 86400
    removed = 0
    try:
        subdirs = [d for d in WORK.iterdir() if d.is_dir()]
    except OSError:
        return 0
    for d in subdirs:
        # 인메모리 JOBS 에 살아있는 잡은 보호 — 3일 지난 잡을 rehydrate 로 열어
        # 텍스트 검수만 하는 중이면 mtime 이 안 갱신되어 오삭제될 수 있다(2026-07-06 QA).
        if d.name in JOBS:
            continue
        try:
            newest = max((f.stat().st_mtime for f in d.rglob("*") if f.is_file()),
                         default=d.stat().st_mtime)
        except OSError:
            continue
        if newest < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    if removed:
        print(f"  [정리] {_CLEANUP_AFTER_DAYS}일 지난 작업 {removed}건 삭제")
    return removed


# ── 작업 상태 지속화 (서버 재시작으로 인메모리 JOBS 초기화돼도 복구) ─────────
_PERSIST_KEYS = ("status", "dir", "src", "school", "code", "region", "subject", "exam_range",
                 "page_paths", "problems", "figures", "originals", "kinds", "usage", "pages",
                 "problem_count", "error", "rotations", "registry_key", "owner", "pdf_name",
                 "drive_file_id", "result",
                 "auto_figures", "figure_candidates", "figure_warnings")


def _save_state(job_id: str) -> None:
    import json
    j = _job(job_id)
    if not j:
        return
    st = {k: j[k] for k in _PERSIST_KEYS if k in j}
    try:
        (WORK / job_id / "state.json").write_text(
            json.dumps(st, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001 (지속화 실패는 치명적 아님)
        pass


def _rehydrate(job_id: str) -> dict | None:
    """JOBS 가 비었을 때 WORK/{job_id} 에서 복구. state.json 우선, 없으면 파일명으로 그림만."""
    import json
    # job_id 는 URL/바디에서 오는 클라이언트 값 — ".." 등 경로 탈출이면 복구 대상 아님
    if not job_id or ".." in job_id or "/" in job_id or "\\" in job_id:
        return None
    d = WORK / job_id
    if not d.is_dir():
        return None
    sf = d / "state.json"
    if sf.is_file():
        try:
            j = json.loads(sf.read_text(encoding="utf-8"))
            JOBS[job_id] = j
            return j
        except Exception:  # noqa: BLE001
            pass
    figs: dict[str, str] = {}
    for pat, cut in (("crop_*.png", 5), ("erased_*.png", 7), ("redrawn_*.png", 8)):
        for f in sorted(d.glob(pat)):
            figs[f.stem[cut:]] = str(f)   # crop < erased < redrawn 순으로 덮어써 최신 우선
    pages = [str(p) for p in sorted(d.glob("page-*.png"))]
    if not figs and not pages:
        return None
    j = {"status": "done", "dir": str(d), "page_paths": pages, "figures": figs}
    res = d / "result.hwpx"
    if res.is_file():
        j["result"] = str(res)   # 빌드 결과가 있으면 재시작 후에도 다운로드 가능
    JOBS[job_id] = j
    return j


def _job(job_id: str) -> dict | None:
    return JOBS.get(job_id) or _rehydrate(job_id)


def _render_pages(job_id: str) -> None:
    """업로드 직후: 페이지 이미지만 렌더(OCR 전). 사용자가 회전을 맞춘 뒤 분석을 돌린다."""
    try:
        j = JOBS[job_id]
        from pipeline.ingest import ingest
        # do_preprocess=False: 자동 deskew 는 누운 스캔을 ~90° 오판해 이미지를 자른다.
        # 방향 교정은 사용자가 회전 UI 에서 직접 하므로 자동 보정은 끈다.
        pages = ingest(j["src"], work_dir=j["dir"], do_preprocess=False)
        j["_pages"] = pages   # PageImage 객체(내부용) — api_job 직렬화에서 제외됨
        j.update(
            status="preview",
            pages=[{"index": p.index, "width": p.width, "height": p.height} for p in pages],
            page_paths=[p.path for p in pages],
        )
    except Exception as e:  # noqa: BLE001
        JOBS[job_id].update(status="error", error=f"{e}", trace=traceback.format_exc())
        _mark_registry_error(job_id)


def _mark_registry_error(job_id: str) -> None:
    j = JOBS.get(job_id) or {}
    key = j.get("registry_key")
    if key:
        try:
            update_registry_entry(key, job_id=job_id, status="error")
        except Exception:  # noqa: BLE001
            pass


def _run_analysis(job_id: str) -> None:
    """분석 전 단계에서 맞춘 회전을 적용한 뒤 OCR/구조화한다."""
    t0 = time.time()
    j = JOBS[job_id]
    try:
        pages = j["_pages"]
        rot = j.get("rotations", {}) or {}
        rotated = any(int(v or 0) % 360 for v in rot.values())
        from PIL import Image
        for pg in pages:
            ang = int(rot.get(str(pg.index), 0) or 0) % 360
            if ang:
                with Image.open(pg.path) as im:
                    im.rotate(-ang, expand=True).save(pg.path)   # 시계방향 ang
                with Image.open(pg.path) as im:
                    pg.width, pg.height = im.size
        # 회전은 파일에 이미 반영됨 — 재실행 시 같은 각도가 또 적용(이중 회전)되지 않게 소거
        j["rotations"] = {}
        # 표·상자 자동 감지(exam-engine 이식 2026-07-19): 표는 Claude 가 내용을
        # 구조화(grid)해 빌드 때 '한글 네이티브 표'로 직접 그린다(학원장 지시 2026-07-19).
        # 구조화 실패·충실도 미달 표만 이미지 크롭으로 갤러리에 올라간다.
        # 그림 자동 감지·자동 크롭은 금지(detect_figs=False — 학원장 결정 2026-07-19):
        # 그림은 검수자가 원본에서 '처음부터 드래그'로 직접 크롭한다. 자동 크롭은
        # 삭제→재드래그 이중 작업만 만들고, guide.html 의 "그림은 자동으로 안
        # 들어갑니다" 약속과도 어긋난다. AI 가 알아서 하는 건 표 작성뿐이다.
        # Gemini 재작도는 기존처럼 카드에서 수동으로만(자동 재작도는 원본과 달라져 비활성).
        # 구조화(발문/보기/수식/자모)는 정확도가 최우선이라 opus 사용(충실도>비용 방침).
        # 선(先)드레인 금지 — 반환값을 버리면 동시 요청의 미집계 토큰이 로그에서
        # 영영 빠져 총합이 깨진다(2026-07-06 QA). 귀속 노이즈는 감수, 총합은 보존.
        a = analyze(j["src"], pages=pages, school=j.get("school", ""),
                    code=j.get("code", ""), detect_figs=False, detect_tabs=True,
                    auto_figures=False, crop_only=True, tables_as_image=False,
                    gemini_redraw=False, structural=not rotated,
                    work_dir=j["dir"], model="claude-opus-4-8")
        usage = _drain_usage()
        cost = _log_usage(j.get("owner", ""), j.get("pdf_name", ""), "analyze",
                          usage, duration_s=time.time() - t0)
        # 안전 드레인 — 분석 중 Gemini 호출이 없으면 0. 켜진 적 있던 경로 대비 유지.
        gcost = _log_gemini_usage(j.get("owner", ""), j.get("pdf_name", ""))

        # ── 감지·재작도 결과를 검수 UI 갤러리에 등록 ─────────────────────────
        # j["figures"](fid→경로)가 갤러리의 단일 출처. 원본 크롭은 originals 에 보존해
        # '다시 재작도' 입력·원본 비교에 쓰고, 표는 kinds 에 기록해 빌드에서 표 규칙
        # (발문 중복 제거·단 폭)을 태운다. 미배정(problem_index=None)은 UI 가 배정을 강제.
        figs = j.setdefault("figures", {})
        kinds = j.setdefault("kinds", {})
        origs = j.setdefault("originals", {})
        auto_figs, warns, ui_cands = [], [], []
        def _register(cand: dict, kind: str) -> None:
            if cand.get("bbox"):
                ui_cands.append({"page": cand.get("page"), "bbox": cand["bbox"]})
            img = cand.get("image")
            if not img:
                warns.append(f"{(cand.get('page') or 0) + 1}쪽 {'표' if kind == 'table' else '그림'} "
                             f"자동 추출 실패: {cand.get('error') or '이미지 없음'}")
                return
            fid = uuid.uuid4().hex[:8]
            figs[fid] = img
            crop = cand.get("crop")
            if crop and crop != img:
                origs[fid] = crop
            if kind == "table":
                kinds[fid] = "table"
            entry = {"figure_id": fid, "page": cand.get("page"),
                     "problem_index": cand.get("assigned_index"),
                     "kind": kind, "source": cand.get("source")}
            if cand.get("redraw_error"):   # 재작도 실패 → 크롭 폴백 상태(카드에서 재시도 가능)
                entry["redraw_error"] = str(cand["redraw_error"])[:200]
            auto_figs.append(entry)
        for c in a.figure_candidates:
            _register(c, "figure")
        for t in a.table_candidates:
            # 구조화(grid) 성공 표는 problems[].tables 에 실려 빌드가 한글 표로 직접
            # 그린다 — 갤러리 등록 없음(이미지가 아니므로). 후보 박스 표시만 남긴다.
            if t.get("grid"):
                if t.get("bbox"):
                    ui_cands.append({"page": t.get("page"), "bbox": t["bbox"]})
                if t.get("assigned_index") is None:   # 미배정 grid 는 어디에도 안 들어감
                    warns.append(f"{(t.get('page') or 0) + 1}쪽 표 인식됨(문항 미배정) — "
                                 "필요하면 표 영역을 드래그로 크롭해 배정하세요.")
                continue
            _register(t, "table")   # 구조화 실패·충실도 미달 → 이미지 크롭으로 검수

        j.update(
            status="done",
            pages=[{"index": p.index, "width": p.width, "height": p.height} for p in a.pages],
            page_paths=[p.path for p in a.pages],
            problems=analysis_to_problems(a),
            usage={**usage, "cost_usd": cost + gcost, "gemini_cost_usd": gcost},
            figure_candidates=ui_cands, auto_figures=auto_figs, figure_warnings=warns,
        )
        _save_state(job_id)
    except Exception as e:  # noqa: BLE001
        JOBS[job_id].update(status="error", error=f"{e}", trace=traceback.format_exc())
        _log_usage(j.get("owner", ""), j.get("pdf_name", ""), "analyze",
                   _drain_usage(), duration_s=time.time() - t0, status="error")
        # 실패 전까지 성공한 자동 재작도(Gemini) 생성분도 집계(미드레인 잔류 방지)
        _log_gemini_usage(j.get("owner", ""), j.get("pdf_name", ""), status="error")
        _mark_registry_error(job_id)


@router.post("/api/analyze")
async def api_analyze(request: Request,
                      file: UploadFile = File(...), school: str = Form(""), code: str = Form(""),
                      region: str = Form(""), subject: str = Form(""), exam_range: str = Form(""),
                      registry_key: str = Form("")):
    email = require_login(request)
    _check_cost_cap(email)
    cleanup_old_jobs()   # 업로드 시점마다 3일 지난 작업 정리(가벼움)

    if registry_key:
        validate_safe_key(registry_key)

    job_id = uuid.uuid4().hex[:12]
    jdir = WORK / job_id
    jdir.mkdir(parents=True, exist_ok=True)
    pdf_name = Path(file.filename or "upload.pdf").name   # 경로 성분 제거
    src = jdir / pdf_name
    src.write_bytes(await file.read())
    JOBS[job_id] = {"status": "rendering", "dir": str(jdir), "src": str(src),
                    "school": school, "code": code, "region": region,
                    "subject": subject, "exam_range": exam_range, "rotations": {},
                    "registry_key": registry_key, "owner": email, "pdf_name": pdf_name}
    if registry_key:
        try:
            update_registry_entry(registry_key, job_id=job_id, status="converting",
                                  school=school, subject=subject, pdf_name=pdf_name)
        except Exception:  # noqa: BLE001 — 레지스트리 쓰기 실패가 업로드를 막지 않게
            pass
    threading.Thread(target=_render_pages, args=(job_id,), daemon=True).start()
    return {"job_id": job_id, "status": "rendering"}


@router.post("/api/jobs/{job_id}/run")
def api_run(job_id: str, request: Request, payload: dict = Body(default={})):
    """분석 전 회전 단계에서 정한 각도를 받아 OCR/구조화를 시작한다.

    payload: {"rotations": {"0": 90, "1": 0, ...}}  (페이지index → 시계방향 각도)
    """
    email = require_login(request)
    _check_cost_cap(email)   # 분석은 Claude만 사용(자동 Gemini 재작도 비활성)
    j = _job(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    if j.get("status") == "processing":
        raise HTTPException(409, "이미 분석이 진행 중입니다.")   # 중복 클릭 → 이중 과금 방지
    if "_pages" not in j:
        raise HTTPException(409, "아직 페이지 렌더가 끝나지 않았습니다.")
    # 페이지 수 상한 — 캡 검사는 '시작 전 누적액'만 보므로, 초대형 PDF 1건이
    # 페이지당 opus 호출로 일일 캡을 크게 뚫는 것을 여기서 막는다.
    if len(j["_pages"]) > _MAX_ANALYZE_PAGES:
        raise HTTPException(400, f"페이지가 너무 많습니다({len(j['_pages'])}쪽). "
                                 f"시험지 분석은 {_MAX_ANALYZE_PAGES}쪽 이하만 지원합니다.")
    j["rotations"] = (payload or {}).get("rotations", {}) or {}
    j["status"] = "processing"
    threading.Thread(target=_run_analysis, args=(job_id,), daemon=True).start()
    return {"status": "processing"}


@router.get("/api/jobs/{job_id}")
def api_job(job_id: str, request: Request):
    require_login(request)
    j = _job(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    out = {k: v for k, v in j.items()
           if not k.startswith("_") and k not in ("page_paths", "trace", "owner")}
    #        owner(업로더 이메일)는 응답에서 제외 — 다른 사용자에게 노출 방지
    if j.get("status") == "done":
        out["problem_count"] = len(j.get("problems", []))
    return out


@router.get("/api/jobs/{job_id}/pages/{idx}.png")
def api_page(job_id: str, idx: int, request: Request):
    require_login(request)
    j = _job(job_id)
    if not j or "page_paths" not in j or not (0 <= idx < len(j["page_paths"])):
        raise HTTPException(404, "page not found")
    return FileResponse(j["page_paths"][idx], media_type="image/png")


@router.post("/api/figure/crop")
def api_crop(request: Request, job_id: str = Body(...), page: int = Body(...), bbox: list = Body(...)):
    require_login(request)
    j = _job(job_id)
    if not j or "page_paths" not in j:
        raise HTTPException(404, "job not found")
    if not (0 <= page < len(j["page_paths"])):
        raise HTTPException(400, "잘못된 페이지 번호입니다.")
    if not (isinstance(bbox, list) and len(bbox) == 4):
        raise HTTPException(400, "bbox는 [x0, y0, x1, y1] 형식이어야 합니다.")
    fid = uuid.uuid4().hex[:8]
    out = str(Path(j["dir"]) / f"crop_{fid}.png")
    r = crop_region(j["page_paths"][page], bbox, out, pad=0.01)
    j.setdefault("figures", {})[fid] = out
    _save_state(job_id)
    return {"figure_id": fid, "width": r.width, "height": r.height}


@router.post("/api/figure/erase")
async def api_erase(request: Request, job_id: str = Form(...), figure_id: str = Form(...),
                    mask: UploadFile = File(...)):
    require_login(request)
    j = _job(job_id)
    if not j or figure_id not in j.get("figures", {}):
        raise HTTPException(404, "figure not found")
    src = j["figures"][figure_id]
    mask_path = str(Path(j["dir"]) / f"mask_{figure_id}.png")
    Path(mask_path).write_bytes(await mask.read())
    out = str(Path(j["dir"]) / f"erased_{figure_id}.png")
    # 인페인팅(LaMa/OpenCV)은 블로킹 CPU 작업 — async 라우트에서 직접 부르면
    # 이벤트 루프 전체가 멈춰 다른 요청까지 지연된다(2026-07-07 속도 검토).
    try:
        await run_in_threadpool(erase_with_mask, src, mask_path, out)
    except Exception as e:   # cv2 미로드(libGL) 등 — 원인을 UI 까지 전달(무증상 실패 방지)
        raise HTTPException(500, f"낙서 지우개 처리 실패: {e}")
    j["figures"][figure_id] = out
    _save_state(job_id)
    return {"figure_id": figure_id, "status": "erased"}


@router.post("/api/figure/redraw")
def api_redraw(request: Request, job_id: str = Body(...), figure_id: str = Body(...),
               pro: bool = Body(False), kind: str = Body("figure")):
    """크롭 영역 재작도(Gemini image-to-image).

    kind="figure"(기본) 도형 재작도 / kind="table" 표(격자·실선) 재현.
    기본=저비용 Flash, pro=True 면 '다시 생성'으로 Pro(고품질).
    폴백: 구조 스펙 → matplotlib (도형 한정, 표는 폴백 없이 에러).
    """
    email = require_login(request)
    _check_cost_cap(email, need=("claude", "gemini"))   # 재작도는 Gemini 생성 + Claude 검증
    t0 = time.time()
    j = _job(job_id)
    if not j or figure_id not in j.get("figures", {}):
        raise HTTPException(404, "figure not found")
    src = j["figures"][figure_id]
    # 재작도 전 이미지를 '원본 비교용'으로 보존(최초 1회) — 숫자/라벨 변조 검수용
    orig = j.setdefault("originals", {}).setdefault(figure_id, src)
    # 재작도 입력은 항상 '재작도 전' 이미지로 — 재작도본을 다시 입력하면 직전 시도의
    # 환각(없는 글자 등)이 다음 결과로 누적된다. redrawn_*=수동 재작도본,
    # *_gem.png=분석 단계 자동 재작도본(exam-engine 이식, 2026-07-19).
    if Path(src).name.startswith("redrawn_") or Path(src).name.endswith("_gem.png"):
        src = orig
    out = str(Path(j["dir"]) / f"redrawn_{figure_id}.png")
    OPUS = "claude-opus-4-8"   # 구조 추출·검증은 가장 똑똑한 모델로(정확도 우선)

    def _log_redraw(status: str = "ok") -> None:
        # 한 번의 재작도 = Claude 검증(토큰) + Gemini 생성(이미지). 두 provider 를
        # 각각 기록해 키별 사용량이 정확히 잡히게 한다(Gemini 는 로그가 비면 스킵).
        _log_usage(email, j.get("pdf_name", ""), "redraw", _drain_usage(),
                   duration_s=time.time() - t0, status=status)
        _log_gemini_usage(email, j.get("pdf_name", ""), status=status)

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
        # 반려 시 지적사항을 넣어 1회 재시도.
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
            _log_redraw("rejected")
            issues = " / ".join(map(str, verdict.get("issues", [])[:3])) or "원본과 불일치"
            raise HTTPException(422, f"재작도 반려(원본 유지) — 검증에서 차이 발견: {issues} "
                                     f"— 다시 시도하거나 '낙서 지우기'한 원본을 그대로 쓰세요.")
        j["figures"][figure_id] = path
        j.setdefault("kinds", {})[figure_id] = kind   # build 에서 표는 단 폭으로 삽입
        _save_state(job_id)
        _log_redraw()
        return {"figure_id": figure_id, "status": "redrawn", "method": "gemini",
                "model": model, "pro": pro, "kind": kind,
                "verified": bool(verdict), "ok": (verdict or {}).get("ok", True),
                "score": (verdict or {}).get("score"),
                "issues": (verdict or {}).get("issues", [])}
    except GeminiError as e:        # 키 미설정/안전차단 등 → 폴백
        gem_err = str(e)

    # 표는 구조스펙/matplotlib 폴백이 부적합 → Gemini 실패 시 에러로
    if kind == "table":
        _log_redraw("error")
        raise HTTPException(422, f"표 재현 실패(Gemini): {gem_err}")

    # 폴백: 구조 스펙 → matplotlib 코드 재작도(검증 1회)
    from pipeline.figure_ai import redraw_via_spec, redraw_figure_verified, RedrawError
    try:
        path, _ = redraw_via_spec(src, out, model=OPUS)
        j["figures"][figure_id] = path
        _save_state(job_id)
        _log_redraw()
        # 응답 필드는 gemini 경로와 동일 구성으로 — 프론트(FigureCard)가 pro/ok 를 읽는다
        return {"figure_id": figure_id, "status": "redrawn", "method": "spec",
                "pro": pro, "kind": kind, "verified": False, "gemini_error": gem_err}
    except Exception:  # noqa: BLE001
        pass
    try:
        path, verdict = redraw_figure_verified(src, out, max_retries=1, model=OPUS)
    except RedrawError as e:
        _log_redraw("error")
        raise HTTPException(422, f"재작도 실패: {e} (gemini: {gem_err})")
    j["figures"][figure_id] = path
    _save_state(job_id)
    _log_redraw()
    return {"figure_id": figure_id, "status": "redrawn", "method": "code",
            "pro": pro, "kind": kind,
            "score": verdict.get("score"), "ok": verdict.get("ok"),
            "issues": verdict.get("issues", []), "gemini_error": gem_err}


@router.get("/api/figure/{job_id}/{figure_id}.png")
def api_figure_img(job_id: str, figure_id: str, request: Request):
    require_login(request)
    j = _job(job_id)
    if not j or figure_id not in j.get("figures", {}):
        raise HTTPException(404, "figure not found")
    return FileResponse(j["figures"][figure_id], media_type="image/png")


@router.get("/api/figure/{job_id}/{figure_id}/original.png")
def api_figure_original(job_id: str, figure_id: str, request: Request):
    """재작도 전 원본(크롭/낙서지움) 이미지 — 검수 UI 의 '원본↔재작도' 비교용.

    재작도 전이면 '원본' = 현재 그림(크롭/지움본) 그 자체 → 404 대신 그걸 준다.
    """
    require_login(request)
    j = _job(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    orig = j.get("originals", {}).get(figure_id) or j.get("figures", {}).get(figure_id)
    if not orig or not Path(orig).exists():
        orig = j.get("figures", {}).get(figure_id)
    if not orig or not Path(orig).exists():
        raise HTTPException(404, "original not found")
    return FileResponse(orig, media_type="image/png")


@router.post("/api/jobs/{job_id}/build")
def api_build(job_id: str, request: Request, payload: dict = Body(...)):
    """payload: {problems:[...], figures:[{figure_id, problem_index}], header:{...}}

    빌드 성공 시: registry_key 가 있으면 Google Drive 업로드 + 레지스트리 done 갱신.
    """
    email = require_login(request)
    t0 = time.time()
    j = _job(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    import copy as _copy
    problems = _copy.deepcopy(payload.get("problems") or j.get("problems", []))

    # payload.problems 에 실려온 figures 경로는 작업 폴더 내부만 허용 —
    # 임의 서버 경로(.env 등)를 넣어 HWPX 안에 담아 내려받는 것을 막는다.
    _jdir = Path(j["dir"]).resolve()

    def _fig_in_jobdir(f) -> bool:
        p = f.get("path") if isinstance(f, dict) else f
        try:
            return isinstance(p, str) and Path(p).resolve().is_relative_to(_jdir)
        except (OSError, ValueError):
            return False

    for p in problems:
        if p.get("figures"):
            p["figures"] = [f for f in p["figures"] if _fig_in_jobdir(f)]
        if p.get("tables"):   # 표 이미지 폴백 경로도 동일 검증(임의 서버 경로 차단)
            p["tables"] = [t for t in p["tables"] if isinstance(t, dict)
                           and (not t.get("image") or _fig_in_jobdir(t["image"]))]

    # UI 갤러리(자동+수동)가 그림의 단일 출처 → 분석에서 붙은 figures 는 비우고
    # payload.figures(figure_id) 만 해당 문제에 매핑(이중삽입 방지)
    fig_map = j.get("figures", {})
    payload_figs = payload.get("figures")
    # kind 는 payload(검수 UI 의 그림/표 선택)가 우선, 없으면 재작도 때 기록된 값.
    # 종전엔 재작도 기록만 봐서, 재작도 없이 크롭만 한 표가 '그림'으로 취급돼
    # 표 폭·발문 중복 제거가 전혀 동작하지 않았다(2026-07-11).
    _kinds = j.get("kinds", {})

    def _fig_kind(f: dict) -> str:
        return str(f.get("kind") or _kinds.get(f.get("figure_id")) or "figure")

    # 표 그림이 있으면 발문 중복 제거에 Claude(opus) 호출이 발생 → 비용 캡 검사
    if any(_fig_kind(f) == "table" for f in (payload_figs or []) if isinstance(f, dict)):
        _check_cost_cap(email)
    if payload_figs is not None:
        for p in problems:
            p["figures"] = []
            # 자동 표 중 '이미지 폴백'은 갤러리(auto_figures, kind=table)로 다시
            # 들어오므로 소거(이중 삽입 방지). 구조화(grid) 성공 표는 갤러리에 없고
            # 빌드가 한글 네이티브 표로 직접 그리므로 유지한다.
            p["tables"] = [t for t in (p.get("tables") or [])
                           if isinstance(t, dict) and t.get("grid")]
        kinds = j.setdefault("kinds", {})
        for f in payload_figs:
            if not isinstance(f, dict):
                continue
            idx, fid = f.get("problem_index"), f.get("figure_id")
            if fid in fig_map and isinstance(idx, int) and 0 <= idx < len(problems):
                # 표는 단 폭 가득(≈108mm), 그림은 기본 폭.
                if _fig_kind(f) == "table":
                    kinds[fid] = "table"   # 재빌드/재작도에서도 표로 유지
                    # 사용자가 직접 배정한 표 크롭이 자동 구조화(grid) 표를 교체
                    # (같은 표가 네이티브+이미지로 두 번 들어가는 것 방지)
                    problems[idx]["tables"] = []
                    item = {"path": fig_map[fid], "width_mm": 108.0, "max_height_mm": 200.0,
                            "kind": "table"}
                    # 표 내용이 발문에 텍스트로 중복되면 Claude 로 의미 대조 제거.
                    # 표 내용 파악엔 재작도 전 원본 크롭이 정확. 실패하면 원본 발문 유지.
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
    # 문제 간 간격(빈 줄 수). 기본 20(2026-07-08 15→20 상향; 짧은 객관식 상단 몰림 완화), payload 로 조절 가능.
    gap = payload.get("gap_lines")
    gap = int(gap) if isinstance(gap, (int, float, str)) and str(gap).isdigit() else 20
    # 1페이지 상단 헤더 표(과목/범위/지역/학교/학기): 업로드 때 받은 값으로 채운다.
    # 빈 값은 템플릿 텍스트 유지. payload.header 가 오면 그 값이 우선.
    header = {"school": j.get("school", ""), "region": j.get("region", ""),
              "subject": j.get("subject", ""), "exam_range": j.get("exam_range", ""),
              "term": j.get("code", "")}
    header.update(payload.get("header") or {})
    rep = build_exam(TEMPLATE, problems, out, header=header, gap_lines=gap)
    _log_usage(email, j.get("pdf_name", ""), "build", _drain_usage(),
               duration_s=time.time() - t0)
    j["result"] = out

    # ── Drive 업로드 + 레지스트리 갱신 (매트릭스에서 시작한 작업만) ──────────
    drive_id = None
    drive_error = None       # 실패 사유 — UI 가 "미연동/실패" 를 구분해 안내
    key = j.get("registry_key", "")
    if key:
        parts = key.split("_")
        year = parts[0] if parts and parts[0].isdigit() else datetime.now().strftime("%Y")
        subject = parts[4] if len(parts) >= 5 else (j.get("subject") or "기타")
        named = Path(j["dir"]) / f"[{key}].hwpx"
        old_id = j.get("drive_file_id")
        try:
            shutil.copyfile(out, named)
            drive_id = upload_hwpx(named, year, subject)
            if not drive_id:
                drive_error = drive_last_error() or "알 수 없는 오류(서버 로그 확인)"
        except Exception as e:  # noqa: BLE001 — Drive 실패는 빌드 실패가 아님(다운로드는 가능)
            drive_id = None
            drive_error = f"{e.__class__.__name__}: {e}"
        finally:
            named.unlink(missing_ok=True)
        if drive_id:
            # ⚠️ 순서 불변: 새 업로드 '성공 확인 후'에만 이전 파일 삭제.
            #    선삭제하면 업로드 실패 시 Drive 보관본이 통째로 사라진다(2026-07-06 QA).
            if old_id and old_id != drive_id:
                drive_delete_file(old_id)
            j["drive_file_id"] = drive_id
        else:
            drive_id = None
            # 업로드 실패 — 이전 성공본 ID 는 보존(레지스트리도 덮어쓰지 않음)
        # 🔴 확인 필요: 수식 변환 폴백이 있으면 사람이 한글에서 채워야 할 자리가 있다는 뜻.
        # 매트릭스 배지로 표시하고, 문서 안에는 【확인필요】 마커가 찍혀 Ctrl+F 로 찾는다.
        needs_review = rep.equations_fallback > 0
        review_note = (f"수식 변환 실패 {rep.equations_fallback}개 — "
                       f"한글에서 Ctrl+F '확인필요' 검색 후 수동 교정") if needs_review else ""
        try:
            update_registry_entry(key, job_id=job_id, status="done",
                                  school=j.get("school", ""), subject=j.get("subject", ""),
                                  pdf_name=j.get("pdf_name", ""),
                                  drive_file_id=j.get("drive_file_id"),
                                  needs_review=needs_review, review_note=review_note,
                                  reviewed_by=None, reviewed_at=None)
        except Exception:  # noqa: BLE001 — 레지스트리 쓰기 실패가 성공한 빌드를 5xx 로 만들지 않게
            pass
    _save_state(job_id)
    return {"status": "ok", "problems": rep.problems,
            "equations_ok": rep.equations_ok, "equations_fallback": rep.equations_fallback,
            "figures_inserted": rep.figures_inserted,
            "fallbacks": rep.fallbacks, "download": f"/api/jobs/{job_id}/download",
            "drive_uploaded": bool(drive_id),
            # 매트릭스 밖 변환(no_key)과 '연동/업로드 실패'를 UI 에서 구분
            "drive_skipped": None if key else "no_registry_key",
            "drive_error": drive_error}


@router.get("/api/jobs/{job_id}/download")
def api_download(job_id: str, request: Request):
    require_login(request)
    j = _job(job_id)
    if not j or "result" not in j:
        raise HTTPException(404, "result not ready")
    key = j.get("registry_key", "")
    fname = f"[{key}].hwpx" if key else "변환_시험지.hwpx"
    return FileResponse(j["result"],
                        media_type="application/octet-stream",
                        filename=fname)


@router.delete("/api/jobs/{job_id}")
def api_delete_job(job_id: str, request: Request):
    """잡 삭제 — 작업 폴더 + Drive 파일 + 레지스트리 항목 제거 (매트릭스 삭제 버튼)."""
    require_login(request)
    validate_safe_key(job_id)
    j = _job(job_id) or {}

    # Drive 파일 삭제 (job state 우선, 레지스트리 폴백)
    drive_id = j.get("drive_file_id")
    reg = load_registry()
    keys_to_del = [k for k, v in reg.items() if v.get("job_id") == job_id]
    if not drive_id:
        for k in keys_to_del:
            drive_id = reg[k].get("drive_file_id")
            if drive_id:
                break
    if drive_id:
        ok = drive_delete_file(drive_id)
        if not ok:
            print(f"  [Drive] {drive_id} 삭제 실패 (계속 진행)")

    # 작업 폴더 삭제
    shutil.rmtree(WORK / job_id, ignore_errors=True)
    JOBS.pop(job_id, None)

    # 레지스트리에서 제거
    for k in keys_to_del:
        del reg[k]
    if keys_to_del:
        save_registry(reg)

    return {"ok": True, "deleted_keys": keys_to_del}


_CV2_OK = None   # 첫 헬스 호출 때 1회 판정 후 캐시 (import cv2 는 ~1초 걸림)


def _opencv_ok() -> bool:
    """낙서 지우개·기울기 보정의 전제인 cv2 가 실제로 '로드'되는지 확인.

    설치돼 있어도 네이티브 lib(libGL 등)이 없으면 import 가 런타임에 실패한다
    (2026-07-11 Railway 실사고) — 배포 후 로그인 없이 확인할 수 있게 헬스에 노출.
    """
    global _CV2_OK
    if _CV2_OK is None:
        try:
            import cv2                      # noqa: F401
            _CV2_OK = True
        except Exception:
            _CV2_OK = False
    return _CV2_OK


@router.get("/api/health")
def health():
    import os
    return {"ok": True,
            "claude": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "gemini": bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")),
            "mathpix": bool(os.environ.get("MATHPIX_APP_ID")),
            "opencv": _opencv_ok()}
