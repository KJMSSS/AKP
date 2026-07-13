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
                            detect_tables, table_to_grid, verify_table)
from .build_exam import build_exam, BuildReport
from .figure import crop_region, process_figure
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
            auto_figures: bool = True, redraw: bool = True,
            dpi: int = 300, do_preprocess: bool = False,
            work_dir: str | None = None, pages=None) -> Analysis:
    """OCR + 구조화 + (자동) 그림 추출·변환·문제연결.

    auto_figures=True 면 감지된 그림마다 고해상도 크롭 → (redraw=True 면) AI 재작도
    (실패 시 크롭 유지) → 소속 문제의 figures 에 첨부한다. 아무 이미지나 넣지 않고
    '대응 문제'에 변환된 그림을 넣는 것이 목적.
    do_preprocess: 기울기/노이즈 보정(깨끗한 스캔이면 불필요, 기본 off).
    """
    import tempfile
    from pathlib import Path
    # pages 가 주어지면(업로드→수동 회전 후 호출) 그대로 쓴다. 없으면 여기서 렌더.
    # 회전 보정은 분석 '전 단계'에서 사용자가 직접 맞춘다(자동 감지는 오판 위험이 커 폐기).
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
        if not detect_figs:
            continue
        for fi, fg in enumerate(detect_figures(pg.path, model=model)):
            cand = {"page": pg.index, "label": fg.label, "bbox": fg.bbox,
                    "problem": fg.problem, "image": None}
            if auto_figures:
                try:                       # 전체 공정: 크롭→잡티제거(LaMa)→재작도검증→폴백
                    pf = process_figure(pg.path, fg.bbox, str(figdir), model=model,
                                        redraw=redraw, prefix=f"p{pg.index}_f{fi}")
                    cand["image"] = pf["final"]
                    cand["source"] = pf["source"]   # crop | inpaint | redraw
                    if pf.get("verdict"):           # 재작도 충실도(검증 통과분)
                        v = dict(pf["verdict"])
                        v["flagged"] = not passes(v, FIGURE_MIN_SCORE)
                        cand["fidelity"] = v
                    if pf.get("redraw_error"):
                        cand["redraw_error"] = pf["redraw_error"]
                except Exception as e:  # noqa: BLE001 (한 그림만 건너뜀, 표면화)
                    cand["error"] = f"그림 처리 실패: {e}"
                if cand.get("image"):
                    target = _match_problem(result.problems, pg.index, fg.problem, fg.bbox)
                    idx = _index_of(result.problems, target) if target is not None else None
                    if idx is not None:
                        result.problems[idx].figures.append(cand["image"])
                        cand["assigned_to"] = target.number
                        cand["assigned_index"] = idx
                    else:
                        cand["error"] = (cand.get("error") or "") + " 배정 문제 없음(미배정)"
            result.figure_candidates.append(cand)

        if detect_tabs:
            for ti, tg in enumerate(detect_tables(pg.path, model=model)):
                tab = {"page": pg.index, "label": tg.label, "bbox": tg.bbox,
                       "problem": tg.problem, "source": None}
                crop_path = str(tabdir / f"p{pg.index}_t{ti}_crop.png")
                try:                        # 표 영역 크롭 → Claude 비전 구조화
                    crop_region(pg.path, tg.bbox, crop_path, pad=0.012)
                    grid = table_to_grid(crop_path, model=model)
                except Exception as e:  # noqa: BLE001 (표 하나만 건너뜀, 표면화)
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
            work_dir: str | None = None) -> tuple[BuildReport, Analysis]:
    """원클릭 변환: 분석(자동 그림 추출·변환 포함) 후 바로 hwpx 생성."""
    a = analyze(path, school=school, code=code, model=model, detect_figs=True,
                auto_figures=True, redraw=True, dpi=dpi, work_dir=work_dir)
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
