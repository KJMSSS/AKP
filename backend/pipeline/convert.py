"""
오케스트레이터: PDF/이미지 -> 구조화 Problem JSON -> 한글 시험지 hwpx.

전체 파이프라인을 한 함수로 묶는다. CLI/FastAPI/UI 가 공통으로 사용.

  ingest -> (페이지별) Mathpix OCR -> Claude 구조화 -> 병합 -> build_exam

그림은 이 단계에서 '후보 감지'만 하고(detect_figures), 실제 크롭/낙서제거/삽입은
사용자 검수(UI)를 거친다. analyze() 가 후보까지 포함한 검수용 상태를 돌려준다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .ingest import ingest
from .ocr_mathpix import mmd_to_runs
from .vision_claude import (structure_problems_vision, detect_figures,
                            detect_tables, table_to_grid, verify_table,
                            read_box_lines, clean_stem_against_table)
from .build_exam import build_exam, BuildReport
from .figure import crop_region, process_figure, snap_rect_bbox
from .figure_pdf import detect_graphics_pdf
from .table import should_rasterize
from .fidelity import passes, TABLE_MIN_SCORE, FIGURE_MIN_SCORE


@dataclass
class AnalyzedProblem:
    number: str
    points: str
    school: str
    code: str
    difficulty: str
    stem: list           # runs (text/eqn-latex)
    choices: list        # list[runs]
    page: int
    figures: list = field(default_factory=list)   # 변환된 그림 이미지 경로(자동/수동)
    tables: list = field(default_factory=list)     # 표: {source:"struct",grid} 또는 {source:"image",image}
    has_figure: bool = False   # 비전 구조화가 감지한 '문제에 그림/그래프 있음' — 검수 경고용
    has_table: bool = False    # 비전 구조화가 감지한 '문제에 표 있음' — 검수 경고용


@dataclass
class Analysis:
    source: str
    pages: list = field(default_factory=list)        # PageImage 들
    problems: list = field(default_factory=list)      # AnalyzedProblem
    figure_candidates: list = field(default_factory=list)  # {page, label, bbox}
    table_candidates: list = field(default_factory=list)   # {page, label, bbox, source, ...}

    def to_json(self) -> str:
        return json.dumps({
            "source": self.source,
            "pages": [asdict(p) if hasattr(p, "__dict__") else p for p in self.pages],
            "problems": [asdict(p) for p in self.problems],
            "figure_candidates": self.figure_candidates,
            "table_candidates": self.table_candidates,
        }, ensure_ascii=False, indent=2)


def _norm_num(s) -> str:
    """문제 번호를 숫자만 남겨 정규화('5번','5.','문제 5' → '5')."""
    import re
    return re.sub(r"\D", "", str(s or ""))


def _match_problem(problems: list, page_idx: int, fig_problem: str, fig_bbox: list):
    """그림을 소속 문제에 연결: 같은 페이지에서 (1) 번호 일치(숫자 정규화), 없으면
    (2) bbox y중심 비율로 페이지 내 순서 추정. 매칭 실패 시 None."""
    same = [p for p in problems if p.page == page_idx]
    if not same:
        return None
    fp = _norm_num(fig_problem)
    if fp:
        for p in same:
            if _norm_num(p.number) == fp:
                return p
    # 번호 매칭 실패 → bbox y중심 비율로 페이지 내 순서 추정(균등 분할 근사)
    if fig_bbox and len(fig_bbox) >= 4:
        ycen = (fig_bbox[1] + fig_bbox[3]) / 2.0
        idx = min(len(same) - 1, max(0, int(ycen * len(same))))
        return same[idx]
    return same[0]


def _index_of(problems: list, target) -> int | None:
    """값 동등이 아닌 '동일 객체' 기준 인덱스(필드 동일 문제 충돌 방지)."""
    for k, p in enumerate(problems):
        if p is target:
            return k
    return None


def _natural_width_mm(image_path: str, dpi: int) -> float | None:
    """크롭 이미지의 '원본 크기'(mm) — 픽셀 ÷ 렌더 DPI. 원본 시험지에서 차지하던 실제
    폭으로 삽입해야 한글에서 원본과 같은 크기로 보인다(단 폭으로 확대하면 그림이 너무
    커져 1페이지가 여러 페이지로 불어난다 — exam-engine 2026-07-18 실사고 이식)."""
    try:
        from PIL import Image
        with Image.open(image_path) as im:
            w = im.size[0]
        return round(w / max(dpi, 1) * 25.4, 1)
    except Exception:  # noqa: BLE001
        return None


def _runs_text(runs) -> str:
    """runs([{type,text/latex}] 또는 str) 에서 표시 텍스트만 이어붙인다."""
    out = []
    for r in runs or []:
        if isinstance(r, dict):
            out.append(r.get("text", "") or r.get("latex", "") or "")
        elif isinstance(r, str):
            out.append(r)
    return " ".join(out)


def _page_body_norm(problems: list, page_idx: int) -> str:
    """해당 페이지 문항들의 지문+발문+선택지를 공백제거·연결 — PDF 구조 크롭에서
    '본문 텍스트'(보기·자료·발문 상자 = native)를 그림/표와 구분하는 기준."""
    import re
    txt = ""
    for p in problems:
        if p.page != page_idx:
            continue
        txt += " " + _runs_text(p.stem)
        for c in p.choices:
            txt += " " + _runs_text(c)
    return re.sub(r"\s+", "", txt)


def _gemini_auto_redraw(crop_img: str, kind: str = "figure") -> tuple[str | None, str | None]:
    """감지된 크롭을 Gemini image-to-image 로 자동 재작도(그림/표 지시 분리).

    반환 (재작도 경로, 오류메시지) — 실패 시 (None, 사유). 검증(opus)은 하지 않는다:
    자동 경로의 결과는 검수 UI 갤러리에 떠서 사람이 확인하고, 필요하면 개별 재작도
    (검증 포함)로 다시 그린다. 사용량은 redraw_gemini._GEMINI_USAGE_LOG 에 쌓여
    웹셸이 provider='gemini' 로 집계한다.
    """
    from pathlib import Path
    try:
        from .redraw_gemini import (redraw_with_gemini, DEFAULT_IMAGE_MODEL,
                                    REDRAW_TABLE_INSTRUCTION)
        out = str(Path(crop_img).with_name(Path(crop_img).stem + "_gem.png"))
        instr = REDRAW_TABLE_INSTRUCTION if kind == "table" else None
        redraw_with_gemini(crop_img, out, model=DEFAULT_IMAGE_MODEL, instruction=instr)
        return out, None
    except Exception as e:  # noqa: BLE001 (재작도 실패 → 크롭 폴백 + 표면화)
        return None, str(e)


def _autorotate_pages(pages, model=None) -> None:
    """회전된 스캔(90/180/270°)을 똑바로 세운다 — OCR 이 발문을 못 읽는 것을 방지.

    Claude 로 방향을 감지(detect_orientation)해 PIL 로 회전·저장하고 PageImage 크기를
    갱신한다. 감지/회전 실패 시 해당 페이지는 원본을 그대로 둔다(안전).
    """
    from PIL import Image
    from .vision_claude import detect_orientation
    for pg in pages:
        try:
            angle = detect_orientation(pg.path, model=model)
            if angle:
                with Image.open(pg.path) as im:
                    im.rotate(-angle, expand=True).save(pg.path)   # 시계방향 angle
                with Image.open(pg.path) as im:
                    pg.width, pg.height = im.size
        except Exception:  # noqa: BLE001 (회전 실패 → 원본 유지)
            pass


def analyze(path: str, *, school: str = "", code: str = "", model: str | None = None,
            detect_figs: bool = True, detect_tabs: bool = True,
            auto_figures: bool = True, redraw: bool = True, crop_only: bool = False,
            tables_as_image: bool = False, gemini_redraw: bool = False,
            structural: bool | None = None,
            dpi: int = 300, do_preprocess: bool = False,
            work_dir: str | None = None, pages=None) -> Analysis:
    """OCR + 구조화 + (자동) 그림 추출·변환·문제연결.

    auto_figures=True 면 감지된 그림마다 고해상도 크롭 → (redraw=True 면) AI 재작도
    (실패 시 크롭 유지) → 소속 문제의 figures 에 첨부한다. 아무 이미지나 넣지 않고
    '대응 문제'에 변환된 그림을 넣는 것이 목적.
    do_preprocess: 기울기/노이즈 보정(깨끗한 스캔이면 불필요, 기본 off).

    exam-engine(과학 엔진) 이식 옵션(2026-07-19):
      crop_only=True       그림 공정에서 인페인팅/코드 재작도를 생략하고 순수 크롭만.
      gemini_redraw=True   감지된 그림·표를 Gemini image-to-image 로 자동 재작도해 넣는다
                           (실패 시 크롭 폴백 + redraw_error 표면화).
      tables_as_image=True 표를 구조화(grid) 대신 '이미지'로 처리(구조 재구성이 값을
                           쪼개는 문제 회피 — 재작도/크롭이 원본에 가장 충실).
      structural           디지털 PDF 구조 기반 결정적 크롭(figure_pdf) 사용 여부.
                           None(기본)이면 'pages 미지정 + .pdf' 일 때 자동 사용.
                           웹셸은 회전 미적용 PDF 에 한해 True 로 켠다(회전되면 원본
                           PDF 좌표와 렌더 페이지가 어긋남). 스캔이면 내부에서 VLM 폴백.
    """
    import tempfile
    from pathlib import Path
    # pages 가 주어지면(업로드→수동 회전 후 호출) 그대로 쓴다. 없으면 여기서 렌더.
    # 회전 보정은 분석 '전 단계'에서 사용자가 직접 맞춘다(자동 감지는 오판 위험이 커 폐기).
    if structural is None:
        structural = (pages is None) and str(path).lower().endswith(".pdf")
    else:
        structural = structural and str(path).lower().endswith(".pdf")
    if pages is None:
        pages = ingest(path, dpi=dpi, do_preprocess=do_preprocess, work_dir=work_dir)
    result = Analysis(source=path, pages=pages)
    base = Path(work_dir or tempfile.mkdtemp(prefix="examfig_"))
    figdir = base / "figures"; figdir.mkdir(parents=True, exist_ok=True)
    tabdir = base / "tables"; tabdir.mkdir(parents=True, exist_ok=True)

    for pg in pages:
        for p in structure_problems_vision(pg.path, model=model):
            result.problems.append(AnalyzedProblem(
                number=str(p.get("number", "")), points=p.get("points", ""),
                school=school, code=code, difficulty=p.get("difficulty", "") or "",
                stem=mmd_to_runs(p.get("stem", "")),
                choices=[mmd_to_runs(c) for c in p.get("choices", [])],
                page=pg.index,
                has_figure=bool(p.get("has_figure")),
                has_table=bool(p.get("has_table")),
            ))
        # --- 구조 기반 크롭 경로(디지털 PDF — exam-engine 이식) ---
        # 원본 PDF 좌표로 그림·데이터표를 결정적으로 크롭한다. 보기·자료·발문 상자는
        # problems 본문과 매칭돼 제외되고(native 로 그림), 그림/표만 남는다. VLM 눈대중
        # bbox 의 오크롭(텍스트 조각·뒤섞임)을 근본 제거. 스캔 페이지면 None → VLM 폴백.
        if structural and detect_figs:
            body_norm = _page_body_norm(result.problems, pg.index)
            try:
                pdf_figs = detect_graphics_pdf(path, pg.index, str(figdir),
                                               body_norm, dpi=dpi,
                                               prefix=f"pg{pg.index}")
            except Exception:  # noqa: BLE001 (구조 크롭 실패 → VLM 폴백)
                pdf_figs = None
            if pdf_figs is not None:
                for pf in pdf_figs:
                    crop_img = pf["image"]
                    # 표시 크기(mm)는 '원본 영역' 기준(크롭 픽셀÷DPI) — Gemini 재작도본은
                    # 픽셀 크기가 달라도 원본과 같은 크기로 삽입해야 한다.
                    nat = _natural_width_mm(crop_img, dpi)
                    final_img, source = crop_img, "pdf_crop"
                    redraw_err = None
                    if gemini_redraw:
                        redrawn, redraw_err = _gemini_auto_redraw(crop_img)
                        if redrawn:
                            final_img, source = redrawn, "gemini_redraw"
                    cand = {"page": pg.index, "label": "", "bbox": pf["bbox"],
                            "problem": pf["problem"], "image": final_img,
                            "crop": crop_img, "source": source}
                    if nat:
                        cand["width_mm"] = nat
                    if redraw_err:
                        cand["redraw_error"] = redraw_err
                    target = _match_problem(result.problems, pg.index,
                                            pf["problem"], pf["bbox"])
                    idx = _index_of(result.problems, target) if target is not None else None
                    if idx is not None:
                        result.problems[idx].figures.append(
                            {"path": final_img, "width_mm": nat} if nat else final_img)
                        cand["assigned_to"] = target.number
                        cand["assigned_index"] = idx
                    else:
                        cand["error"] = "배정 문제 없음(미배정)"
                    result.figure_candidates.append(cand)
                continue   # 이 페이지는 구조 추출로 완료 — VLM 감지 생략

        # 그림 감지(detect_figs)와 표 감지(detect_tabs)는 독립이다. 과거엔 여기서
        # detect_figs=False 면 continue 해 표 감지까지 통째로 건너뛰었다(웹 UI 경로에서
        # 표가 하나도 안 잡히던 원인 — exam-engine 2026-07-18 수정 이식).
        for fi, fg in (enumerate(detect_figures(pg.path, model=model))
                       if detect_figs else []):
            # 페이지1 최상단 헤더(성명·수험 번호·시험명)는 그림이 아니다 — 프롬프트만으로
            # 안 걸러지는 오검출을 '위치'로 결정적 배제(exam-engine 실사고: 수험번호 칸이
            # 그림으로 잡혀 1번 문제에 붙음). bbox 하단(y1)이 페이지 상단 15% 안이면
            # (=전부 헤더대) 스킵. 실제 문제 그림은 그 아래에서 시작한다.
            if pg.index == 0 and fg.bbox and len(fg.bbox) >= 4 and fg.bbox[3] < 0.15:
                continue
            cand = {"page": pg.index, "label": fg.label, "bbox": fg.bbox,
                    "problem": fg.problem, "image": None}
            if auto_figures:
                try:                       # 전체 공정: 크롭→잡티제거(LaMa)→재작도검증→폴백
                    pf = process_figure(pg.path, fg.bbox, str(figdir), model=model,
                                        redraw=redraw, crop_only=crop_only,
                                        prefix=f"p{pg.index}_f{fi}")
                    cand["image"] = pf["final"]
                    cand["crop"] = pf.get("crop")
                    cand["source"] = pf["source"]   # crop | inpaint | redraw
                    if pf.get("verdict"):           # 재작도 충실도(검증 통과분)
                        v = dict(pf["verdict"])
                        v["flagged"] = not passes(v, FIGURE_MIN_SCORE)
                        cand["fidelity"] = v
                    if pf.get("redraw_error"):
                        cand["redraw_error"] = pf["redraw_error"]
                    # 자동 Gemini 재작도 — 수동 검수의 '🎨 재작도' 버튼과 같은 결과를
                    # 분석 단계에서 미리 만든다(입력은 크롭/인페인팅본, 실패 시 크롭 유지).
                    if gemini_redraw and cand["image"]:
                        redrawn, gerr = _gemini_auto_redraw(cand["image"])
                        if redrawn:
                            cand["image"], cand["source"] = redrawn, "gemini_redraw"
                        elif gerr:
                            cand["redraw_error"] = gerr
                except Exception as e:  # noqa: BLE001 (한 그림만 건너뜀, 표면화)
                    cand["error"] = f"그림 처리 실패: {e}"
                if cand.get("image"):
                    # 원본 크기(크롭 픽셀÷DPI)로 삽입 — 단 폭 확대 금지(페이지 폭발 방지)
                    nat = _natural_width_mm(cand.get("crop") or cand["image"], dpi)
                    if nat:
                        cand["width_mm"] = nat
                    target = _match_problem(result.problems, pg.index, fg.problem, fg.bbox)
                    idx = _index_of(result.problems, target) if target is not None else None
                    if idx is not None:
                        result.problems[idx].figures.append(
                            {"path": cand["image"], "width_mm": nat} if nat
                            else cand["image"])
                        cand["assigned_to"] = target.number
                        cand["assigned_index"] = idx
                    else:
                        cand["error"] = (cand.get("error") or "") + " 배정 문제 없음(미배정)"
            result.figure_candidates.append(cand)

        if detect_tabs:
            for ti, tg in enumerate(detect_tables(pg.path, model=model)):
                # 1쪽 머리말대(시험명·배점표·성명칸) 오검출을 위치로 결정적 배제 —
                # 프롬프트 배제만으로 안 걸러진 실사고(제목 줄이 표로 잡혀 1번 문항에
                # 배정, 2026-07-19 용봉중). 한국 시험지 1쪽 상단 18% 는 머리말 영역이고
                # 실제 문제 표는 그 아래에서 시작한다.
                if pg.index == 0 and tg.bbox and len(tg.bbox) >= 4 and tg.bbox[3] < 0.18:
                    continue
                # VLM bbox 는 상자를 통째로 빗나가거나 끝 줄을 자르는 일이 잦다 —
                # 인쇄된 4변 테두리에 결정적으로 스냅(실패 시 원래 bbox 폴백).
                try:
                    use_bbox = snap_rect_bbox(pg.path, tg.bbox) or tg.bbox
                except Exception:  # noqa: BLE001
                    use_bbox = tg.bbox
                tab = {"page": pg.index, "label": tg.label, "bbox": use_bbox,
                       "problem": tg.problem, "source": None,
                       "kind": tg.kind or "grid"}
                crop_path = str(tabdir / f"p{pg.index}_t{ti}_crop.png")
                try:
                    crop_region(pg.path, use_bbox, crop_path, pad=0.012)
                except Exception as e:  # noqa: BLE001 (표 하나만 건너뜀, 표면화)
                    tab["error"] = f"표 크롭 실패: {e}"
                    result.table_candidates.append(tab)
                    continue
                tab["crop"] = crop_path
                if tables_as_image:
                    # 표를 '이미지'로 처리 — 구조 재구성(table_to_grid)이 값을 쪼개고
                    # (132→1 3 2) 대각선 borderFill 을 그리는 문제를 회피(exam-engine
                    # 2026-07-18). 원본 크기(mm)로 삽입하고, gemini_redraw=True 면 표
                    # 전용 지시(격자·실선 보존)로 재작도해 낙서·스캔 잡티를 지운 표를 넣는다.
                    tab["image"] = crop_path
                    tab["source"] = "image"
                    tab["width_mm"] = _natural_width_mm(crop_path, dpi)   # 원본 크기
                    if gemini_redraw:
                        redrawn, gerr = _gemini_auto_redraw(crop_path, kind="table")
                        if redrawn:
                            tab["image"], tab["source"] = redrawn, "gemini_redraw"
                        elif gerr:
                            tab["redraw_error"] = gerr
                elif tab["kind"] == "box":
                    # 테두리 상자(조건·과정 상자) → 줄 목록으로 읽어 1×1 표(셀 안
                    # 줄당 문단)로 원본 상자를 재현. 격자 구조화·충실도 채점은 grid
                    # 전용이라 생략(실패 시 이미지 폴백 → 갤러리 검수).
                    try:
                        lines = read_box_lines(crop_path, model=model)
                    except Exception as e:  # noqa: BLE001
                        lines = []
                        tab["error"] = f"상자 읽기 실패: {e}"
                    if lines:
                        tab["grid"] = [[{"text": "\n".join(lines)}]]
                        tab["source"] = "struct"
                    else:
                        tab["image"] = crop_path
                        tab["source"] = "image"
                        tab["reason"] = tab.get("error") or "상자 내용 없음"
                else:
                    try:                    # 표 영역 → Claude 비전 구조화(grid)
                        grid = table_to_grid(crop_path, model=model)
                    except Exception as e:  # noqa: BLE001
                        tab["error"] = f"표 추출 실패: {e}"
                        result.table_candidates.append(tab)
                        continue
                    rast, reason = should_rasterize(grid) if grid else (True, "구조 추출 실패")
                    fidelity = None
                    if grid and not rast:       # 1차 구조화 적합 → 충실도 채점(QA)
                        fidelity = verify_table(crop_path, grid, model=model)
                        fidelity["flagged"] = not passes(fidelity, TABLE_MIN_SCORE)
                        if fidelity["flagged"]:  # 충실도 미달 → 이미지 폴백(충실도 우선)
                            rast, reason = True, f"충실도 미달({fidelity['score']})"
                    if grid and not rast:
                        tab["grid"] = grid
                        tab["source"] = "struct"
                    else:                       # 과대/과병합/실패/충실도미달 → 이미지 폴백
                        tab["image"] = crop_path
                        tab["source"] = "image"
                        tab["reason"] = reason
                    if fidelity:
                        tab["fidelity"] = fidelity
                target = _match_problem(result.problems, pg.index, tg.problem, tg.bbox)
                idx = _index_of(result.problems, target) if target is not None else None
                if idx is not None:
                    result.problems[idx].tables.append(tab)
                    tab["assigned_to"] = target.number
                    tab["assigned_index"] = idx
                    # 상자 내용은 구조화가 발문에도 옮겨 적는다(격자 표의 '[표] 토큰만'
                    # 규칙 밖) → 상자 표 + 발문 텍스트로 이중 삽입되지 않게 의미 대조로
                    # 발문에서 제거. 실패 시 원본 발문 유지(함수 내부 폴백).
                    if tab.get("kind") == "box" and tab.get("source") == "struct":
                        result.problems[idx].stem = clean_stem_against_table(
                            result.problems[idx].stem, crop_path, model=model)
                else:
                    tab["error"] = "배정 문제 없음(미배정)"
                result.table_candidates.append(tab)
    return result


def analysis_to_problems(a: Analysis) -> list[dict]:
    out = []
    for p in a.problems:
        out.append({
            "school": p.school, "number": p.number, "code": p.code,
            "difficulty": p.difficulty, "points": p.points,
            "stem": p.stem, "choices": p.choices, "figures": list(p.figures),
            "tables": list(p.tables),
            "has_figure": p.has_figure, "has_table": p.has_table,
        })
    return out


def convert(path: str, out_hwpx: str, *, template: str, school: str = "",
            code: str = "", model: str | None = None, dpi: int = 300,
            work_dir: str | None = None,
            gemini_redraw: bool = True) -> tuple[BuildReport, Analysis]:
    """원클릭 변환: 분석(자동 그림·표 추출·재작도 포함) 후 바로 hwpx 생성.

    exam-engine 이식(2026-07-19): 그림·표는 크롭 후 Gemini image-to-image 재작도본을
    원본 크기(mm)로 넣는다(실패 시 크롭 폴백). 디지털 PDF 는 구조 기반 결정적 크롭.
    """
    a = analyze(path, school=school, code=code, model=model, detect_figs=True,
                auto_figures=True, redraw=False, crop_only=True, tables_as_image=True,
                gemini_redraw=gemini_redraw, dpi=dpi, work_dir=work_dir)
    rep = build_exam(template, analysis_to_problems(a), out_hwpx)
    return rep, a


if __name__ == "__main__":
    import sys
    from config import load_env
    load_env()
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/converted.hwpx"
    tpl = str(Path(__file__).resolve().parents[1] / "templates" / "base.hwpx")
    rep, a = convert(src, out, template=tpl, school="샘플고", code="DEMO")
    print(f"문제 {rep.problems}개, 수식 변환 {rep.equations_ok} OK / {rep.equations_fallback} 폴백")
    print("저장:", out)
