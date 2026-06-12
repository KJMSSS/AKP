"""
HWPX 표/글상자 삽입 모듈.

세 가지 기능:
  1. replace_condition_tables()  — 【★ 조건시작:N번】/【★ 조건끝:N번】 마커를 1×1 박스 표로 교체
  2. replace_boilerplate_tables() — 【★ 보기시작:N번】/【★ 보기끝:N번】 마커를 1×1 박스 표로 교체
  3. replace_placeholder_with_data_table() — 【★ 데이터표:N번】을 N×M 표로 교체

사용 방법 (빌드 스크립트):
    from src.common.hwpx_table_inserter import replace_condition_tables, TableSpec, replace_placeholder_with_data_table

    replace_condition_tables(out_hwpx)     # 조건 표 (자동)
    replace_boilerplate_tables(out_hwpx)   # 보기 표 (자동)
    replace_placeholder_with_data_table(   # 데이터 표 (수동 지정)
        out_hwpx,
        TableSpec(item="19", headers=["헤더1","헤더2"], rows=[["a","b"]], col_widths=[24000,24000]),
    )
"""
from __future__ import annotations

import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

# 표 템플릿 빌더 (samples/templates/table_templates.json 있으면 자동 적용)
try:
    from src.common.table_template_builder import (
        build_condition_box as _tpl_condition_box,
        build_boilerplate_box as _tpl_boilerplate_box,
        build_data_table as _tpl_data_table,
        get_default_templates as _get_templates,
        DataTableSpec as _DataTableSpec,
    )
    _TEMPLATE_SUPPORT = True
except ImportError:
    _TEMPLATE_SUPPORT = False


# ── XML 템플릿 ─────────────────────────────────────────────────────────────

# 1×1 박스 표 (조건/보기) — borderFillIDRef=13 (외곽 NONE), borderFillIDRef=12 (셀 SOLID)
_COND_TBL_OPEN = (
    '<hp:tbl id="{tbl_id}" zOrder="{zo}" numberingType="PICTURE" '
    'textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" '
    'pageBreak="CELL" repeatHeader="1" rowCnt="1" colCnt="1" cellSpacing="0" '
    'borderFillIDRef="13" noAdjust="0">'
    '<hp:sz width="29190" widthRelTo="ABSOLUTE" height="{height}" heightRelTo="ABSOLUTE" protect="0"/>'
    '<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
    'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="PARA" vertAlign="TOP" '
    'horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
    '<hp:outMargin left="0" right="0" top="0" bottom="0"/>'
    '<hp:shapeComment>{label}</hp:shapeComment>'
    '<hp:inMargin left="0" right="0" top="1133" bottom="1133"/>'
    '<hp:tr>'
    '<hp:tc name="" header="0" hasMargin="0" protect="0" editable="0" dirty="0" borderFillIDRef="12">'
    '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" '
    'linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
)
_COND_TBL_CLOSE = (
    '</hp:subList>'
    '<hp:cellAddr colAddr="0" rowAddr="0"/>'
    '<hp:cellSpan colSpan="1" rowSpan="1"/>'
    '<hp:cellSz width="29190" height="282"/>'
    '<hp:cellMargin left="510" right="510" top="141" bottom="141"/>'
    '</hp:tc>'
    '</hp:tr>'
    '</hp:tbl>'
)

# 박스 표를 담는 외부 단락 (treatAsChar=1 → 인라인 글자처럼)
_COND_PARA = (
    '<hp:p id="{pid}" paraPrIDRef="{ppr}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
    '<hp:run charPrIDRef="{cpr}">'
    '{tbl_xml}'
    '<hp:t/>'
    '</hp:run>'
    '<hp:linesegarray>'
    '<hp:lineseg textpos="0" vertpos="0" vertsize="{height}" textheight="{height}" '
    'baseline="{bl}" spacing="720" horzpos="0" horzsize="48189" flags="393216"/>'
    '</hp:linesegarray>'
    '</hp:p>'
)

