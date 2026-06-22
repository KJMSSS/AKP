"""
추출된 표 템플릿(table_templates.json)으로 HWPX 표 XML 생성.

templates = load_templates(json_path)   # None이면 기존 하드코딩 fallback
build_condition_box(templates, ...)
build_boilerplate_box(templates, ...)
build_data_table(templates, ...)
"""
from __future__ import annotations

import json
import os
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

_BOX_WARNED: set[str] = set()


def box_skeleton_usable(skeleton: str) -> bool:
    """1×1 박스 스켈레톤이 사용 가능한지 검사 (부작용 없음).

    추출이 잘못되면 다중 셀 표(예: 6×4 답안표)가 스켈레톤으로 저장되고,
    원본 텍스트((가)(나)(다)·①~⑤ 등)가 잔존한 채 {{CONTENT}}만 한 셀에
    박혀 내용이 좁은 셀에서 세로로 흐른다. rowCnt/colCnt=1 + 잔존 텍스트
    없음 + {{CONTENT}} 존재를 모두 만족해야 사용한다.
    """
    m = re.search(r'rowCnt="(\d+)" colCnt="(\d+)"', skeleton)
    if not (m and m.group(1) == "1" and m.group(2) == "1"
            and '{{CONTENT}}' in skeleton):
        return False
    leftover = [t for t in re.findall(r'<hp:t[^>]*>([^<]*)</hp:t>', skeleton)
                if t.strip()]
    return not leftover


def _box_skeleton_ok(skeleton: str, kind: str) -> bool:
    """build 시점 가드 — box_skeleton_usable + 1회 경고 출력."""
    ok = box_skeleton_usable(skeleton)
    if not ok and kind not in _BOX_WARNED:
        _BOX_WARNED.add(kind)
        print(f"  [표 템플릿] {kind} 스켈레톤 오염(다중 셀/잔존 텍스트) — 내장 1×1 박스로 폴백")
    return ok


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
        skeleton = templates['condition_tbl']['skeleton']
        if _box_skeleton_ok(skeleton, '조건'):
            return _fill_box_skeleton(skeleton, content_paras, tbl_id, zo)
    return '', 0  # fallback 신호


def build_boilerplate_box(
    templates: dict | None,
    content_paras: list[str],
    tbl_id: int,
    zo: int,
) -> tuple[str, int]:
    """보기표 XML 생성. templates 없으면 ('' , 0) fallback 신호."""
    if templates and templates.get('boilerplate_tbl'):
        skeleton = templates['boilerplate_tbl']['skeleton']
        if _box_skeleton_ok(skeleton, '보기'):
            return _fill_box_skeleton(skeleton, content_paras, tbl_id, zo)
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


# ── 전역 템플릿 캐시 (파일 mtime 기준 무효화) ──────────────────────────────

_TEMPLATES_CACHE: dict | None = None
_TEMPLATES_MTIME: float | None = None
_TEMPLATES_KEY: Path | None = None


def template_json_candidates(project_root: Path | None = None) -> list[Path]:
    """표 템플릿 JSON 후보 경로 (우선순위 순).

    1) DATA_DIR/table_templates.json — 학원장이 한글 양식을 업로드해 추출한 영속본
       (Railway Volume. 재배포에도 유지). 관리자 표 양식 업로드의 저장 위치.
    2) samples/templates/table_templates.json — 레포 번들(있으면).
    """
    paths: list[Path] = []
    data_dir = os.environ.get("DATA_DIR", "")
    if data_dir:
        paths.append(Path(data_dir) / "table_templates.json")
    root = project_root or Path(__file__).resolve().parent.parent.parent
    paths.append(root / "samples" / "templates" / "table_templates.json")
    return paths


# ── 제목 박스 (보기/조건) — 학원장 골드 스타일 이식 ────────────────────────
# titled_box_template.json: 골드 table[16](▍보 기▍ 헤더 바 박스)에서 추출한
# 재사용 스켈레톤. 자리표시자 {{TBL_ID}} {{ZO}} {{TITLE}} {{CONTENT}} {{BF_51..58}}.
# borderFill 은 출력 헤더의 빈 번호로 동적 주입(템플릿마다 안전)하므로 51~58 →
# bf_base..bf_base+7 로 매핑한다.

_TITLED_TPL: dict | None = None
_TITLED_BF_ORDER = [str(i) for i in range(51, 59)]  # 골드 원본 borderFill 번호


def _load_titled_template() -> dict:
    global _TITLED_TPL
    if _TITLED_TPL is None:
        p = Path(__file__).resolve().parent / "titled_box_template.json"
        try:
            _TITLED_TPL = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            _TITLED_TPL = {}
    return _TITLED_TPL


def has_titled_template() -> bool:
    return bool(_load_titled_template().get("skeleton"))


def build_titled_box(title: str, content_inner: str, tbl_id: int,
                     zo: int, bf_base: int) -> str | None:
    """제목 박스 XML 생성 (None=템플릿 없음).

    bf_base: borderFill 시작 번호 — 골드 51→bf_base, …, 58→bf_base+7.
    content_inner: 박스 내용(이미 만들어진 <hp:p> 들의 연결 문자열).
    """
    tpl = _load_titled_template()
    skel = tpl.get("skeleton")
    if not skel:
        return None
    for k, orig in enumerate(_TITLED_BF_ORDER):
        skel = skel.replace(f"{{{{BF_{orig}}}}}", str(bf_base + k))
    return (skel.replace("{{TBL_ID}}", str(tbl_id))
                .replace("{{ZO}}", str(zo))
                .replace("{{TITLE}}", title)
                .replace("{{CONTENT}}", content_inner))


def titled_borderfill_defs(bf_base: int) -> str:
    """주입할 borderFill 정의 8개 XML (id = bf_base..bf_base+7)."""
    tpl = _load_titled_template()
    bfs = tpl.get("borderfills", {})
    out = []
    for k, orig in enumerate(_TITLED_BF_ORDER):
        d = bfs.get(orig, "").replace("{{ID}}", str(bf_base + k))
        if d:
            out.append(d)
    return "".join(out)


def get_default_templates(project_root: Path | None = None) -> dict | None:
    """표 템플릿 JSON 로드 (DATA_DIR 우선 → 레포 번들).

    파일 mtime 기준 캐싱 — 런타임 중 템플릿이 업로드/수정되면 자동 재로드한다
    (웹 서버 재시작 불필요). 후보가 모두 없으면 None → 호출자가 하드코딩 fallback.

    이전 구현은 1회 로드 플래그라, 서버 기동 시점에 파일이 없으면 이후 템플릿을
    넣어도 같은 프로세스에서 영영 반영되지 않았다(숨은 차단요인).
    """
    global _TEMPLATES_CACHE, _TEMPLATES_MTIME, _TEMPLATES_KEY

    active: Path | None = None
    mtime: float | None = None
    for p in template_json_candidates(project_root):
        try:
            mtime = p.stat().st_mtime
            active = p
            break
        except OSError:
            continue

    # 활성 경로·mtime 모두 동일하면 캐시 그대로 반환
    if active == _TEMPLATES_KEY and mtime == _TEMPLATES_MTIME:
        return _TEMPLATES_CACHE

    _TEMPLATES_KEY = active
    _TEMPLATES_MTIME = mtime
    _TEMPLATES_CACHE = load_templates(active) if active is not None else None
    if _TEMPLATES_CACHE:
        print(f'[표 템플릿] 로드: {active}')
    return _TEMPLATES_CACHE
