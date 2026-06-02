"""
PDF Mathpix 마크다운 → 빈 .hwpx 빌더 (템플릿 불필요)

전략:
  - 기존 워드초벌에서 header.xml만 빌려 폰트/스타일 정의를 재사용
  - masterpage0.xml, section0.xml은 새로 생성 (단일 컬럼, 헤더/푸터 없음)
  - 마크다운 줄 → hp:p, 인라인 $...$ → hp:equation, 블록 $$...$$ → 별도 문단
"""
import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape as _xe

from src.common.latex_to_hwp import convert as latex_to_hwp

# ── XML 네임스페이스 선언 ─────────────────────────────────────────────
_NS = (
    'xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app" '
    'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
    'xmlns:hp10="http://www.hancom.co.kr/hwpml/2016/paragraph" '
    'xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
    'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" '
    'xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" '
    'xmlns:hhs="http://www.hancom.co.kr/hwpml/2011/history" '
    'xmlns:hm="http://www.hancom.co.kr/hwpml/2011/master-page" '
    'xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:opf="http://www.idpf.org/2007/opf/" '
    'xmlns:ooxmlchart="http://www.hancom.co.kr/hwpml/2016/ooxmlchart" '
    'xmlns:hwpunitchar="http://www.hancom.co.kr/hwpml/2016/HwpUnitChar" '
    'xmlns:epub="http://www.idpf.org/2007/ops" '
    'xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0"'
)

# ── 페이지 설정 (HWP 단위 = 1/7200 inch ≒ 0.003528 mm) ──────────────
# A4 세로(210×297mm), 20mm 여백
_PW = 59527    # 210mm
_PH = 84189    # 297mm
_ML = 5669     # 좌 여백 ≈ 20mm
_MR = 5669     # 우 여백 ≈ 20mm
_MT = 5669     # 상 여백 ≈ 20mm
_MB = 5669     # 하 여백 ≈ 20mm
_TW = _PW - _ML - _MR   # 본문 폭 = 48189 (≈170mm)

# ── 선택지 원문자 변환 ───────────────────────────────────────────────
# OCR(Mathpix)은 ①~⑩을 (1)~(10) 또는 （1）~（5）로 출력한다.
# 공백·줄시작 앞 / 공백·줄끝 뒤 조건이 맞는 text 세그먼트에서만 치환.
_CIRCLE_MAP: dict[str, str] = {
    '1': '①', '2': '②', '3': '③', '4': '④', '5': '⑤',
    '6': '⑥', '7': '⑦', '8': '⑧', '9': '⑨', '10': '⑩',
}
# ASCII (1)~(10): 앞은 줄시작 또는 공백, 뒤는 공백 또는 줄끝
_CHOICE_ASCII_RE = re.compile(r'(?:^|(?<=\s))\((10|[1-9])\)(?=\s|$)')
# 전각 （1）~（5）: 동일 조건
_CHOICE_WIDE_RE  = re.compile(r'(?:^|(?<=\s))（(10|[1-5])）(?=\s|$)')


def _to_circled(text: str) -> str:
    """선택지 (N)/（N） → 원문자. text 세그먼트 전용 (수식 블록 보호)."""
    text = _CHOICE_ASCII_RE.sub(lambda m: _CIRCLE_MAP[m.group(1)], text)
    text = _CHOICE_WIDE_RE.sub(lambda m: _CIRCLE_MAP[m.group(1)], text)
    return text


# ── 줄 구조 분석 패턴 ────────────────────────────────────────────────
_PROB_LINE_RE    = re.compile(r'^\d{1,2}[.．]\s')          # "1. " "2. " …
_SCORE_LINE_RE   = re.compile(r'^\[\d+(?:\.\d+)?점\]')     # "[4.5점]" "[5점]"