# N×M 데이터 표 — borderFillIDRef=2 (SOLID), 셀=borderFillIDRef=4
_DATA_TBL_OPEN = (
    '<hp:tbl id="{tbl_id}" zOrder="{zo}" numberingType="TABLE" '
    'textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" '
    'pageBreak="CELL" repeatHeader="1" rowCnt="{rows}" colCnt="{cols}" cellSpacing="0" '
    'borderFillIDRef="2" noAdjust="0">'
    '<hp:sz width="{total_w}" widthRelTo="ABSOLUTE" height="{total_h}" heightRelTo="ABSOLUTE" protect="0"/>'
    '<hp:pos treatAsChar="0" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
    'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" '
    'horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
    '<hp:outMargin left="283" right="283" top="283" bottom="283"/>'
    '<hp:inMargin left="510" right="510" top="141" bottom="141"/>'
)
_DATA_TBL_CLOSE = '</hp:tbl>'

_DATA_CELL = (
    '<hp:tc name="" header="0" hasMargin="0" protect="0" editable="0" dirty="0" borderFillIDRef="{bfid}">'
    '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" '
    'linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
    '<hp:p id="0" paraPrIDRef="2" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
    '<hp:run charPrIDRef="13"><hp:t>{text}</hp:t></hp:run>'
    '<hp:linesegarray>'
    '<hp:lineseg textpos="0" vertpos="0" vertsize="1900" textheight="1900" '
    'baseline="1615" spacing="572" horzpos="0" horzsize="{inner_w}" flags="393216"/>'
    '</hp:linesegarray>'
    '</hp:p>'
    '</hp:subList>'
    '<hp:cellAddr colAddr="{col}" rowAddr="{row}"/>'
    '<hp:cellSpan colSpan="1" rowSpan="1"/>'
    '<hp:cellSz width="{cell_w}" height="{row_h}"/>'
    '<hp:cellMargin left="510" right="510" top="141" bottom="141"/>'
    '</hp:tc>'
)

# 데이터 표를 담는 외부 단락 (block-level, treatAsChar=0)
_DATA_PARA = (
    '<hp:p id="{pid}" paraPrIDRef="8" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
    '<hp:run charPrIDRef="0">'
    '{tbl_xml}'
    '<hp:t/>'
    '</hp:run>'
    '<hp:linesegarray/>'
    '</hp:p>'
)


@dataclass
class TableSpec:
    item: str                     # 플레이스홀더 문제 번호 (예: "19")
    headers: list[str]            # 헤더 행 (빈 리스트 = 헤더 없음)
    rows: list[list[str]]         # 데이터 행들
    col_widths: list[int]         # 열 너비 HWP 단위 (합계 ≤ 48189)
    row_height: int = 2400        # 행 높이 HWP 단위 (기본 2400)


# ── 내부 유틸 ──────────────────────────────────────────────────────────────

def _max_ids(xml: str) -> tuple[int, int]:
    """현재 XML에서 최대 hp:p id와 zOrder 반환."""
    pids = [int(m) for m in re.findall(r'<hp:p id="(\d+)"', xml) if int(m) < 2_000_000_000]
    zos  = [int(m) for m in re.findall(r'zOrder="(\d+)"', xml)]
    return (max(pids) if pids else 200), (max(zos) if zos else 200)


def _extract_lineseg_heights(para_xml: str) -> list[int]:
    """단락 XML에서 lineseg vertsize 값 목록 추출."""
    return [int(m) for m in re.findall(r'vertsize="(\d+)"', para_xml)]


def _split_top_level_paras(xml_fragment: str) -> list[str]:
    """xml_fragment 내 최상위 hp:p 요소들을 추출.

    주의: <hp:pic>, <hp:pos> 등 <hp:p로 시작하는 다른 태그와 구별하기 위해
    6번째 문자가 공백 또는 >인지 확인.
    """
    result = []
    i = 0
    n = len(xml_fragment)

    def is_para_open(pos: int) -> bool:
        if xml_fragment[pos:pos+5] != '<hp:p':
            return False
        c = xml_fragment[pos+5:pos+6]
        return c in (' ', '>')

    def is_para_close(pos: int) -> bool:
        return xml_fragment[pos:pos+7] == '</hp:p>'

    while i < n:
        if is_para_open(i):
            depth = 0
            j = i
            while j < n:
                if is_para_open(j):
                    depth += 1; j += 5
                elif is_para_close(j):
                    depth -= 1
                    if depth == 0:
                        result.append(xml_fragment[i:j + 7])
                        i = j + 7
                        break
                    j += 7
                else:
                    j += 1
            else:
                break
        else:
            i += 1
    return result


