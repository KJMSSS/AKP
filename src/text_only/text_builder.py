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
    # 멀티라인 $$...$$ → 한 줄
    md = re.sub(
        r'\$\$(.*?)\$\$',
        lambda m: '$$' + ' '.join(m.group(1).split()) + '$$',
        md, flags=re.DOTALL,
    )
    return md.split('\n')


# ── XML 생성 ─────────────────────────────────────────────────────────

class _HwpxWriter:
    """섹션 XML을 순차적으로 구성하는 내부 빌더."""

    def __init__(self):
        self._eq_id  = 3000
        self._eq_z   = 1
        self._para_id = 10

    # ── ID 발급 ──────────────────────────────────────────────────────

    def _eid(self) -> int:
        v = self._eq_id; self._eq_id += 1; return v

    def _ez(self) -> int:
        v = self._eq_z; self._eq_z += 1; return v

    def _pid(self) -> int:
        v = self._para_id; self._para_id += 1; return v

    # ── hp:equation ──────────────────────────────────────────────────

    def _equation(self, latex: str) -> str:
        hwp = latex_to_hwp(latex)
        return (
            f'<hp:equation id="{self._eid()}" zOrder="{self._ez()}" '
            f'numberingType="EQUATION" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" '
            f'lock="0" dropcapstyle="None" version="Equation Version 60" '
            f'baseLine="85" textColor="#000000" baseUnit="1100" lineMode="CHAR" font="HYhwpEQ">'
            f'<hp:sz width="2000" widthRelTo="ABSOLUTE" height="1500" heightRelTo="ABSOLUTE" protect="0"/>'
            f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
            f'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" '
            f'horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
            f'<hp:outMargin left="0" right="0" top="0" bottom="0"/>'
            f'<hp:script>{_xe(hwp)}</hp:script>'
            f'</hp:equation>'
        )

    # ── linesegarray ─────────────────────────────────────────────────

    def _lineseg(self, empty: bool = False) -> str:
        if empty:
            return '<hp:linesegarray/>'
        return (
            '<hp:linesegarray>'
            f'<hp:lineseg textpos="0" vertpos="0" vertsize="1600" textheight="1600" '
            f'baseline="1360" spacing="960" horzpos="0" horzsize="{_TW}" flags="393216"/>'
            '</hp:linesegarray>'
        )

    # ── hp:p (일반 문단) ─────────────────────────────────────────────

    def _para(self, segs: list[tuple[str, str]], cpr: int = 0) -> str:
        parts: list[str] = []
        for kind, content in segs:
            if kind == 'text':
                if content:
                    parts.append(f'<hp:t>{_xe(_to_circled(content))}</hp:t>')
            else:
                parts.append(self._equation(content))

        run = (
            f'<hp:run charPrIDRef="{cpr}">{"".join(parts)}</hp:run>'
            if parts else
            f'<hp:run charPrIDRef="{cpr}"/>'
        )
        return (
            f'<hp:p id="{self._pid()}" paraPrIDRef="8" styleIDRef="0" '
            f'pageBreak="0" columnBreak="0" merged="0">'
            f'{run}'
            f'{self._lineseg(empty=not parts)}'
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

    # ── 전체 section XML ─────────────────────────────────────────────

    def build_section(self, lines: list[str]) -> str:
        paras: list[str] = [self._secpr_para()]

        for raw in lines:
            # 마크다운 헤더 기호 제거
            line = re.sub(r'^#+\s*', '', raw)

            if not line.strip():
                paras.append(self._para([]))
                continue

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
    eq_count = section_xml.count('<hp:equation')
    para_count = section_xml.count('<hp:p ')

    # ZIP 조합 (mimetype 은 반드시 첫 항목, ZIP_STORED)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zout:
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

    return {
        'paragraphs': para_count,
        'equations':  eq_count,
        'output':     output_path,
    }
