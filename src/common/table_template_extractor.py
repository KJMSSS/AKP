"""
사용자 제공 HWPX에서 표 구조(skeleton)를 추출해 JSON 템플릿으로 저장.

== 사용자 준비 방법 ==
  한글에서 원하는 스타일로 표 3개를 만들고 아래 경로에 저장:
    samples/templates/table_templates.hwpx

  표 식별 규칙 (셀 안에 아래 텍스트 중 하나 포함):
    "조건표"  → 조건/보기 1×1 박스 스타일
    "보기표"  → 보기 전용 1×1 박스 스타일 (없으면 조건표 재사용)
    "데이터표" → N×M 데이터 표 스타일 (헤더행 + 데이터행 구조 추출)

== 실행 ==
  python -m src.common.table_template_extractor
  → samples/templates/table_templates.json 생성
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path


# ── 내부 파서 ─────────────────────────────────────────────────────────────

def _find_tables(xml: str) -> list[str]:
    """section0.xml에서 모든 최상위 <hp:tbl>...</hp:tbl> 블록 추출."""
    tables: list[str] = []
    pos = 0
    while True:
        start = xml.find('<hp:tbl ', pos)
        if start == -1:
            break
        depth = 0
        i = start
        while i < len(xml):
            if xml[i:i+8] == '<hp:tbl ':
                depth += 1
                i += 8
            elif xml[i:i+9] == '</hp:tbl>':
                depth -= 1
                if depth == 0:
                    tables.append(xml[start:i + 9])
                    pos = i + 9
                    break
                i += 9
            else:
                i += 1
        else:
            break
    return tables


def _find_rows(tbl_xml: str) -> list[str]:
    """표 XML에서 <hp:tr>...</hp:tr> 행 목록 추출."""
    rows: list[str] = []
    pos = 0
    while True:
        s = tbl_xml.find('<hp:tr>', pos)
        if s == -1:
            break
        e = tbl_xml.find('</hp:tr>', s)
        if e == -1:
            break
        rows.append(tbl_xml[s:e + 8])
        pos = e + 8
    return rows


def _find_cells(row_xml: str) -> list[str]:
    """행 XML에서 <hp:tc ...>...</hp:tc> 셀 목록 추출."""
    cells: list[str] = []
    pos = 0
    while True:
        s = row_xml.find('<hp:tc ', pos)
        if s == -1:
            break
        depth = 0
        i = s
        while i < len(row_xml):
            if row_xml[i:i+7] == '<hp:tc ':
                depth += 1; i += 7
            elif row_xml[i:i+8] == '</hp:tc>':
                depth -= 1
                if depth == 0:
                    cells.append(row_xml[s:i + 8])
                    pos = i + 8
                    break
                i += 8
            else:
                i += 1
        else:
            break
    return cells


def _plain_text(xml: str) -> str:
    """XML에서 <hp:t> 텍스트만 추출."""
    return ' '.join(re.findall(r'<hp:t[^>]*>([^<]+)</hp:t>', xml))


def _classify(tbl_xml: str) -> str | None:
    txt = _plain_text(tbl_xml)

    # 1순위: 직접 키워드
    if '조건표' in txt:  return 'condition'
    if '보기표' in txt:  return 'boilerplate'
    if '데이터표' in txt: return 'data'

    # 2순위: 내용 기반 자동 감지
    # (가)(나)(다) 패턴 → 조건표
    if re.search(r'[（(][가나다마바사][）)]', txt):
        return 'condition'
    # ㄱ. ㄴ. ㄷ. 또는 "보 기" → 보기표
    if re.search(r'[ㄱㄴㄷ]\s*[.．]', txt) or re.search(r'보\s*기', txt):
        return 'boilerplate'

    return None


# ── skeleton 생성 ─────────────────────────────────────────────────────────

def _make_box_skeleton(tbl_xml: str) -> dict:
    """
    1×1 박스 표에서 skeleton 추출.
    - id / zOrder → {{TBL_ID}} / {{ZO}}
    - height (tbl, sz, cellSz) → {{HEIGHT}}
    - subList 내부 내용 → {{CONTENT}}
    """
    xml = tbl_xml

    # id 동적화 (<hp:tbl ... id="N" ... > 에서 id만 교체)
    xml = re.sub(r'(<hp:tbl\b[^>]*)id="\d+"', r'\1id="{{TBL_ID}}"', xml, count=1)
    # zOrder 동적화
    xml = re.sub(r'zOrder="\d+"', 'zOrder="{{ZO}}"', xml, count=1)
    # height 동적화: hp:tbl 태그 내
    xml = re.sub(r'(<hp:tbl\b[^>]*)height="\d+"', r'\1height="{{HEIGHT}}"', xml, count=1)
    # hp:sz 의 height
    xml = re.sub(r'(<hp:sz\b[^>]*)height="\d+"', r'\1height="{{HEIGHT}}"', xml, count=1)
    # hp:cellSz 의 height
    xml = re.sub(r'(<hp:cellSz\b[^>]*)height="\d+"', r'\1height="{{HEIGHT}}"', xml, count=1)

    # subList 내부 내용 → {{CONTENT}}
    sl_open_end = xml.find('>', xml.find('<hp:subList ')) + 1
    sl_close = xml.find('</hp:subList>', sl_open_end)
    if sl_open_end > 0 and sl_close > sl_open_end:
        xml = xml[:sl_open_end] + '{{CONTENT}}' + xml[sl_close:]

    # 표 너비 추출
    m = re.search(r'<hp:sz width="(\d+)"', xml)
    width = int(m.group(1)) if m else 29190

    return {'skeleton': xml, 'width': width}


def _strip_cell_to_placeholder(cell_xml: str, idx: int) -> str:
    """셀 XML의 subList 내용을 {{TEXT_i}} 플레이스홀더로 교체."""
    s = cell_xml.find('<hp:subList ')
    end_open = cell_xml.find('>', s) + 1
    close = cell_xml.find('</hp:subList>', end_open)
    if s == -1 or close == -1:
        return cell_xml
    marker = f'{{{{TEXT_{idx}}}}}'
    return cell_xml[:end_open] + marker + cell_xml[close:]


def _make_data_skeleton(tbl_xml: str) -> dict:
    """
    N×M 데이터 표에서 skeleton 추출.
    반환: tbl_open / header_row / data_row / tbl_close / col_widths / row_height
    """
    rows = _find_rows(tbl_xml)
    if not rows:
        return {}

    # 헤더행 = 첫 행, 데이터행 = 두 번째 행 (없으면 첫 행 재사용)
    header_row_xml = rows[0]
    data_row_xml   = rows[1] if len(rows) > 1 else rows[0]

    # 셀별 플레이스홀더 삽입
    h_cells = _find_cells(header_row_xml)
    d_cells = _find_cells(data_row_xml)
    col_count = len(h_cells)

    h_stripped = ''.join(_strip_cell_to_placeholder(c, i) for i, c in enumerate(h_cells))
    d_stripped = ''.join(_strip_cell_to_placeholder(c, i) for i, c in enumerate(d_cells))
    header_row_tpl = f'<hp:tr>{h_stripped}</hp:tr>'
    data_row_tpl   = f'<hp:tr>{d_stripped}</hp:tr>'

    # 셀 너비 추출 (헤더행 기준)
    col_widths = [int(m) for m in re.findall(r'<hp:cellSz width="(\d+)"', header_row_xml)]
    # 행 높이 추출
    m = re.search(r'<hp:cellSz [^>]*height="(\d+)"', header_row_xml)
    row_height = int(m.group(1)) if m else 2400

    # tbl_open: 첫 <hp:tr> 직전까지
    first_tr = tbl_xml.find('<hp:tr>')
    tbl_header_raw = tbl_xml[:first_tr]
    # id/zOrder 동적화
    tbl_open = re.sub(r'(<hp:tbl\b[^>]*)id="\d+"', r'\1id="{{TBL_ID}}"', tbl_header_raw, count=1)
    tbl_open = re.sub(r'zOrder="\d+"', 'zOrder="{{ZO}}"', tbl_open, count=1)
    # rowCnt / height 동적화
    tbl_open = re.sub(r'rowCnt="\d+"', 'rowCnt="{{ROW_CNT}}"', tbl_open)
    tbl_open = re.sub(r'(<hp:sz\b[^>]*)height="\d+"', r'\1height="{{TOTAL_H}}"', tbl_open)

    return {
        'tbl_open':    tbl_open,
        'header_row':  header_row_tpl,
        'data_row':    data_row_tpl,
        'tbl_close':   '</hp:tbl>',
        'col_count':   col_count,
        'col_widths':  col_widths,
        'row_height':  row_height,
    }


# ── 공개 API ──────────────────────────────────────────────────────────────

def extract_templates(hwpx_path: Path) -> dict:
    """
    HWPX에서 표 템플릿 추출 후 dict 반환.
    키: 'condition_tbl', 'boilerplate_tbl', 'data_tbl'
    """
    with zipfile.ZipFile(hwpx_path, 'r') as zf:
        xml = zf.read('Contents/section0.xml').decode('utf-8')

    tables = _find_tables(xml)
    result: dict[str, dict | None] = {
        'condition_tbl':   None,
        'boilerplate_tbl': None,
        'data_tbl':        None,
    }

    for tbl_xml in tables:
        kind = _classify(tbl_xml)
        if kind == 'condition' and result['condition_tbl'] is None:
            result['condition_tbl'] = _make_box_skeleton(tbl_xml)
        elif kind == 'boilerplate' and result['boilerplate_tbl'] is None:
            result['boilerplate_tbl'] = _make_box_skeleton(tbl_xml)
        elif kind == 'data' and result['data_tbl'] is None:
            result['data_tbl'] = _make_data_skeleton(tbl_xml)

    # 보기표 없으면 조건표 재사용
    if result['boilerplate_tbl'] is None and result['condition_tbl'] is not None:
        result['boilerplate_tbl'] = result['condition_tbl']

    return result


def save_templates(templates: dict, json_path: Path) -> None:
    json_path.write_text(json.dumps(templates, ensure_ascii=False, indent=2), encoding='utf-8')


def load_templates(json_path: Path) -> dict | None:
    if not json_path.exists():
        return None
    try:
        return json.loads(json_path.read_text(encoding='utf-8'))
    except Exception:
        return None


# ── CLI ───────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    base = Path(__file__).resolve().parent.parent.parent
    hwpx = base / 'samples' / 'templates' / 'table_templates.hwpx'
    out  = base / 'samples' / 'templates' / 'table_templates.json'

    if not hwpx.exists():
        print(f'[오류] 템플릿 파일 없음: {hwpx}')
        print('  → 한글에서 조건표/보기표/데이터표 샘플을 만들고 위 경로에 저장하세요.')
        raise SystemExit(1)

    print(f'추출 중: {hwpx}')
    templates = extract_templates(hwpx)

    found = [k for k, v in templates.items() if v is not None]
    missing = [k for k, v in templates.items() if v is None]
    for k in found:
        print(f'  [OK] {k}')
    for k in missing:
        print(f'  [없음] {k} - 셀 텍스트에 키워드 확인 필요')

    save_templates(templates, out)
    print(f'저장: {out}')