def _compute_box_height(paras: list[str]) -> int:
    """조건/보기 박스 높이 계산 (inMargin 2266 + 줄높이 합계 + 간격)."""
    heights = []
    for p in paras:
        vs = _extract_lineseg_heights(p)
        heights.append(max(vs) if vs else 1200)
    n = len(heights)
    content_h = sum(heights) + max(0, n - 1) * 720
    return content_h + 2266  # 2 × inMargin(1133)


def _build_condition_table_xml(
    content_paras: list[str],
    tbl_id: int,
    zo: int,
    label: str,
    kind: str = "조건",
) -> tuple[str, int]:
    """1×1 박스 표 XML 생성. (tbl_xml, height) 반환.
    템플릿 있으면 우선 사용, 없으면 하드코딩 fallback.
    """
    if _TEMPLATE_SUPPORT:
        templates = _get_templates()
        if kind == "보기":
            tbl_xml, height = _tpl_boilerplate_box(templates, content_paras, tbl_id, zo)
        else:
            tbl_xml, height = _tpl_condition_box(templates, content_paras, tbl_id, zo)
        if tbl_xml:
            return tbl_xml, height

    # fallback: 기존 하드코딩 XML
    height = _compute_box_height(content_paras)
    inner = "".join(content_paras)
    tbl_xml = (
        _COND_TBL_OPEN.format(tbl_id=tbl_id, zo=zo, height=height, label=label)
        + inner
        + _COND_TBL_CLOSE
    )
    return tbl_xml, height


def _build_data_table_xml(
    spec: TableSpec,
    tbl_id: int,
    zo: int,
) -> tuple[str, int]:
    """N×M 데이터 표 XML 생성. (tbl_xml, total_height) 반환.
    템플릿 있으면 우선 사용, 없으면 하드코딩 fallback.
    """
    if _TEMPLATE_SUPPORT:
        templates = _get_templates()
        dspec = _DataTableSpec(
            item=spec.item,
            headers=spec.headers,
            rows=spec.rows,
            col_widths=spec.col_widths,
            row_height=spec.row_height,
        )
        tbl_xml, total_h = _tpl_data_table(templates, dspec, tbl_id, zo)
        if tbl_xml:
            return tbl_xml, total_h
    all_rows: list[list[str]] = []
    header_flag: list[bool] = []
    if spec.headers:
        all_rows.append(spec.headers)
        header_flag.append(True)
    for r in spec.rows:
        all_rows.append(r)
        header_flag.append(False)

    n_rows = len(all_rows)
    n_cols = len(spec.col_widths)
    total_w = sum(spec.col_widths)
    rh = spec.row_height
    total_h = n_rows * rh

    cells_xml = ""
    for r_idx, (row, is_hdr) in enumerate(zip(all_rows, header_flag)):
        cells_xml += "<hp:tr>"
        bfid = "5" if is_hdr else "4"
        for c_idx, (text, cw) in enumerate(zip(row, spec.col_widths)):
            inner_w = max(100, cw - 1020)
            cells_xml += _DATA_CELL.format(
                bfid=bfid,
                text=text,
                inner_w=inner_w,
                col=c_idx, row=r_idx,
                cell_w=cw, row_h=rh,
            )
        cells_xml += "</hp:tr>"

    tbl_xml = (
        _DATA_TBL_OPEN.format(
            tbl_id=tbl_id, zo=zo,
            rows=n_rows, cols=n_cols,
            total_w=total_w, total_h=total_h,
        )
        + cells_xml
        + _DATA_TBL_CLOSE
    )
    return tbl_xml, total_h


def _rewrite_hwpx(
    hwpx_path: Path,
    xml_new: str,
    out_path: Path,
) -> None:
    """section0.xml을 교체한 HWPX를 out_path에 저장."""
    tmp = hwpx_path.with_suffix(".tmp2.hwpx")
    with zipfile.ZipFile(hwpx_path, "r") as src:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                if item.filename == "Contents/section0.xml":
                    dst.writestr(item, xml_new.encode("utf-8"))
                else:
                    dst.writestr(item, src.read(item.filename))
    shutil.move(str(tmp), str(out_path))


