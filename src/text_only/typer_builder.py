"""
1단 HWPX → 2단 타이퍼 양식 변환기

1단 파이프라인 출력 HWPX를 받아 학원 타이핑 양식(2단 A3)으로 변환한다.
각 문제마다: 1행×6열 메타 표 + 1단 본문 단락(스타일 조정)
"""
from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape as _xe

# ── XML 네임스페이스 ──────────────────────────────────────────────────
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

# ── 2단 A3 페이지 설정 (HWPUNIT = 1/7200 inch) ───────────────────────
_PW2     = 84188    # A3 폭 297mm
_PH2     = 119052   # A3 높이 420mm
_ML2     = 8504     # 좌/우 여백 30mm
_MR2     = 8504
_MT2     = 5669     # 상 여백 20mm
_MB2     = 4252     # 하 여백 15mm
_MH2     = 4252     # 헤더/푸터 여백 15mm
_MF2     = 4252
_COL_GAP = 2268     # 단 간격 8mm
_COL_W   = 32456    # 각 단 폭 = (84188 - 2*8504 - 2268) / 2

# ── 메타 표 (1행×6열) ─────────────────────────────────────────────────
# 순서: [번호칸(empty)] [학교명] [문제번호] [시험코드] [난이도] [배점]
_CELL_W    = [2857, 5956, 3976, 11347, 3140, 5121]
_CELL_BFID = [6,    5,    5,    5,     5,    5]     # borderFillIDRef
_TBL_W     = sum(_CELL_W)                           # 32397

# 1단 본문 폭 (너비 스케일링 기준)
_1DAN_TW = 48189

# 기본 참조 템플릿 (header.xml 소스)
_ROOT_DIR  = Path(__file__).resolve().parent.parent.parent
_REF_TYPER = _ROOT_DIR / 'samples' / '11b' / '[2025_1_1_b_공수1_경신여고].hwpx'


# ── 보조 XML ─────────────────────────────────────────────────────────

def _masterpage_xml() -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
        f'<masterPage {_NS} id="masterpage0" type="BOTH" '
        'pageNumber="0" pageDuplicate="0" pageFront="0">'
        f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="TOP" '
        f'linkListIDRef="0" linkListNextIDRef="0" textWidth="{_COL_W}" textHeight="0" '
        'hasTextRef="0" hasNumRef="0">'
        '<hp:p id="0" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
        '<hp:run charPrIDRef="0"/>'
        '<hp:linesegarray/>'
        '</hp:p>'
        '</hp:subList>'
        '</masterPage>'
    ).encode('utf-8')


def _content_hpf_xml(bindata_names: list[str] | None = None) -> bytes:
    """content.hpf — BinData 파일 목록을 포함해 동적 생성."""
    items = (
        '<opf:item id="header" href="Contents/header.xml" media-type="application/xml"/>'
        '<opf:item id="masterpage0" href="Contents/masterpage0.xml" media-type="application/xml"/>'
        '<opf:item id="section0" href="Contents/section0.xml" media-type="application/xml"/>'
        '<opf:item id="settings" href="settings.xml" media-type="application/xml"/>'
    )
    for name in (bindata_names or []):
        fname = name.split('/')[-1]
        stem  = fname.rsplit('.', 1)[0].lower()
        ext   = fname.rsplit('.', 1)[-1].lower()
        mime  = ('image/png' if ext == 'png'
                 else 'image/jpeg' if ext in ('jpg', 'jpeg')
                 else 'application/octet-stream')
        items += f'<opf:item id="{stem}" href="{name}" media-type="{mime}"/>'
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
        '<opf:package xmlns:opf="http://www.idpf.org/2007/opf/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" version="" unique-identifier="" id="">'
        '<opf:metadata><opf:title>타이퍼 양식</opf:title><opf:language>ko</opf:language></opf:metadata>'
        f'<opf:manifest>{items}</opf:manifest>'
        '<opf:spine><opf:itemref idref="section0"/></opf:spine>'
        '</opf:package>'
    ).encode('utf-8')