# ── 보기/점수 정규화 (v7) ─────────────────────────────────────────────
_KOREAN_RE       = re.compile('[가-힣]')
# 줄 시작 원문자/ASCII/전각 선택지 불릿 (공백 포함)
_CHOICE_BULLET_RE = re.compile(r'^(?:[①②③④⑤]|\((?:10|[1-9])\)|（[1-5]）)\s*')
# [N점] 패턴 — plain text 강제
_SCORE_PAT_RE    = re.compile(r'\[\d+(?:\.\d+)?점\]')


def _strip_eq_to_korean_space(segs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """수식 세그먼트 직후 텍스트가 ' [가-힣]' 로 시작하면 공백 1개 제거 (수식→한글 단방향)."""
    out: list[tuple[str, str]] = []
    for k, v in segs:
        if (k == 'text' and out and out[-1][0] in ('inline', 'display')
                and len(v) >= 2 and v[0] == ' ' and '가' <= v[1] <= '힣'):
            v = v[1:]
        out.append((k, v))
    return out


# 공백 뒤에 (N) 또는 （N） 이 나타나는 위치 — 두 번째 선택지부터 분리
_CHOICE_BOUND_RE = re.compile(
    r'(?<=\s)(?=(?:\((?:10|[1-9])\)|（[1-5]）)(?:\s|$))'
)


def _postprocess_lines(lines: list[str]) -> list[str]:
    """
    문항 번호·점수 앞 빈 줄 삽입, display math 앞뒤 빈 줄 보장,
    복수 선택지 한 줄 분리.
    """
    out: list[str] = []
    first_q = False

    for raw in lines:
        s = raw.strip()

        # 마크다운 테이블 줄은 변경 없이 통과
        if s.startswith('|'):
            out.append(raw)
            continue

        # 문항 번호 줄: 첫 번째 제외하고 앞에 빈 줄
        if _PROB_LINE_RE.match(s):
            if first_q:
                out.append('')
            first_q = True
            out.append(raw)
            continue

        # [N점] 줄: 앞에 빈 줄 (본문에 바로 붙지 않도록)
        if _SCORE_LINE_RE.match(s):
            out.append('')
            out.append(raw)
            continue

        # display math 줄: 앞뒤 빈 줄 보장
        if s.startswith('$$'):
            out.append('')
            out.append(raw)
            out.append('')
            continue

        # 복수 선택지 한 줄 분리: "(1) … (2) …" → 별도 줄
        parts = _CHOICE_BOUND_RE.split(s)
        if len(parts) > 1:
            for p in parts:
                p = p.strip()
                if p:
                    out.append(p)
            continue

        out.append(raw)

    # 연속 빈 줄 → 최대 1개로 압축
    deduped: list[str] = []
    for line in out:
        if not line.strip() and deduped and not deduped[-1].strip():
            continue
        deduped.append(line)

    return deduped


# ── 수식 패턴 ($$...$$ 우선, $...$ 후순위) ──────────────────────────
# display: [\s\S]+ (개행 포함, 최소 1자), inline: [^$\n]+ (달러/개행 제외)
_MATH_RE = re.compile(
    r'\$\$([\s\S]+?)\$\$'
    r'|(?<!\$)\$([^$\n]+?)\$(?!\$)',
)

# 문항 번호 패턴 (1. 2. … 줄 굵게)
_PROB_NUM_RE = re.compile(r'^\d{1,2}[.．]')


def _parse_segments(line: str) -> list[tuple[str, str]]:
    """한 줄을 ('text'|'inline'|'display', content) 세그먼트로 분해."""
    segs: list[tuple[str, str]] = []
    pos = 0
    for m in _MATH_RE.finditer(line):
        before = line[pos:m.start()]
        if before:
            segs.append(('text', before))
        if m.group(1) is not None:
            latex = m.group(1).strip()
            if latex:
                segs.append(('display', latex))
        else:
            latex = m.group(2).strip()
            if latex:
                segs.append(('inline', latex))
        pos = m.end()
    tail = line[pos:]
    if tail:
        segs.append(('text', tail))
    return segs


def _preprocess_md(md: str) -> list[str]:
    """
    멀티라인 $$...$$ 블록을 한 줄로 합치고 줄 단위로 반환.
    이미지 링크(![](...))는 제거.
    """
    # 이미지 제거
    md = re.sub(r'!\[.*?\]\(.*?\)', '', md, flags=re.DOTALL)
    # Mathpix 형식 → 표준 형식 변환 (\[...\] → $$...$$, \(...\) → $...$)
    md = re.sub(r'\\\[\s*([\s\S]+?)\s*\\\]', lambda m: '$$' + ' '.join(m.group(1).split()) + '$$', md)
    md = re.sub(r'\\\(\s*(.+?)\s*\\\)', lambda m: '$' + m.group(1).strip() + '$', md)
    # 멀티라인 $$...$$ → 한 줄
    md = re.sub(
        r'\$\$(.*?)\$\$',
        lambda m: '$$' + ' '.join(m.group(1).split()) + '$$',
        md, flags=re.DOTALL,
    )
    return md.split('\n')


# ── 마크다운 테이블 파싱 ─────────────────────────────────────────────
_TABLE_SEP_RE = re.compile(r'^\s*\|[-| :]+\|\s*$')


def _parse_md_table(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    """마크다운 테이블 줄 → (헤더 행, 데이터 행 목록)."""
    rows: list[list[str]] = []
    for line in lines:
        s = line.strip()
        if not s.startswith('|'):
            continue
        if _TABLE_SEP_RE.match(s):
            continue
        cells = [c.strip() for c in s.strip('|').split('|')]
        if cells:
            rows.append(cells)
    if not rows:
        return [], []
    return rows[0], rows[1:]


# ── XML 생성 ─────────────────────────────────────────────────────────

class _HwpxWriter:
    """섹션 XML을 순차적으로 구성하는 내부 빌더."""

    def __init__(self):
        self._eq_id   = 3000
        self._eq_z    = 1
        self._para_id = 10
        self._choice_eq    = 0   # 보기 수식화 건수
        self._choice_plain = 0   # 보기 한글 plain 유지 건수

    # ── ID 발급 ──────────────────────────────────────────────────────

    def _eid(self) -> int:
        v = self._eq_id; self._eq_id += 1; return v

    def _ez(self) -> int:
        v = self._eq_z; self._eq_z += 1; return v

    def _pid(self) -> int:
        v = self._para_id; self._para_id += 1; return v

    # ── hp:equation ──────────────────────────────────────────────────

    # 그리스 문자 키워드 (HWP script 변환 후 이 이름으로 등장)
    _GREEK_KW = {
        'alpha','beta','gamma','delta','epsilon','zeta','eta','theta',
        'iota','kappa','lambda','mu','nu','xi','pi','rho','sigma',
        'tau','upsilon','phi','chi','psi','omega',
        'GAMMA','DELTA','THETA','LAMBDA','XI','PI','SIGMA','UPSILON','PHI','PSI','OMEGA',
    }

    @staticmethod
    def _eq_size(hwp: str) -> tuple[int, int]:
        """수식 크기 추정 (gold HWPX 실측 기반).

        그리스 문자 키워드는 ~500단위, 나머지 의미 문자는 ~650단위/char.
        (gold 실측: x^3-1=0 → 4511, alpha,beta → ~1400)
        """
        if 'cases' in hwp:
            h = 2415
        elif ' atop ' in hwp:
            h = 2000
        elif ' over ' in hwp or 'sqrt' in hwp or 'nroot' in hwp:
            h = 1700
        else:
            h = 1313
        # 그리스 문자 먼저 제거 후 나머지 의미 문자 계산
        remaining = hwp
        greek_count = 0
        for gk in sorted(_HwpxWriter._GREEK_KW, key=len, reverse=True):
            cnt = remaining.count(gk)
            if cnt:
                greek_count += cnt
                remaining = remaining.replace(gk, ' ')
        meaningful = len(re.sub(r'[\s{}()\[\]]', '', remaining))
        w = greek_count * 500 + meaningful * 650
        w = max(1050, min(w, 18000))
        return w, h

    def _equation(self, latex: str) -> str:
        hwp = latex_to_hwp(latex)
        w, h = self._eq_size(hwp)
        return (
            f'<hp:equation id="{self._eid()}" zOrder="{self._ez()}" '
            f'numberingType="EQUATION" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" '
            f'lock="0" dropcapstyle="None" version="Equation Version 60" '
            f'baseLine="85" textColor="#000000" baseUnit="1100" lineMode="CHAR" font="HYhwpEQ">'
            f'<hp:sz width="{w}" widthRelTo="ABSOLUTE" height="{h}" heightRelTo="ABSOLUTE" protect="0"/>'
            f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
            f'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" '
            f'horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
            f'<hp:outMargin left="0" right="0" top="0" bottom="0"/>'
            f'<hp:script>{_xe(hwp)}</hp:script>'
            f'</hp:equation>'
        )

    # ── linesegarray ─────────────────────────────────────────────────

    def _lineseg(self, max_eq_h: int = 0, empty: bool = False) -> str:
        if empty:
            return '<hp:linesegarray/>'
        vs = max(1200, max_eq_h)
        bl = round(vs * 0.85)
        return (
            '<hp:linesegarray>'
            f'<hp:lineseg textpos="0" vertpos="0" vertsize="{vs}" textheight="{vs}" '
            f'baseline="{bl}" spacing="720" horzpos="0" horzsize="{_TW}" flags="393216"/>'
            '</hp:linesegarray>'
        )

    # ── hp:p (일반 문단) ─────────────────────────────────────────────

    def _para(self, segs: list[tuple[str, str]], cpr: int = 0) -> str:
        segs = _strip_eq_to_korean_space(segs)  # B: 수식→한글 공백 제거 (모든 경로 일괄)
        parts: list[str] = []
        for kind, content in segs:
            if kind == 'text':
                if content:
                    parts.append(f'<hp:t>{_xe(_to_circled(content))}</hp:t>')
            else:
                parts.append(self._equation(content))

        max_eq_h = 0
        for part in parts:
            m = re.search(r'height="(\d+)" heightRelTo="ABSOLUTE"', part)
            if m:
                max_eq_h = max(max_eq_h, int(m.group(1)))

        run = (
            f'<hp:run charPrIDRef="{cpr}">{"".join(parts)}</hp:run>'
            if parts else
            f'<hp:run charPrIDRef="{cpr}"/>'
        )
        return (
            f'<hp:p id="{self._pid()}" paraPrIDRef="8" styleIDRef="0" '
            f'pageBreak="0" columnBreak="0" merged="0">'
            f'{run}'
            f'{self._lineseg(max_eq_h, empty=not parts)}'
            f'</hp:p>'
        )

    # ── 섹션 설정 문단 (첫 hp:p) ─────────────────────────────────────

    def _secpr_para(self) -> str:
        return (
            '<hp:p id="1" paraPrIDRef="8" styleIDRef="0" '
            'pageBreak="0" columnBreak="0" merged="0">'
            '<hp:run charPrIDRef="0">'
            '<hp:secPr id="" textDirection="HORIZONTAL" spaceColumns="1200" '
            f'tabStop="7000" tabStopVal="3500" tabStopUnit="HWPUNIT" '
            'outlineShapeIDRef="0" memoShapeIDRef="0" textVerticalWidthHead="0" masterPageCnt="1">'
            '<hp:grid lineGrid="0" charGrid="0" wonggojiFormat="0"/>'
            '<hp:startNum pageStartsOn="BOTH" page="0" pic="0" tbl="0" equation="0"/>'
            '<hp:visibility hideFirstHeader="0" hideFirstFooter="0" hideFirstMasterPage="0" '
            'border="SHOW_ALL" fill="SHOW_ALL" hideFirstPageNum="0" '
            'hideFirstEmptyLine="0" showLineNumber="0"/>'
            '<hp:lineNumberShape restartType="0" countBy="0" distance="0" startNumber="0"/>'
            f'<hp:pagePr landscape="NONE" width="{_PW}" height="{_PH}" gutterType="LEFT_ONLY">'
            f'<hp:margin header="{_MT}" footer="{_MB}" gutter="0" '
            f'left="{_ML}" right="{_MR}" top="{_MT}" bottom="{_MB}"/>'
            '</hp:pagePr>'
            '<hp:colPr id="" type="NEWSPAPER" layout="LEFT" colCount="1" sameSz="1" sameGap="0"/>'
            '<hp:footNotePr>'
            '<hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" suffixChar=")" supscript="0"/>'
            '<hp:noteLine length="-1" type="SOLID" width="0.12 mm" color="#000000"/>'
            '<hp:noteSpacing betweenNotes="283" belowLine="567" aboveLine="850"/>'
            '<hp:numbering type="CONTINUOUS" newNum="1"/>'
            '<hp:placement place="EACH_COLUMN" beneathText="0"/>'
            '</hp:footNotePr>'
            '<hp:endNotePr>'
            '<hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" suffixChar=")" supscript="0"/>'
            '<hp:noteLine length="32456" type="SOLID" width="0.12 mm" color="#000000"/>'
            '<hp:noteSpacing betweenNotes="2834" belowLine="567" aboveLine="850"/>'
            '<hp:numbering type="CONTINUOUS" newNum="1"/>'
            '<hp:placement place="END_OF_DOCUMENT" beneathText="0"/>'
            '</hp:endNotePr>'
            '<hp:masterPage idRef="masterpage0"/>'
            '</hp:secPr>'
            '</hp:run>'
            '<hp:linesegarray>'
            f'<hp:lineseg textpos="0" vertpos="0" vertsize="1000" textheight="1000" '
            f'baseline="850" spacing="600" horzpos="0" horzsize="{_TW}" flags="393216"/>'
            '</hp:linesegarray>'
            '</hp:p>'
        )

    # ── 마크다운 테이블 → HWPX 데이터 표 ────────────────────────────

    def _md_table_to_hwpx(self, tbl_lines: list[str]) -> str:
        """마크다운 테이블 줄 → HWPX 단락 XML (데이터 표)."""
        headers, data_rows = _parse_md_table(tbl_lines)
        if not headers:
            return ''

        all_rows = [headers] + data_rows
        n_cols   = max(len(r) for r in all_rows)
        n_rows   = len(all_rows)
        row_h    = 2400
        cell_w   = _TW // n_cols
        inner_w  = max(1, cell_w - 1020)
        total_w  = cell_w * n_cols
        total_h  = row_h * n_rows
        zo       = self._ez()
        tbl_pid  = self._pid()

        rows_xml = ''
        for ri, row in enumerate(all_rows):
            cells_xml = ''
            bfid = '3' if ri == 0 else '4'
            for ci in range(n_cols):
                cell_text = (row[ci] if ci < len(row) else '').strip()
                segs = _parse_segments(cell_text)
                cell_para = self._para(segs, cpr=0)
                cells_xml += (
                    f'<hp:tc name="" header="0" hasMargin="0" protect="0" editable="0" dirty="0" '
                    f'borderFillIDRef="{bfid}">'
                    f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" '
                    f'linkListIDRef="0" linkListNextIDRef="0" textWidth="{inner_w}" textHeight="0" '
                    f'hasTextRef="0" hasNumRef="0">'
                    f'{cell_para}'
                    f'</hp:subList>'
                    f'<hp:cellAddr colAddr="{ci}" rowAddr="{ri}"/>'
                    f'<hp:cellSpan colSpan="1" rowSpan="1"/>'
                    f'<hp:cellSz width="{cell_w}" height="{row_h}"/>'
                    f'<hp:cellMargin left="510" right="510" top="141" bottom="141"/>'
                    f'</hp:tc>'
                )
            rows_xml += f'<hp:tr>{cells_xml}</hp:tr>'

        tbl_xml = (
            f'<hp:tbl id="{tbl_pid}" zOrder="{zo}" numberingType="TABLE" '
            f'textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" '
            f'pageBreak="CELL" repeatHeader="1" rowCnt="{n_rows}" colCnt="{n_cols}" '
            f'cellSpacing="0" borderFillIDRef="2" noAdjust="0">'
            f'<hp:sz width="{total_w}" widthRelTo="ABSOLUTE" height="{total_h}" heightRelTo="ABSOLUTE" protect="0"/>'
            f'<hp:pos treatAsChar="0" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
            f'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" '
            f'horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
            f'<hp:outMargin left="283" right="283" top="283" bottom="283"/>'
            f'<hp:inMargin left="510" right="510" top="141" bottom="141"/>'
            f'{rows_xml}'
            f'</hp:tbl>'
        )
        return (
            f'<hp:p id="{self._pid()}" paraPrIDRef="8" styleIDRef="0" '
            f'pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="0">{tbl_xml}<hp:t/></hp:run>'
            f'<hp:linesegarray/>'
            f'</hp:p>'
        )

    # ── 전체 section XML ─────────────────────────────────────────────

    def build_section(self, lines: list[str]) -> str:
        paras: list[str] = [self._secpr_para()]
        lines = _postprocess_lines(lines)

        idx = 0
        while idx < len(lines):
            raw = lines[idx]

            # 마크다운 테이블 블록 감지 → HWPX 표 생성
            if raw.strip().startswith('|'):
                tbl_lines: list[str] = []
                while idx < len(lines) and lines[idx].strip().startswith('|'):
                    tbl_lines.append(lines[idx])
                    idx += 1
                tbl_xml = self._md_table_to_hwpx(tbl_lines)
                if tbl_xml:
                    paras.append(tbl_xml)
                continue

            idx += 1

            # 마크다운 헤더 기호 제거
            line = re.sub(r'^#+\s*', '', raw)

            if not line.strip():
                paras.append(self._para([]))
                continue

            # ── 선택지 줄 처리 (v7) ──────────────────────────────────
            cm = _CHOICE_BULLET_RE.match(line)
            if cm:
                bullet  = cm.group(0)
                content = line[cm.end():]
                if content.strip() and not _SCORE_PAT_RE.search(content):
                    content_segs = _parse_segments(content)
                    if all(k == 'text' for k, _ in content_segs):
                        segs = [('text', bullet), ('inline', content.strip())]
                        self._choice_eq += 1
                    else:
                        segs = [('text', bullet)] + content_segs
                else:
                    segs = _parse_segments(line)
                paras.append(self._para(segs, cpr=0))
                continue
            # ────────────────────────────────────────────────────────

            segs = _parse_segments(line)
            paras.append(self._para(segs, cpr=0))

        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
            f'<hs:sec {_NS}>'
            + ''.join(paras)
            + '</hs:sec>'
        )


# ── 보조 XML 생성 ─────────────────────────────────────────────────────

def _masterpage_xml() -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
        f'<masterPage {_NS} id="masterpage0" type="BOTH" '
        'pageNumber="0" pageDuplicate="0" pageFront="0">'
        f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="TOP" '
        f'linkListIDRef="0" linkListNextIDRef="0" textWidth="{_TW}" textHeight="0" '
        'hasTextRef="0" hasNumRef="0">'
        '<hp:p id="0" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
        '<hp:run charPrIDRef="0"/>'
        '<hp:linesegarray/>'
        '</hp:p>'
        '</hp:subList>'
        '</masterPage>'
    ).encode('utf-8')