# ── 공개 API ──────────────────────────────────────────────────────────────

def replace_condition_tables(
    hwpx_path: Path,
    out_path: Path | None = None,
) -> int:
    """
    【★ 조건시작:N번】 ~ 【★ 조건끝:N번】 마커 쌍을 1×1 박스 표로 교체.
    반환: 교체된 건수
    """
    return _replace_box_tables(hwpx_path, "조건", out_path or hwpx_path)


def replace_boilerplate_tables(
    hwpx_path: Path,
    out_path: Path | None = None,
) -> int:
    """
    【★ 보기시작:N번】 ~ 【★ 보기끝:N번】 마커 쌍을 1×1 박스 표로 교체.
    반환: 교체된 건수
    """
    return _replace_box_tables(hwpx_path, "보기", out_path or hwpx_path)


def _rfind_para_open(xml: str, end: int) -> int:
    """end 이전에서 가장 가까운 진짜 <hp:p 단락 여는 태그 위치 (없으면 -1).

    <hp:pPr/<hp:pos/<hp:pic 등 '<hp:p' 접두만 같은 태그는 건너뛴다
    (_split_top_level_paras와 동일 규칙).
    """
    pos = end
    while True:
        pos = xml.rfind("<hp:p", 0, pos)
        if pos < 0:
            return -1
        if xml[pos + 5:pos + 6] in (" ", ">"):
            return pos


def _replace_box_tables(
    hwpx_path: Path,
    kind: str,   # "조건" or "보기"
    out_path: Path,
) -> int:
    with zipfile.ZipFile(hwpx_path, "r") as zf:
        xml = zf.read("Contents/section0.xml").decode("utf-8")

    start_prefix = f"【★ {kind}시작:"
    end_prefix   = f"【★ {kind}끝:"
    start_re = re.compile(re.escape(start_prefix) + r"(\d+)번】")

    if start_prefix not in xml and end_prefix not in xml:
        return 0

    replaced = 0
    failed   = 0
    modified = False  # 표 생성 없이 xml만 바뀐 경우에도 재기록 필요
    # 반복 처리 (여러 쌍)
    while True:
        sm = start_re.search(xml)
        if sm is None:
            break
        num        = sm.group(1)
        start_text = sm.group(0)
        end_text   = f"【★ {kind}끝:{num}번】"
        sm_idx     = sm.start()

        def _drop_start_marker(reason: str) -> None:
            """쌍이 불완전한 시작 마커를 문서에서 제거 (내용은 보존)."""
            nonlocal xml, failed, modified
            print(f"  [table] 경고: {kind} {num}번 — {reason}. 마커 제거, 표 미생성 (내용 보존)")
            xml = xml.replace(start_text, "", 1)
            failed += 1
            modified = True

        # 시작 마커 단락 경계
        p_s_start = _rfind_para_open(xml, sm_idx)
        p_s_close = xml.find("</hp:p>", sm_idx)
        if p_s_start < 0 or p_s_close < 0:
            _drop_start_marker("시작 마커 단락 경계 탐색 실패")
            continue
        p_s_end = p_s_close + 7

        # 끝 마커: 반드시 같은 번호와 정확 매칭 — 번호 불일치 페어링이
        # 이웃 문제를 통째로 박스에 흡수하는 사고 방지
        em_idx = xml.find(end_text, p_s_end)
        if em_idx < 0:
            _drop_start_marker(f"같은 번호의 끝 마커({end_text}) 없음")
            continue
        p_e_start = _rfind_para_open(xml, em_idx)
        p_e_close = xml.find("</hp:p>", em_idx)
        if p_e_start < 0 or p_e_close < 0 or p_e_start < p_s_end:
            _drop_start_marker("끝 마커 단락 경계 탐색 실패")
            continue
        p_e_end = p_e_close + 7

        # 시작~끝 사이 단락들 추출
        between = xml[p_s_end:p_e_start]
        content_paras = _split_top_level_paras(between)

        if not content_paras:
            # 사이 단락 없음 — 마커 단락 자체에 내용이 합쳐졌는지 검사
            seg_texts = "".join(
                re.findall(r"<hp:t[^>]*>([^<]*)</hp:t>", xml[p_s_start:p_e_end])
            )
            leftover = seg_texts.replace(start_text, "").replace(end_text, "").strip()
            if leftover:
                print(f"  [table] 경고: {kind} {num}번 마커 단락에 내용이 붙어 있음 — 마커만 제거, 내용 보존")
                xml = xml.replace(start_text, "", 1).replace(end_text, "", 1)
                failed += 1
            else:
                # 빈 마커 쌍 — 두 단락 제거
                xml = xml[:p_s_start] + xml[p_e_end:]
            modified = True
            continue

        max_pid, max_zo = _max_ids(xml)
        tbl_id = max_pid + 2_000_000
        zo     = max_zo + 1
        pid    = max_pid + 1

        tbl_xml, height = _build_condition_table_xml(content_paras, tbl_id, zo, kind, kind=kind)
        bl = round(height * 0.85)

        # 기존 단락 스타일 (시작 마커 단락에서 추출)
        ppr_m = re.search(r'paraPrIDRef="(\d+)"', xml[p_s_start:p_s_end])
        cpr_m = re.search(r'charPrIDRef="(\d+)"', xml[p_s_start:p_s_end])
        ppr = ppr_m.group(1) if ppr_m else "8"
        cpr = cpr_m.group(1) if cpr_m else "0"

        new_para = _COND_PARA.format(
            pid=pid, ppr=ppr, cpr=cpr,
            tbl_xml=tbl_xml,
            height=height, bl=bl,
        )
        xml = xml[:p_s_start] + new_para + xml[p_e_end:]
        replaced += 1

    # 짝 없는 끝 마커 정리 (시작 마커 소실 케이스) — 마커 리터럴 노출 방지
    stray_end_re = re.compile(re.escape(end_prefix) + r"\d+번】")
    stray_ends = stray_end_re.findall(xml)
    if stray_ends:
        print(f"  [table] 경고: 짝 없는 {kind} 끝 마커 {len(stray_ends)}건 제거: {stray_ends}")
        xml = stray_end_re.sub("", xml)
        failed += len(stray_ends)
        modified = True

    # 번호 없는 비정형 마커가 남아 있으면 알린다 (마커 텍스트 손상 케이스)
    if start_prefix in xml:
        print(f"  [table] 경고: 번호를 읽을 수 없는 {kind} 시작 마커 잔존 — 원문 확인 필요")

    if replaced or modified:
        _rewrite_hwpx(hwpx_path, xml, out_path)
        print(f"  [table] {kind} 박스 표: {replaced}건 삽입" + (f", 실패 {failed}건" if failed else ""))
    return replaced