_CONTAINER_RDF = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
    '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    '<rdf:Description rdf:about="">'
    '<ns0:hasPart xmlns:ns0="http://www.hancom.co.kr/hwpml/2016/meta/pkg#" '
    'rdf:resource="Contents/header.xml"/></rdf:Description>'
    '<rdf:Description rdf:about="Contents/header.xml">'
    '<rdf:type rdf:resource="http://www.hancom.co.kr/hwpml/2016/meta/pkg#HeaderFile"/>'
    '</rdf:Description>'
    '<rdf:Description rdf:about="">'
    '<ns0:hasPart xmlns:ns0="http://www.hancom.co.kr/hwpml/2016/meta/pkg#" '
    'rdf:resource="Contents/masterpage0.xml"/></rdf:Description>'
    '<rdf:Description rdf:about="Contents/masterpage0.xml">'
    '<rdf:type rdf:resource="http://www.hancom.co.kr/hwpml/2016/meta/pkg#MasterPageFile"/>'
    '</rdf:Description>'
    '<rdf:Description rdf:about="">'
    '<ns0:hasPart xmlns:ns0="http://www.hancom.co.kr/hwpml/2016/meta/pkg#" '
    'rdf:resource="Contents/section0.xml"/></rdf:Description>'
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


# ── 파싱 헬퍼 ────────────────────────────────────────────────────────

def _renumber_zorders(sec_xml: str) -> str:
    """섹션 XML의 모든 zOrder 값을 1부터 순차 재발급해 중복 제거."""
    z = [1]
    def repl(m: re.Match) -> str:
        v = z[0]; z[0] += 1
        return f'zOrder="{v}"'
    return re.sub(r'zOrder="[^"]*"', repl, sec_xml)


def _extract_top_paras(xml_str: str) -> list[str]:
    """hs:sec 직속 hp:p 요소들을 XML 문자열 목록으로 반환 (depth tracking)."""
    paras: list[str] = []
    depth = 0
    start = -1
    for m in re.finditer(r'</?hp:p[\s>]', xml_str):
        tag = m.group(0)
        if tag.startswith('</'):
            depth -= 1
            if depth == 0 and start >= 0:
                end = xml_str.find('>', m.start()) + 1
                paras.append(xml_str[start:end])
                start = -1
        else:
            if depth == 0:
                start = m.start()
            depth += 1
    return paras


def _para_text(para_xml: str) -> str:
    """단락 XML에서 텍스트 내용 추출 (수식 제외)."""
    no_eq = re.sub(r'<hp:equation\b.*?</hp:equation>', '', para_xml, flags=re.DOTALL)
    return re.sub(r'<[^>]+>', '', no_eq).strip()


def _has_secpr(para_xml: str) -> bool:
    return '<hp:secPr' in para_xml


def _parse_prob_header(para_xml: str) -> tuple[int, float]:
    """
    문제 시작 단락이면 (prob_no, score) 반환, 아니면 (0, 0.0).
    패턴: 텍스트가 ^[0-9]{1,3}[.．] 로 시작
    """
    text = _para_text(para_xml)
    m = re.match(r'^(\d{1,3})[.．]', text)
    if not m:
        return 0, 0.0
    prob_no = int(m.group(1))
    sm = re.search(r'\[(\d+(?:\.\d+)?)점\]', text)
    score = float(sm.group(1)) if sm else 0.0
    return prob_no, score


def _extract_school(registry_key: str) -> str:
    """'[2025_2_1_b_공수1_경신여고]' → '경신여고'"""
    key = registry_key.strip('[]')
    parts = key.rsplit('_', 1)
    return parts[-1] if parts else key


def _extract_exam_code(registry_key: str) -> str:
    """'[2025_2_1_b_공수1_경신여고]' → '2025_2_1_b_공수1'"""
    key = registry_key.strip('[]')
    parts = key.rsplit('_', 1)
    return parts[0] if len(parts) > 1 else key


