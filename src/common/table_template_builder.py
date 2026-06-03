"""
추출된 표 템플릿(table_templates.json)으로 HWPX 표 XML 생성.

templates = load_templates(json_path)   # None이면 기존 하드코딩 fallback
build_condition_box(templates, ...)
build_boilerplate_box(templates, ...)
build_data_table(templates, ...)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from src.common.table_template_extractor import load_templates


# ── 데이터 ────────────────────────────────────────────────────────────────

@dataclass
class DataTableSpec:
    item: str                         # 문제 번호 (플레이스홀더 식별용)
    headers: list[str]                # 헤더 행 (빈 리스트 = 헤더 없음)
    rows: list[list[str]]             # 데이터 행들
    col_widths: list[int] = field(default_factory=list)  # 비우면 템플릿 값 사용
    row_height: int = 0               # 0이면 템플릿 값 사용


# ── 내부 유틸 ─────────────────────────────────────────────────────────────

def _max_ids(xml: str) -> tuple[int, int]:
    pids = [int(m) for m in re.findall(r'<hp:p id="(\d+)"', xml) if int(m) < 2_000_000_000]
    zos  = [int(m) for m in re.findall(r'zOrder="(\d+)"', xml)]
    return (max(pids) if pids else 200), (max(zos) if zos else 200)


def _extract_lineseg_heights(para_xml: str) -> list[int]:
    return [int(m) for m in re.findall(r'vertsize="(\d+)"', para_xml)]


def _compute_box_height(paras: list[str]) -> int:
    heights = []
    for p in paras:
        vs = _extract_lineseg_heights(p)
        heights.append(max(vs) if vs else 1200)
    n = len(heights)
    content_h = sum(heights) + max(0, n - 1) * 720
    return content_h + 2266


def _fill_box_skeleton(skeleton: str, content_paras: list[str], tbl_id: int, zo: int) -> tuple[str, int]:
    """
    1×1 박스 skeleton에 실제 내용 + 동적 ID 삽입.
    반환: (완성된 tbl_xml, height)
    """
    height = _compute_box_height(content_paras)
    inner  = ''.join(content_paras)
    xml = skeleton
    xml = xml.replace('{{TBL_ID}}', str(tbl_id))
    xml = xml.replace('{{ZO}}',     str(zo))
    xml = xml.replace('{{HEIGHT}}', str(height))
    xml = xml.replace('{{CONTENT}}', inner)
    return xml, height


def _fill_data_skeleton(tmpl: dict, spec: DataTableSpec, tbl_id: int, zo: int) -> tuple[str, int]:
    """
    데이터표 skeleton에 실제 내용 삽입.
    반환: (완성된 tbl_xml, total_height)
    """
    col_widths  = spec.col_widths  or tmpl.get('col_widths', [24000, 24000])
    row_height  = spec.row_height  or tmpl.get('row_height', 2400)
    col_count   = len(col_widths)

    all_rows: list[tuple[str, list[str]]] = []  # (row_type, cells)
    if spec.headers:
        all_rows.append(('header', spec.headers))
    for r in spec.rows:
        all_rows.append(('data', r))

    n_rows   = len(all_rows)
    total_h  = n_rows * row_height
    total_w  = sum(col_widths)

    rows_xml = ''
    for row_type, cells in all_rows:
        row_tpl = tmpl['header_row'] if row_type == 'header' else tmpl['data_row']
        row_xml = row_tpl
        for i, text in enumerate(cells[:col_count]):
            row_xml = row_xml.replace(f'{{{{TEXT_{i}}}}}', _escape(text))
        # 남은 플레이스홀더 제거
        row_xml = re.sub(r'\{\{TEXT_\d+\}\}', '', row_xml)
        rows_xml += row_xml

    tbl_open = tmpl['tbl_open']
    tbl_open = tbl_open.replace('{{TBL_ID}}', str(tbl_id))
    tbl_open = tbl_open.replace('{{ZO}}',     str(zo))
    tbl_open = tbl_open.replace('{{ROW_CNT}}', str(n_rows))
    tbl_open = tbl_open.replace('{{TOTAL_H}}', str(total_h))

    tbl_xml = tbl_open + rows_xml + tmpl['tbl_close']
    return tbl_xml, total_h


def _escape(text: str) -> str:
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


# ── 공개 API ──────────────────────────────────────────────────────────────

def build_condition_box(
    templates: dict | None,
    content_paras: list[str],
    tbl_id: int,
    zo: int,
) -> tuple[str, int]:
    """
    조건표 XML 생성.
    templates 없으면 None 반환 → 호출자가 기존 fallback 사용.
    """
    if templates and templates.get('condition_tbl'):
        return _fill_box_skeleton(templates['condition_tbl']['skeleton'], content_paras, tbl_id, zo)
    return '', 0  # fallback 신호


def build_boilerplate_box(
    templates: dict | None,
    content_paras: list[str],
    tbl_id: int,
    zo: int,
) -> tuple[str, int]:
    """보기표 XML 생성. templates 없으면 ('' , 0) fallback 신호."""
    if templates and templates.get('boilerplate_tbl'):
        return _fill_box_skeleton(templates['boilerplate_tbl']['skeleton'], content_paras, tbl_id, zo)
    return '', 0


def build_data_table(
    templates: dict | None,
    spec: DataTableSpec,
    tbl_id: int,
    zo: int,
) -> tuple[str, int]:
    """데이터표 XML 생성. templates 없으면 ('' , 0) fallback 신호."""
    if templates and templates.get('data_tbl'):
        return _fill_data_skeleton(templates['data_tbl'], spec, tbl_id, zo)
    return '', 0


# ── 전역 템플릿 캐시 ──────────────────────────────────────────────────────

_TEMPLATES: dict | None = None
_TEMPLATES_LOADED = False


def get_default_templates(project_root: Path | None = None) -> dict | None:
    """
    samples/templates/table_templates.json 자동 로드 (1회).
    없으면 None 반환 → 기존 하드코딩 사용.
    """
    global _TEMPLATES, _TEMPLATES_LOADED
    if _TEMPLATES_LOADED:
        return _TEMPLATES
    _TEMPLATES_LOADED = True

    root = project_root or Path(__file__).resolve().parent.parent.parent
    json_path = root / 'samples' / 'templates' / 'table_templates.json'
    _TEMPLATES = load_templates(json_path)
    if _TEMPLATES:
        print(f'[표 템플릿] 로드: {json_path.name}')
    return _TEMPLATES
