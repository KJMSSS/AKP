"""
HWPX 표(hp:tbl) 생성기 — 임의 RxC 표를 본문에 인라인 주입.

base.hwpx 구조 분석 결과(설계 근거):
  - 일반 표는 `hp:p > hp:run > hp:tbl`(pos treatAsChar="1") 형태로 본문에 직접 박힌다.
    따라서 build() 는 hp:p 를 반환하고 HwpxBuilder.add_block() 으로 그대로 주입한다.
  - 셀 테두리는 header.xml 의 borderFill id=2 (4변 SOLID)가 표준 격자다.
  - 셀 자식 순서(스키마 엄격): subList → cellAddr → cellSpan → cellSz → cellMargin.
  - 셀 내용: hp:tc > hp:subList > hp:p > hp:run > hp:t.
  - linesegarray(레이아웃 캐시)는 생략한다 — 한글이 문서 열 때 재계산한다
    (assemble_hwpx.add_text 도 동일하게 생략하고 검증 통과).

병합셀: cellSpan colSpan/rowSpan 으로 표현하고, 병합으로 가려지는 셀(좌상단 제외)은
HWPX 규칙상 tc 를 생략한다. _layout_grid 가 좌표를 계산한다.

run 모델은 blocks.py 와 동일:
  {"type":"text","text":...} | {"type":"eqn","hwp_script":...|"latex":...}

하이브리드 정책(examconv 충실도 우선): 표가 너무 크거나 병합이 과도하면 구조 복원
충실도가 떨어지므로 should_rasterize() 가 True 를 돌려주고, 호출부는 표 영역을
고해상도 이미지로 add_figure() 삽입하는 폴백으로 전환한다.

순수 표준 라이브러리(xml.etree + html.parser)만 사용.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser

from .assemble_hwpx import NS, _q, set_equation_script, harvest_equation

# 표준 격자 테두리(4변 실선) — header.xml 분석 결과 id=2
BORDERFILL_GRID = "2"
# A3 세로 2단 한 단 폭(HWPUNIT). secPr 계산: (본문 237mm - 단간격 8mm)/2 ≈ 114mm.
# 템플릿 4×5 본문표 width=113.2mm 와 일치 → 표가 이 폭을 넘으면 단을 깨므로 상한값.
COLUMN_WIDTH = 32000
MIN_COL_WIDTH = 3600               # 셀 최소 폭(≈13mm)
CHAR_WIDTH = 850                   # 글자폭 근사(한글 1글자 기준)
CELL_PAD = 1400                    # 셀 좌우 여백+숨통
DEFAULT_ROW_HEIGHT = 2600          # 행 높이(≈9mm, 한글이 내용 맞게 재계산)
CELL_MARGIN = {"left": "510", "right": "510", "top": "141", "bottom": "141"}
OUT_MARGIN = {"left": "283", "right": "283", "top": "283", "bottom": "283"}
BODY_CHAR_PR = "8"                 # 본문 글자 스타일(blocks.BODY_CHAR_PR 과 일치)
CELL_PARA_PR = "0"


# --------------------------------------------------------------------------
# 입력 정규화 + 그리드 배치(병합셀 좌표 계산)
# --------------------------------------------------------------------------
def _text_to_runs(text: str) -> list[dict]:
    """셀 텍스트의 인라인 수식 \\( \\)/\\[ \\]/$...$ 을 분해해 run 리스트로.

    수식은 mathconv.latex_to_hwp 로 한글 수식 스크립트로 변환한다(충실 재현).
    변환 실패(미지원 명령/불균형)는 LaTeX 원문 텍스트로 폴백한다.
    """
    if not text or not text.strip():
        return []
    from .ocr_mathpix import mmd_to_runs
    try:
        from ..mathconv.latex_to_hwp import latex_to_hwp
    except (ImportError, ValueError):
        from mathconv.latex_to_hwp import latex_to_hwp

    out: list[dict] = []
    for r in mmd_to_runs(text):
        if r.get("type") == "eqn":
            res = latex_to_hwp(r.get("latex", ""))
            if res.ok and res.script:
                out.append({"type": "eqn", "hwp_script": res.script})
            else:
                out.append({"type": "text", "text": r.get("latex", "")})  # 폴백
        elif r.get("text", "").strip() or r.get("text"):
            out.append({"type": "text", "text": r["text"]})
    return out


def _normalize(rows) -> list[list[dict]]:
    """다양한 셀 표기를 표준 dict 로 정규화.

    허용 입력(셀 단위):
      "텍스트"  |  123(숫자)
      {"text":..., "colspan":, "rowspan":}        # text 의 \\(..\\) 수식은 자동 변환
      {"runs":[run...], "colspan":, "rowspan":}    # 이미 분해된 run 은 그대로
    """
    out: list[list[dict]] = []
    for row in rows:
        nr: list[dict] = []
        for cell in row:
            cs = rs = 1
            if isinstance(cell, dict):
                cs = int(cell.get("colspan", 1) or 1)
                rs = int(cell.get("rowspan", 1) or 1)
                if "runs" in cell:
                    runs = list(cell["runs"])
                else:
                    runs = _text_to_runs(str(cell.get("text", "")))
            else:
                runs = _text_to_runs("" if cell is None else str(cell))
            nr.append({"runs": runs, "colspan": max(1, cs), "rowspan": max(1, rs)})
        out.append(nr)
    return out


def _layout_grid(norm_rows: list[list[dict]]):
    """병합을 고려해 각 셀의 실제 (row, col) 좌표를 계산.

    반환: (ncols, nrows, placements). placements 항목은
    {"r","c","colspan","rowspan","runs"} — 좌상단(대표) 셀만 포함.
    """
    occ: dict[tuple[int, int], bool] = {}
    placements: list[dict] = []
    for r, row in enumerate(norm_rows):
        c = 0
        for cell in row:
            while occ.get((r, c)):
                c += 1
            cs, rs = cell["colspan"], cell["rowspan"]
            placements.append({"r": r, "c": c, "colspan": cs, "rowspan": rs,
                               "runs": cell["runs"]})
            for dr in range(rs):
                for dc in range(cs):
                    occ[(r + dr, c + dc)] = True
            c += cs
    nrows = max((p["r"] + p["rowspan"] for p in placements), default=len(norm_rows))
    ncols = max((p["c"] + p["colspan"] for p in placements), default=0)
    return ncols, nrows, placements


def _cell_textlen(runs) -> int:
    """셀 표시 길이 추정. 한글/전각은 2, 그 외 1로 근사. 수식은 스크립트 길이 반영."""
    n = 0
    for r in runs:
        if r.get("type") == "eqn":
            n += len(r.get("hwp_script") or r.get("latex") or "")
        else:
            n += sum(2 if ord(ch) > 0x1100 else 1 for ch in r.get("text", ""))
    return n


def _estimate_col_widths(placements, ncols: int, max_total: int) -> list[int]:
    """셀 내용 길이에 비례해 열 너비를 잡되, 합계가 단 폭(max_total)을 넘지 않게 한다.

    충실도/가독성 목적: 숫자만 든 좁은 표는 단보다 좁게(왼쪽 정렬), 내용이 길면
    넓게. colspan>1 셀은 단일 열 폭 추정에서 제외(여러 열에 걸쳐 왜곡 방지).
    """
    col_units = [0] * ncols
    for p in placements:
        if p["colspan"] == 1 and 0 <= p["c"] < ncols:
            col_units[p["c"]] = max(col_units[p["c"]], _cell_textlen(p["runs"]))
    widths = [max(MIN_COL_WIDTH, u * CHAR_WIDTH + CELL_PAD) for u in col_units]
    total = sum(widths)
    if total > max_total:                      # 단 폭 초과 → 비례 축소
        scaled = [max(MIN_COL_WIDTH // 2, w * max_total // total) for w in widths]
        scaled[-1] += max_total - sum(scaled)  # 반올림 잔여 보정
        widths = scaled
    return widths


# --------------------------------------------------------------------------
# 표 빌더
# --------------------------------------------------------------------------
@dataclass
class TableBuilder:
    """RxC hp:tbl 단락을 생성한다. 셀 내 수식 골격을 위해 템플릿 경로를 받는다."""

    template_path: str
    _eq_tpl: ET.Element = field(init=False, default=None)
    _counter: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        try:
            self._eq_tpl = harvest_equation(self.template_path)
        except Exception:
            self._eq_tpl = None

    def _next_id(self) -> str:
        self._counter += 1
        return str(1500000000 + self._counter)

    def build(self, rows, *, max_width: int = COLUMN_WIDTH,
              row_height: int = DEFAULT_ROW_HEIGHT, char_pr: str = BODY_CHAR_PR,
              para_pr: str = CELL_PARA_PR, col_widths: list[int] | None = None) -> ET.Element:
        """표 데이터(rows) → hp:p(>run>tbl) 요소. add_block() 으로 주입 가능.

        max_width: 표 전체 폭 상한(기본 = 한 단 폭). 셀 내용 비례로 열 폭을 잡되
        합계가 이 값을 넘지 않게 한다. col_widths 를 직접 주면 그 값을 사용.
        """
        norm = _normalize(rows)
        ncols, nrows, placements = _layout_grid(norm)
        if ncols == 0 or nrows == 0:
            raise ValueError("빈 표는 생성할 수 없습니다.")

        # 열 너비: 지정값 우선, 없으면 셀 내용 기반 자동(단 폭 max_width 이내)
        if col_widths and len(col_widths) == ncols:
            col_w = list(col_widths)
        else:
            col_w = _estimate_col_widths(placements, ncols, max_width)
        total_width = sum(col_w)
        total_h = row_height * nrows

        tbl = ET.Element(_q("hp", "tbl"), {
            "id": self._next_id(), "zOrder": "0", "numberingType": "TABLE",
            "textWrap": "SQUARE", "textFlow": "BOTH_SIDES", "lock": "0",
            "dropcapstyle": "None", "pageBreak": "CELL", "repeatHeader": "0",
            "rowCnt": str(nrows), "colCnt": str(ncols), "cellSpacing": "0",
            "borderFillIDRef": BORDERFILL_GRID, "noAdjust": "0",
        })
        ET.SubElement(tbl, _q("hp", "sz"), {
            "width": str(total_width), "widthRelTo": "ABSOLUTE",
            "height": str(total_h), "heightRelTo": "ABSOLUTE", "protect": "0"})
        ET.SubElement(tbl, _q("hp", "pos"), {
            "treatAsChar": "1", "affectLSpacing": "0", "flowWithText": "1",
            "allowOverlap": "0", "holdAnchorAndSO": "0", "vertRelTo": "PARA",
            "horzRelTo": "COLUMN", "vertAlign": "TOP", "horzAlign": "LEFT",
            "vertOffset": "0", "horzOffset": "0"})
        ET.SubElement(tbl, _q("hp", "outMargin"), dict(OUT_MARGIN))
        ET.SubElement(tbl, _q("hp", "inMargin"), dict(CELL_MARGIN))

        by_row: dict[int, list[dict]] = {}
        for p in placements:
            by_row.setdefault(p["r"], []).append(p)
        for r in range(nrows):
            tr = ET.SubElement(tbl, _q("hp", "tr"))
            for p in sorted(by_row.get(r, []), key=lambda x: x["c"]):
                self._cell(tr, p, col_w, row_height, char_pr, para_pr)

        # hp:p > hp:run > hp:tbl (+ 빈 t 앵커, 샘플과 동일)
        para = ET.Element(_q("hp", "p"), {
            "paraPrIDRef": "0", "styleIDRef": "0",
            "pageBreak": "0", "columnBreak": "0", "merged": "0"})
        run = ET.SubElement(para, _q("hp", "run"), {"charPrIDRef": char_pr})
        run.append(tbl)
        ET.SubElement(run, _q("hp", "t"))
        return para

    def _cell(self, tr, p, col_w, row_height, char_pr, para_pr) -> None:
        cs, rs = p["colspan"], p["rowspan"]
        w = sum(col_w[p["c"]:p["c"] + cs])
        h = row_height * rs
        tc = ET.SubElement(tr, _q("hp", "tc"), {
            "name": "", "header": "0", "hasMargin": "0", "protect": "0",
            "editable": "0", "dirty": "0", "borderFillIDRef": BORDERFILL_GRID})
        sub = ET.SubElement(tc, _q("hp", "subList"), {
            "id": "", "textDirection": "HORIZONTAL", "lineWrap": "BREAK",
            "vertAlign": "CENTER", "linkListIDRef": "0", "linkListNextIDRef": "0",
            "textWidth": "0", "textHeight": "0", "hasTextRef": "0", "hasNumRef": "0"})
        cp = ET.SubElement(sub, _q("hp", "p"), {
            "id": "0", "paraPrIDRef": para_pr, "styleIDRef": "0",
            "pageBreak": "0", "columnBreak": "0", "merged": "0"})
        self._fill_runs(cp, p["runs"], char_pr)
        # 자식 순서 엄수: subList → cellAddr → cellSpan → cellSz → cellMargin
        ET.SubElement(tc, _q("hp", "cellAddr"), {"colAddr": str(p["c"]), "rowAddr": str(p["r"])})
        ET.SubElement(tc, _q("hp", "cellSpan"), {"colSpan": str(cs), "rowSpan": str(rs)})
        ET.SubElement(tc, _q("hp", "cellSz"), {"width": str(w), "height": str(h)})
        ET.SubElement(tc, _q("hp", "cellMargin"), dict(CELL_MARGIN))

    def _fill_runs(self, p, runs, char_pr) -> None:
        if not runs:
            r = ET.SubElement(p, _q("hp", "run"), {"charPrIDRef": char_pr})
            ET.SubElement(r, _q("hp", "t"))
            return
        for run in runs:
            r = ET.SubElement(p, _q("hp", "run"), {"charPrIDRef": char_pr})
            if run.get("type") == "eqn" and self._eq_tpl is not None and run.get("hwp_script"):
                r.append(set_equation_script(self._eq_tpl, run["hwp_script"]))
            else:
                t = ET.SubElement(r, _q("hp", "t"))
                t.text = run.get("text") or run.get("latex") or ""


# --------------------------------------------------------------------------
# Mathpix 표 HTML 파서
# --------------------------------------------------------------------------
class _HTMLTableParser(HTMLParser):
    """<table> 의 tr/td/th(+colspan/rowspan)를 rows 로 추출."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[dict]] = []
        self._row: list[dict] | None = None
        self._cell: dict | None = None
        self._buf: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._depth += 1
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = {"colspan": int(a.get("colspan", 1) or 1),
                          "rowspan": int(a.get("rowspan", 1) or 1)}
            self._buf = []
        elif tag == "br" and self._cell is not None:
            self._buf.append("\n")

    def handle_data(self, data):
        if self._cell is not None:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._cell["text"] = "".join(self._buf).strip()
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
        elif tag == "table":
            self._depth -= 1