# ── 타이퍼 빌더 ──────────────────────────────────────────────────────

class _TyprWriter:

    def __init__(self):
        self._para_id = 10
        self._eq_id   = 3000
        self._eq_z    = 1
        self._tbl_id  = 1000

    def _pid(self) -> int:
        v = self._para_id; self._para_id += 1; return v

    def _eid(self) -> int:
        v = self._eq_id; self._eq_id += 1; return v

    def _ez(self) -> int:
        v = self._eq_z; self._eq_z += 1; return v

    def _tid(self) -> int:
        v = self._tbl_id; self._tbl_id += 1; return v

    # ── 2단 섹션 헤더 단락 ───────────────────────────────────────────

    def _secpr_para(self) -> str:
        return (
            '<hp:p id="1" paraPrIDRef="8" styleIDRef="0" '
            'pageBreak="0" columnBreak="0" merged="0">'
            '<hp:run charPrIDRef="0">'
            '<hp:secPr id="" textDirection="HORIZONTAL" spaceColumns="1200" '
            'tabStop="7000" tabStopVal="3500" tabStopUnit="HWPUNIT" '
            'outlineShapeIDRef="0" memoShapeIDRef="0" textVerticalWidthHead="0" masterPageCnt="1">'
            '<hp:grid lineGrid="0" charGrid="0" wonggojiFormat="0"/>'
            '<hp:startNum pageStartsOn="BOTH" page="0" pic="0" tbl="0" equation="0"/>'
            '<hp:visibility hideFirstHeader="0" hideFirstFooter="0" hideFirstMasterPage="0" '
            'border="SHOW_ALL" fill="SHOW_ALL" hideFirstPageNum="0" '
            'hideFirstEmptyLine="0" showLineNumber="0"/>'
            '<hp:lineNumberShape restartType="0" countBy="0" distance="0" startNumber="0"/>'
            f'<hp:pagePr landscape="WIDELY" width="{_PW2}" height="{_PH2}" gutterType="LEFT_ONLY">'
            f'<hp:margin header="{_MH2}" footer="{_MF2}" gutter="0" '
            f'left="{_ML2}" right="{_MR2}" top="{_MT2}" bottom="{_MB2}"/>'
            '</hp:pagePr>'
            '<hp:footNotePr>'
            '<hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" suffixChar=")" supscript="0"/>'
            '<hp:noteLine length="-1" type="SOLID" width="0.12 mm" color="#000000"/>'
            '<hp:noteSpacing betweenNotes="283" belowLine="567" aboveLine="850"/>'
            '<hp:numbering type="CONTINUOUS" newNum="1"/>'
            '<hp:placement place="EACH_COLUMN" beneathText="0"/>'
            '</hp:footNotePr>'
            '<hp:endNotePr>'
            '<hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" suffixChar=")" supscript="0"/>'
            f'<hp:noteLine length="{_COL_W}" type="SOLID" width="0.12 mm" color="#000000"/>'
            '<hp:noteSpacing betweenNotes="2834" belowLine="567" aboveLine="850"/>'
            '<hp:numbering type="CONTINUOUS" newNum="1"/>'
            '<hp:placement place="END_OF_DOCUMENT" beneathText="0"/>'
            '</hp:endNotePr>'
            '<hp:masterPage idRef="masterpage0"/>'
            '</hp:secPr>'
            '<hp:ctrl>'
            f'<hp:colPr id="" type="NEWSPAPER" layout="LEFT" colCount="2" sameSz="1" sameGap="{_COL_GAP}"/>'
            '</hp:ctrl>'
            '</hp:run>'
            '<hp:linesegarray>'
            f'<hp:lineseg textpos="0" vertpos="0" vertsize="1000" textheight="1000" '
            f'baseline="850" spacing="600" horzpos="0" horzsize="{_COL_W}" flags="393216"/>'
            '</hp:linesegarray>'
            '</hp:p>'
        )

    # ── 1×6 메타 표 단락 ────────────────────────────────────────────

    def _meta_table_para(
        self,
        school: str,
        prob_no: int,
        exam_code: str,
        difficulty: str,
        score: float,
    ) -> str:
        tbl_h      = 2131
        score_txt  = f'{score:g}점' if score > 0 else ''
        cell_texts = ['', school, f'{prob_no}번', exam_code, difficulty, score_txt]
        # 셀 0: paraPr=12/charPr=8/styleIDRef=1 (empty 칸)
        # 셀 1~5: paraPr=6/charPr=16/styleIDRef=3
        para_prs   = [12, 6, 6, 6, 6, 6]
        char_prs   = [8, 16, 16, 16, 16, 16]
        style_refs = [1, 3, 3, 3, 3, 3]
        # lineseg 파라미터 (실측값 기반)
        vs_list    = [1600, 1200, 1200, 1200, 1200, 1200]
        bl_list    = [1360, 1020, 1020, 1020, 1020, 1020]
        sp_list    = [960,  720,  720,  720,  720,  720]
        hp_list    = [0,    100,  100,  100,  100,  100]
        fl_list    = ['2490368', '393216', '393216', '393216', '393216', '393216']

        cells_xml = ''
        for ci in range(6):
            text    = cell_texts[ci]
            horzsize = _CELL_W[ci] - 1020 - hp_list[ci]
            cell_para = (
                f'<hp:p id="2147483648" paraPrIDRef="{para_prs[ci]}" '
                f'styleIDRef="{style_refs[ci]}" pageBreak="0" columnBreak="0" merged="0">'
                f'<hp:run charPrIDRef="{char_prs[ci]}">'
                + (f'<hp:t>{_xe(text)}</hp:t>' if text else '')
                + '</hp:run>'
                '<hp:linesegarray>'
                f'<hp:lineseg textpos="0" vertpos="0" vertsize="{vs_list[ci]}" '
                f'textheight="{vs_list[ci]}" baseline="{bl_list[ci]}" '
                f'spacing="{sp_list[ci]}" horzpos="{hp_list[ci]}" '
                f'horzsize="{horzsize}" flags="{fl_list[ci]}"/>'
                '</hp:linesegarray>'
                '</hp:p>'
            )
            cells_xml += (
                f'<hp:tc name="" header="0" hasMargin="0" protect="0" editable="0" dirty="0" '
                f'borderFillIDRef="{_CELL_BFID[ci]}">'
                '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" '
                'linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" '
                'hasTextRef="0" hasNumRef="0">'
                f'{cell_para}'
                '</hp:subList>'
                f'<hp:cellAddr colAddr="{ci}" rowAddr="0"/>'
                '<hp:cellSpan colSpan="1" rowSpan="1"/>'
                f'<hp:cellSz width="{_CELL_W[ci]}" height="{tbl_h}"/>'
                '<hp:cellMargin left="510" right="510" top="141" bottom="141"/>'
                '</hp:tc>'
            )

        tbl_xml = (
            f'<hp:tbl id="{self._tid()}" zOrder="{self._ez()}" '
            'numberingType="TABLE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" '
            'lock="0" dropcapstyle="None" pageBreak="CELL" repeatHeader="1" '
            f'rowCnt="1" colCnt="6" cellSpacing="0" borderFillIDRef="2" noAdjust="0">'
            f'<hp:sz width="{_TBL_W}" widthRelTo="ABSOLUTE" height="{tbl_h}" '
            'heightRelTo="ABSOLUTE" protect="0"/>'
            '<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
            'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="PARA" vertAlign="TOP" '
            'horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
            '<hp:outMargin left="0" right="0" top="0" bottom="283"/>'
            '<hp:inMargin left="510" right="510" top="141" bottom="141"/>'
            f'<hp:tr>{cells_xml}</hp:tr>'
            '</hp:tbl>'
        )

        return (
            f'<hp:p id="{self._pid()}" paraPrIDRef="5" styleIDRef="1" '
            'pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="8">{tbl_xml}<hp:t/></hp:run>'
            '<hp:linesegarray>'
            f'<hp:lineseg textpos="0" vertpos="0" vertsize="2414" textheight="2414" '
            f'baseline="2052" spacing="720" horzpos="0" horzsize="{_COL_W}" flags="393216"/>'
            '</hp:linesegarray>'
            '</hp:p>'
        )

    # ── 1단 → 2단 단락 변환 ─────────────────────────────────────────

    def _adapt_para(self, para_xml: str) -> str:
        """1단 단락 XML → 2단 스타일/크기로 변환."""
        xml = para_xml

        # 단락 ID 재발급 (외부 hp:p의 첫 번째 id= 만)
        xml = re.sub(r'\bid="[^"]*"', f'id="{self._pid()}"', xml, count=1)

        # 스타일: 외부 hp:p의 첫 출현만 교체 (count=1)
        xml = xml.replace('paraPrIDRef="8"', 'paraPrIDRef="5"', 1)
        xml = xml.replace('styleIDRef="0"',  'styleIDRef="1"',  1)
        xml = xml.replace('charPrIDRef="0"', 'charPrIDRef="8"', 1)

        # lineseg horzsize → 2단 열폭
        xml = re.sub(r'horzsize="\d+"', f'horzsize="{_COL_W}"', xml)

        # 수식 ID/zOrder 재발급
        def _renumber_eq(m: re.Match) -> str:
            s = m.group(0)
            s = re.sub(r'(?<=\s)id="[^"]*"', f'id="{self._eid()}"', s, count=1)
            s = re.sub(r'zOrder="[^"]*"',    f'zOrder="{self._ez()}"', s, count=1)
            return s
        xml = re.sub(r'<hp:equation\b.*?</hp:equation>', _renumber_eq, xml, flags=re.DOTALL)

        # 표 너비 스케일링 (1단→2단 열폭 비율)
        ratio = _COL_W / _1DAN_TW

        def _scale_sz(m: re.Match) -> str:
            w = int(m.group(1))
            return f'<hp:sz width="{round(w * ratio)}"' if w <= _1DAN_TW else m.group(0)

        def _scale_cell(m: re.Match) -> str:
            w = int(m.group(1))
            return f'<hp:cellSz width="{round(w * ratio)}"' if w <= _1DAN_TW else m.group(0)

        xml = re.sub(r'<hp:sz width="(\d+)"',     _scale_sz,   xml)
        xml = re.sub(r'<hp:cellSz width="(\d+)"', _scale_cell, xml)

        return xml

    # ── 전체 섹션 XML ────────────────────────────────────────────────

    def build_section(
        self,
        one_dan_xml: str,
        school: str,
        exam_code: str,
        difficulty_map: dict[int, str],
    ) -> str:
        top_paras = _extract_top_paras(one_dan_xml)

        # 문제 그룹화
        problems: list[tuple[int, float, list[str]]] = []
        cur_no    = 0
        cur_score = 0.0
        cur_paras: list[str] = []

        for para_xml in top_paras:
            if _has_secpr(para_xml):
                continue
            prob_no, score = _parse_prob_header(para_xml)
            if prob_no > 0:
                if cur_no > 0:
                    problems.append((cur_no, cur_score, cur_paras))
                cur_no, cur_score, cur_paras = prob_no, score, [para_xml]
            elif cur_no > 0:
                cur_paras.append(para_xml)
            # prob_no==0 and cur_no==0: 머릿말 — 스킵

        if cur_no > 0:
            problems.append((cur_no, cur_score, cur_paras))

        # XML 조립
        parts: list[str] = [self._secpr_para()]
        for prob_no, score, paras in problems:
            difficulty = difficulty_map.get(prob_no, '')
            parts.append(self._meta_table_para(school, prob_no, exam_code, difficulty, score))
            for p in paras:
                # 내용 없는 순수 빈 단락은 제외 (메타 표가 구분자 역할)
                if not _para_text(p) and '<hp:equation' not in p and '<hp:tbl' not in p:
                    continue
                parts.append(self._adapt_para(p))

        sec = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
            f'<hs:sec {_NS}>'
            + ''.join(parts)
            + '</hs:sec>'
        )
        return _renumber_zorders(sec)