def replace_placeholder_with_data_table(
    hwpx_path: Path,
    spec: TableSpec,
    out_path: Path | None = None,
) -> Path:
    """
    【★ 데이터표:N번】 단락을 N×M 데이터 표로 교체.
    """
    out_path = out_path or hwpx_path
    with zipfile.ZipFile(hwpx_path, "r") as zf:
        xml = zf.read("Contents/section0.xml").decode("utf-8")

    marker = f"【★ 데이터표:{spec.item}번】"
    if marker not in xml:
        print(f"  [table] 경고: {marker} 미발견 — 삽입 건너뜀")
        return out_path

    idx = xml.find(marker)
    p_start = _rfind_para_open(xml, idx)
    p_close = xml.find("</hp:p>", idx)
    if p_start < 0 or p_close < 0:
        print(f"  [table] 경고: {marker} 단락 경계 탐색 실패 — 삽입 건너뜀")
        return out_path
    p_end = p_close + 7

    max_pid, max_zo = _max_ids(xml)
    tbl_id = max_pid + 2_000_000
    zo     = max_zo + 1
    pid    = max_pid + 1

    tbl_xml, _ = _build_data_table_xml(spec, tbl_id, zo)
    new_para = _DATA_PARA.format(pid=pid, tbl_xml=tbl_xml)

    xml_new = xml[:p_start] + new_para + xml[p_end:]
    _rewrite_hwpx(hwpx_path, xml_new, out_path)
    print(f"  [table] 데이터표:{spec.item}번 삽입 ({len(spec.rows)}행×{len(spec.col_widths)}열)")
    return out_path