def _content_hpf_xml() -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
        '<opf:package xmlns:opf="http://www.idpf.org/2007/opf/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" version="" unique-identifier="" id="">'
        '<opf:metadata>'
        '<opf:title>PDF 변환 문서</opf:title>'
        '<opf:language>ko</opf:language>'
        '</opf:metadata>'
        '<opf:manifest>'
        '<opf:item id="header" href="Contents/header.xml" media-type="application/xml"/>'
        '<opf:item id="masterpage0" href="Contents/masterpage0.xml" media-type="application/xml"/>'
        '<opf:item id="section0" href="Contents/section0.xml" media-type="application/xml"/>'
        '<opf:item id="settings" href="settings.xml" media-type="application/xml"/>'
        '</opf:manifest>'
        '<opf:spine>'
        '<opf:itemref idref="section0"/>'
        '</opf:spine>'
        '</opf:package>'
    ).encode('utf-8')


def _container_rdf_xml() -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="">'
        '<ns0:hasPart xmlns:ns0="http://www.hancom.co.kr/hwpml/2016/meta/pkg#" '
        'rdf:resource="Contents/header.xml"/>'
        '</rdf:Description>'
        '<rdf:Description rdf:about="Contents/header.xml">'
        '<rdf:type rdf:resource="http://www.hancom.co.kr/hwpml/2016/meta/pkg#HeaderFile"/>'
        '</rdf:Description>'
        '<rdf:Description rdf:about="">'
        '<ns0:hasPart xmlns:ns0="http://www.hancom.co.kr/hwpml/2016/meta/pkg#" '
        'rdf:resource="Contents/masterpage0.xml"/>'
        '</rdf:Description>'
        '<rdf:Description rdf:about="Contents/masterpage0.xml">'
        '<rdf:type rdf:resource="http://www.hancom.co.kr/hwpml/2016/meta/pkg#MasterPageFile"/>'
        '</rdf:Description>'
        '<rdf:Description rdf:about="">'
        '<ns0:hasPart xmlns:ns0="http://www.hancom.co.kr/hwpml/2016/meta/pkg#" '
        'rdf:resource="Contents/section0.xml"/>'
        '</rdf:Description>'
        '<rdf:Description rdf:about="Contents/section0.xml">'
        '<rdf:type rdf:resource="http://www.hancom.co.kr/hwpml/2016/meta/pkg#BodyTextFile"/>'
        '</rdf:Description>'
        '</rdf:RDF>'
    ).encode('utf-8')