# ── 공개 API ─────────────────────────────────────────────────────────

def build_typer_hwpx(
    one_dan_path: Path,
    registry_key: str,
    out_path: Path,
    ref_template: Path | None = None,
    difficulty_map: dict[int, str] | None = None,
    school_name: str = '',
) -> Path:
    """
    1단 HWPX → 2단 타이퍼 양식 변환.

    Args:
        one_dan_path:   1단 파이프라인 출력 HWPX
        registry_key:   레지스트리 키 (exam_code/학교명 추출용)
        out_path:       출력 HWPX 경로
        ref_template:   2단 참조 템플릿 (header.xml 소스). None이면 기본값 사용
        difficulty_map: {문제번호: 난이도문자열} — 빈 셀이면 ''
        school_name:    학교명 override (없으면 registry_key에서 추출)

    Returns:
        저장된 HWPX 경로
    """
    if ref_template is None:
        ref_template = _REF_TYPER
    if not ref_template.exists():
        raise FileNotFoundError(f'참조 템플릿 없음: {ref_template}')

    difficulty_map = difficulty_map or {}
    school    = school_name or _extract_school(registry_key)
    exam_code = _extract_exam_code(registry_key)

    # 1단 section0.xml 읽기
    with zipfile.ZipFile(one_dan_path, 'r') as zf:
        one_dan_xml = zf.read('Contents/section0.xml').decode('utf-8')
        bindata: dict[str, bytes] = {
            name: zf.read(name)
            for name in zf.namelist()
            if name.startswith('BinData/')
        }

    # 2단 헤더 읽기 (스타일/폰트 정의)
    with zipfile.ZipFile(ref_template, 'r') as zf:
        header_xml = zf.read('Contents/header.xml')

    # section0.xml 생성
    writer    = _TyprWriter()
    sec_xml   = writer.build_section(one_dan_xml, school, exam_code, difficulty_map)
    sec_bytes = sec_xml.encode('utf-8')

    prob_count = sec_xml.count('rowCnt="1" colCnt="6"')
    eq_count   = sec_xml.count('<hp:equation')
    para_count = sec_xml.count('<hp:p ')
    print(f'  [typer] 문제 {prob_count}건 / 수식 {eq_count}건 / 단락 {para_count}건')

    # ZIP 패키징
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix('.tmp.hwpx')

    with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zout:
        zout.writestr(zipfile.ZipInfo('mimetype'), 'application/hwp+zip')
        zout.writestr('version.xml',              _VERSION_XML)
        zout.writestr('Contents/header.xml',      header_xml)
        zout.writestr('Contents/masterpage0.xml', _masterpage_xml())
        zout.writestr('Contents/section0.xml',    sec_bytes)
        zout.writestr('Contents/content.hpf',     _content_hpf_xml(list(bindata.keys())))
        zout.writestr('META-INF/container.xml',   _CONTAINER_XML)
        zout.writestr('META-INF/container.rdf',   _CONTAINER_RDF)
        zout.writestr('META-INF/manifest.xml',    _MANIFEST_XML)
        zout.writestr('settings.xml',             _SETTINGS_XML)
        zout.writestr('Preview/PrvText.txt',      '타이퍼 양식'.encode('utf-8'))
        for name, data in bindata.items():
            zout.writestr(name, data)

    try:
        out_path.unlink(missing_ok=True)
        shutil.move(str(tmp_path), str(out_path))
    except PermissionError:
        alt = out_path.with_stem(out_path.stem + '_new')
        shutil.move(str(tmp_path), str(alt))
        print(f'  [주의] 잠금: 대체 경로 저장 → {alt.name}')
        out_path = alt

    return out_path