def html_table_to_rows(html: str) -> list[list[dict]]:
    """Mathpix(또는 임의) HTML <table> → rows([[{text,colspan,rowspan}...]...])."""
    p = _HTMLTableParser()
    p.feed(html or "")
    return p.rows


def markdown_table_to_rows(md: str) -> list[list[dict]]:
    """Mathpix Markdown(MMD)의 파이프 표(| a | b |) → rows.

    구분선 행(|---|:--:|)은 건너뛴다. HTML 표가 없을 때의 폴백 경로로,
    키 없이도(이미 받은 text 만으로) 표를 복원할 수 있다. 병합셀은 표현 불가
    (마크다운 한계) → 단순 격자만.
    """
    rows: list[list[dict]] = []
    for line in (md or "").splitlines():
        s = line.strip()
        if not (s.startswith("|") and s.endswith("|")):
            continue
        cells = [c.strip() for c in s[1:-1].split("|")]
        if cells and all(c and set(c) <= set("-: ") for c in cells):
            continue  # 정렬 구분선 행
        rows.append([{"text": c, "colspan": 1, "rowspan": 1} for c in cells])
    return rows


# --------------------------------------------------------------------------
# 하이브리드 판단: 구조 복원 vs 이미지 삽입
# --------------------------------------------------------------------------
def should_rasterize(rows, *, max_cells: int = 80, max_cols: int = 12,
                     max_rows: int = 25, span_ratio: float = 0.5) -> tuple[bool, str]:
    """충실도 우선 정책. True 면 호출부가 표를 이미지로 삽입해야 한다.

    기준: (1) 표가 과대(셀 수/행/열) → 2단 폭 초과로 깨질 위험,
          (2) 병합셀 비율 과도 → 구조 복원 충실도 저하.
    """
    try:
        norm = _normalize(rows)
        ncols, nrows, pl = _layout_grid(norm)
    except Exception as e:
        return True, f"파싱 실패({e})"
    if ncols == 0 or nrows == 0:
        return True, "빈 표"
    if ncols > max_cols or nrows > max_rows or ncols * nrows > max_cells:
        return True, f"표 과대({nrows}x{ncols}, {ncols*nrows}셀)"
    spanned = sum(1 for p in pl if p["colspan"] > 1 or p["rowspan"] > 1)
    if pl and spanned / len(pl) > span_ratio:
        return True, f"병합셀 과다({spanned}/{len(pl)})"
    return False, f"구조화 적합({nrows}x{ncols})"