_SETTINGS_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
    '<ha:HWPApplicationSetting xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app" '
    'xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0">'
    '<ha:CaretPosition listIDRef="0" paraIDRef="10" pos="0"/>'
    '</ha:HWPApplicationSetting>'
).encode('utf-8')

_CONTAINER_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
    '<ocf:container xmlns:ocf="urn:oasis:names:tc:opendocument:xmlns:container" '
    'xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf">'
    '<ocf:rootfiles>'
    '<ocf:rootfile full-path="Contents/content.hpf" '
    'media-type="application/hwpml-package+xml"/>'
    '<ocf:rootfile full-path="Preview/PrvText.txt" media-type="text/plain"/>'
    '<ocf:rootfile full-path="META-INF/container.rdf" media-type="application/rdf+xml"/>'
    '</ocf:rootfiles>'
    '</ocf:container>'
).encode('utf-8')

_MANIFEST_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
    '<odf:manifest xmlns:odf="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"/>'
).encode('utf-8')

_VERSION_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
    '<hv:HCFVersion xmlns:hv="http://www.hancom.co.kr/hwpml/2011/version" '
    'tagetApplication="WORDPROCESSOR" major="5" minor="1" micro="1" buildNumber="0" '
    'os="1" xmlVersion="1.5" application="Hancom Office Hangul" '
    'appVersion="12, 0, 0, 2535 WIN32LEWindows_10"/>'
).encode('utf-8')


# ── 공개 API ─────────────────────────────────────────────────────────

def build_from_markdown(md: str, output_path: Path, base_template: Path) -> dict:
    """
    Mathpix 마크다운 → HWPX 저장.

    Args:
        md:            Mathpix PDF OCR 결과 마크다운
        output_path:   저장 경로 (*.hwpx)
        base_template: header.xml 참조용 기존 워드초벌 파일

    Returns:
        {'paragraphs': int, 'equations': int, 'output': Path}
    """
    # 기존 템플릿에서 header.xml 추출 (폰트/스타일 재사용)
    with zipfile.ZipFile(base_template, 'r') as zf:
        header_xml = zf.read('Contents/header.xml')

    # section0.xml 생성
    lines = _preprocess_md(md)
    writer = _HwpxWriter()
    section_xml = writer.build_section(lines)
    section_bytes = section_xml.encode('utf-8')

    # 통계
    eq_count   = section_xml.count('<hp:equation')
    para_count = section_xml.count('<hp:p ')

    # [N점] plain 카운트: 마크다운 전체 출현 중 $...$ 내부가 아닌 것
    score_total   = len(re.findall(r'\[\d+(?:\.\d+)?점\]', md))
    score_in_math = len(re.findall(r'\$[^$\n]*\[\d+(?:\.\d+)?점\][^$\n]*\$', md))
    score_plain   = score_total - score_in_math

    print(
        f"  [v7] 보기 수식화 {writer._choice_eq}건 / "
        f"한글 plain {writer._choice_plain}건 / "
        f"[N점] plain {score_plain}/{score_total}건"
    )

    # ZIP 조합 (mimetype 은 반드시 첫 항목, ZIP_STORED)
    # 임시 파일에 먼저 쓰고 교체 — 원본이 열려있어도 우회
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix('.tmp.hwpx')
    with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zout:
        # mimetype 은 압축 없이 첫 번째로
        zout.writestr(
            zipfile.ZipInfo('mimetype'),
            'application/hwp+zip',
        )
        zout.writestr('version.xml',               _VERSION_XML)
        zout.writestr('Contents/header.xml',        header_xml)
        zout.writestr('Contents/masterpage0.xml',   _masterpage_xml())
        zout.writestr('Contents/section0.xml',      section_bytes)
        zout.writestr('Contents/content.hpf',       _content_hpf_xml())
        zout.writestr('META-INF/container.xml',     _CONTAINER_XML)
        zout.writestr('META-INF/container.rdf',     _container_rdf_xml())
        zout.writestr('META-INF/manifest.xml',      _MANIFEST_XML)
        zout.writestr('settings.xml',               _SETTINGS_XML)
        zout.writestr('Preview/PrvText.txt',        'PDF 변환 문서'.encode('utf-8'))

    # 임시 파일 → 최종 경로로 교체
    import shutil
    try:
        output_path.unlink(missing_ok=True)
        shutil.move(str(tmp_path), str(output_path))
    except PermissionError:
        # Windows: 파일이 다른 프로세스(한글 등)에 의해 잠긴 경우 → 대체 경로 사용
        alt = output_path.with_stem(output_path.stem + '_new')
        shutil.move(str(tmp_path), str(alt))
        print(f"  [주의] 기존 파일이 잠겨 있어 대체 경로에 저장: {alt.name}")
        output_path = alt

    return {
        'paragraphs':   para_count,
        'equations':    eq_count,
        'output':       output_path,
        'choice_eq':    writer._choice_eq,
        'choice_plain': writer._choice_plain,
        'score_plain':  score_plain,
        'score_total':  score_total,
    }
