"""
1단 HWPX → 2단 타이퍼 양식 변환기

1단 파이프라인 출력 HWPX를 받아 학원 타이핑 양식(2단 A3)으로 변환한다.
각 문제마다: 1행×6열 메타 표 + 1단 본문 단락(스타일 조정)
"""
from __future__ import annotations

import re
import shutil
import zipfile
from dataclasses import dataclass
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
# samples/template.hwpx 는 git 추적 파일 — Railway 배포 환경에서도 항상 존재
_ROOT_DIR     = Path(__file__).resolve().parent.parent.parent
_REF_TYPER    = _ROOT_DIR / 'samples' / 'template.hwpx'
_REF_SUHBISEO = _ROOT_DIR / 'samples' / 'suhbiseo_template.hwpx'   # 수학비서(서울세종고) 명조 header


# ── 출력 양식 프로필 ─────────────────────────────────────────────────
@dataclass(frozen=True)
class FormatProfile:
    """출력 양식 = 페이지 기하 + 메타표/스타일 정책 + header.xml 소스.

    타이퍼: A3 2단 + 문제별 1×6 메타표 + 스타일 재매핑(template.hwpx 화려 폰트).
    수학비서: B4 2단, 메타표 없음, 1단 스타일 ID 유지(서울세종고 명조 header).
    """
    name: str
    page_w: int
    page_h: int
    ml: int
    mr: int
    mt: int
    mb: int
    mh: int
    mf: int
    col_gap: int
    col_w: int
    meta_table: bool        # 문제별 1×6 메타표 삽입 (타이퍼 전용)
    remap_styles: bool      # _adapt_para 스타일 ID 재매핑 (타이퍼 전용)
    ref_template: Path      # header.xml 소스
    prv_title: str
    col_count: int = 2
    title_block: bool = False   # 상단 제목블록(로고+제목+쪽번호+과목박스+범위) 주입 (수학비서)
    prob_meta_line: bool = False  # 매 문제 앞 1×2 번호줄("{제목} {번호} [{배점}점]" | "난이도") 주입 (수학비서)


# 타이퍼 = 기존 동작 (모듈 상수 그대로) → 무회귀
TYPER = FormatProfile(
    name='타이퍼', page_w=_PW2, page_h=_PH2, ml=_ML2, mr=_MR2, mt=_MT2, mb=_MB2,
    mh=_MH2, mf=_MF2, col_gap=_COL_GAP, col_w=_COL_W,
    meta_table=True, remap_styles=True, ref_template=_REF_TYPER, prv_title='타이퍼 양식',
)

# 수학비서(학원) = B4 2단 명조, 메타표 없음, 스타일 유지, 제목블록 주입 (서울세종고.hwpx 기준)
SUHBISEO = FormatProfile(
    name='수학비서', page_w=72852, page_h=103180, ml=5102, mr=5102, mt=4251, mb=3685,
    mh=5669, mf=3685, col_gap=2268, col_w=30190,
    meta_table=False, remap_styles=False, ref_template=_REF_SUHBISEO, prv_title='수학비서 양식',
    title_block=True, prob_meta_line=True,
)

# 과목 약어 → 정식 표기 (제목블록 과목 박스용)
_SUBJECT_MAP = {
    '수상': '수학상', '수하': '수학하', '수1': '수학Ⅰ', '수2': '수학Ⅱ',
    '미적분': '미적분', '확통': '확률과통계', '기하': '기하',
    '공수1': '공통수학1', '공수2': '공통수학2', '확률과통계': '확률과통계',
}

_PROFILES = {'타이퍼': TYPER, '수학비서': SUHBISEO}

# 매 문제 앞 번호줄(1×2) 셀 폭 비율 — 동성고 250409 실측(23302:6322 ≈ 0.7865:0.2135)
_PMETA_W0_RATIO = 0.7865


# ── 보조 XML ─────────────────────────────────────────────────────────

def _masterpage_xml(col_w: int = _COL_W) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
        f'<masterPage {_NS} id="masterpage0" type="BOTH" '
        'pageNumber="0" pageDuplicate="0" pageFront="0">'
        f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="TOP" '
        f'linkListIDRef="0" linkListNextIDRef="0" textWidth="{col_w}" textHeight="0" '
        'hasTextRef="0" hasNumRef="0">'
        '<hp:p id="0" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
        '<hp:run charPrIDRef="0"/>'
        '<hp:linesegarray/>'
        '</hp:p>'
        '</hp:subList>'
        '</masterPage>'
    ).encode('utf-8')


def _content_hpf_xml(bindata_names: list[str] | None = None, title: str = '타이퍼 양식') -> bytes:
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
        f'<opf:metadata><opf:title>{_xe(title)}</opf:title><opf:language>ko</opf:language></opf:metadata>'
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
    - 일반: ^[0-9]{1,3}[.．)］] (마침표형 "1." / 닫는괄호형 "5)")
    - 서술형: "서술형1)" "[서술형] 1" → prob_no = 100 + N
      (메타표에서 "서술형 N" 으로 표기; 문서상 단답형보다 앞이라 순서 보존됨)
    """
    text = _para_text(para_xml)

    def _score(t: str) -> float:
        sm = re.search(r'\[(\d+(?:\.\d+)?)점\]', t)
        return float(sm.group(1)) if sm else 0.0

    # 서술형 — "〈서술형문제〉" 안내문(괄호 시작)은 제외, "서술형N" 헤더만
    subj = re.match(r'^\[?\s*서술형\s*\]?\s*(\d{1,2})', text)
    if subj:
        return 100 + int(subj.group(1)), _score(text)

    m = re.match(r'^(\d{1,3})[.．)）]', text)
    if not m:
        return 0, 0.0
    return int(m.group(1)), _score(text)


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


def _find_range(top_paras: list[str]) -> str:
    """범위줄(예: '다항식의 연산 ~ 이차함수') 추정 — 본문 앞쪽 '~' 포함 짧은 단락."""
    for p in top_paras[:8]:
        t = _para_text(p)
        if '~' in t and not re.match(r'^\s*\d', t) and 4 <= len(t) <= 60:
            return t
    return ''


def _patch_header_for_meta_line(header_xml: bytes) -> tuple[bytes, dict[str, int]]:
    """수학비서 header.xml에 매-문제 번호줄 스타일(난이도 초록색 포함) 항목을 새 ID로 주입.

    서울세종고 header에는 해당 스타일이 없고, 동성고 250409 참조본의 같은 ID 번호는
    다른(충돌하는) 정의라 그대로 재사용할 수 없다(예: borderFill id=6 서울=가시선/동성=투명).
    그래서 항목을 헤더에 실재하는 최대 ID + 1로 새로 추가한다.
    borderFillIDRef="1"(무테두리)은 두 header가 바이트 동일해 그대로 재사용.
    """
    xml = header_xml.decode('utf-8')

    def _next_id(tag: str) -> int:
        ids = [int(m) for m in re.findall(rf'<hh:{tag} id="(\d+)"', xml)]
        return (max(ids) + 1) if ids else 0

    bf_id   = _next_id('borderFill')
    char_lb = _next_id('charPr')
    char_df = char_lb + 1
    para_lb = _next_id('paraPr')
    para_df = para_lb + 1

    bf_xml = (
        f'<hh:borderFill id="{bf_id}" threeD="0" shadow="0" centerLine="NONE" breakCellSeparateLine="0">'
        '<hh:slash type="NONE" Crooked="0" isCounter="0"/><hh:backSlash type="NONE" Crooked="0" isCounter="0"/>'
        '<hh:leftBorder type="NONE" width="0.12 mm" color="#000000"/>'
        '<hh:rightBorder type="NONE" width="0.12 mm" color="#000000"/>'
        '<hh:topBorder type="NONE" width="0.12 mm" color="#000000"/>'
        '<hh:bottomBorder type="NONE" width="0.12 mm" color="#000000"/>'
        '<hh:diagonal type="SOLID" width="0.1 mm" color="#000000"/>'
        '<hc:fillBrush><hc:winBrush faceColor="none" hatchColor="#000000" alpha="0"/></hc:fillBrush>'
        '</hh:borderFill>'
    )
    char_lb_xml = (
        f'<hh:charPr id="{char_lb}" height="900" textColor="#000000" shadeColor="none" '
        f'useFontSpace="0" useKerning="0" symMark="NONE" borderFillIDRef="{bf_id}">'
        '<hh:fontRef hangul="1" latin="1" hanja="1" japanese="1" other="1" symbol="1" user="1"/>'
        '<hh:ratio hangul="95" latin="95" hanja="95" japanese="95" other="95" symbol="95" user="95"/>'
        '<hh:spacing hangul="-5" latin="-5" hanja="-5" japanese="-5" other="-5" symbol="-5" user="-5"/>'
        '<hh:relSz hangul="100" latin="100" hanja="100" japanese="100" other="100" symbol="100" user="100"/>'
        '<hh:offset hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>'
        '<hh:underline type="NONE" shape="SOLID" color="#000000"/>'
        '<hh:strikeout shape="NONE" color="#000000"/><hh:outline type="NONE"/>'
        '<hh:shadow type="NONE" color="#C0C0C0" offsetX="10" offsetY="10"/>'
        '</hh:charPr>'
    )
    char_df_xml = (
        f'<hh:charPr id="{char_df}" height="900" textColor="#4F7429" shadeColor="none" '
        'useFontSpace="0" useKerning="0" symMark="NONE" borderFillIDRef="1">'
        '<hh:fontRef hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>'
        '<hh:ratio hangul="95" latin="95" hanja="95" japanese="95" other="95" symbol="95" user="95"/>'
        '<hh:spacing hangul="-5" latin="-5" hanja="-5" japanese="-5" other="-5" symbol="-5" user="-5"/>'
        '<hh:relSz hangul="100" latin="100" hanja="100" japanese="100" other="100" symbol="100" user="100"/>'
        '<hh:offset hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>'
        '<hh:bold/>'
        '<hh:underline type="NONE" shape="SOLID" color="#000000"/>'
        '<hh:strikeout shape="NONE" color="#000000"/><hh:outline type="NONE"/>'
        '<hh:shadow type="NONE" color="#C0C0C0" offsetX="10" offsetY="10"/>'
        '</hh:charPr>'
    )

    def _para_xml(id_: int, align: str, bf: int) -> str:
        return (
            f'<hh:paraPr id="{id_}" tabPrIDRef="0" condense="0" fontLineHeight="0" snapToGrid="0" '
            f'suppressLineNumbers="0" checked="0"><hh:align horizontal="{align}" vertical="BASELINE"/>'
            '<hh:heading type="NONE" idRef="0" level="0"/>'
            '<hh:breakSetting breakLatinWord="KEEP_WORD" breakNonLatinWord="BREAK_WORD" widowOrphan="0" '
            'keepWithNext="0" keepLines="0" pageBreakBefore="0" lineWrap="BREAK"/>'
            '<hh:autoSpacing eAsianEng="0" eAsianNum="0"/>'
            '<hp:switch><hp:case hp:required-namespace="http://www.hancom.co.kr/hwpml/2016/HwpUnitChar">'
            '<hh:margin><hc:intent value="0" unit="HWPUNIT"/><hc:left value="500" unit="HWPUNIT"/>'
            '<hc:right value="500" unit="HWPUNIT"/><hc:prev value="0" unit="HWPUNIT"/>'
            '<hc:next value="0" unit="HWPUNIT"/></hh:margin>'
            '<hh:lineSpacing type="PERCENT" value="165" unit="HWPUNIT"/></hp:case>'
            '<hp:default><hh:margin><hc:intent value="0" unit="HWPUNIT"/><hc:left value="1000" unit="HWPUNIT"/>'
            '<hc:right value="1000" unit="HWPUNIT"/><hc:prev value="0" unit="HWPUNIT"/>'
            '<hc:next value="0" unit="HWPUNIT"/></hh:margin>'
            '<hh:lineSpacing type="PERCENT" value="165" unit="HWPUNIT"/></hp:default></hp:switch>'
            f'<hh:border borderFillIDRef="{bf}" offsetLeft="0" offsetRight="0" offsetTop="0" offsetBottom="0" '
            'connect="0" ignoreMargin="0"/></hh:paraPr>'
        )

    para_lb_xml = _para_xml(para_lb, 'LEFT', bf_id)
    para_df_xml = _para_xml(para_df, 'RIGHT', 1)

    def _insert_before(xml_: str, list_close: str, addition: str, count: int) -> str:
        idx = xml_.index(list_close)
        xml_ = xml_[:idx] + addition + xml_[idx:]
        open_tag = '<hh:' + list_close[len('</hh:'):-1] + ' itemCnt="'
        oi = xml_.index(open_tag) + len(open_tag)
        oe = xml_.index('"', oi)
        return xml_[:oi] + str(int(xml_[oi:oe]) + count) + xml_[oe:]

    xml = _insert_before(xml, '</hh:borderFills>', bf_xml, 1)
    xml = _insert_before(xml, '</hh:charProperties>', char_lb_xml + char_df_xml, 2)
    xml = _insert_before(xml, '</hh:paraProperties>', para_lb_xml + para_df_xml, 2)

    ids = {
        'char_label': char_lb, 'char_diff': char_df,
        'para_label': para_lb, 'para_diff': para_df,
    }
    return xml.encode('utf-8'), ids


# ── 타이퍼 빌더 ──────────────────────────────────────────────────────

class _TyprWriter:

    def __init__(self, profile: FormatProfile = TYPER, meta_line_ids: dict[str, int] | None = None):
        self.pf       = profile
        self._para_id = 10
        self._eq_id   = 3000
        self._eq_z    = 1
        self._tbl_id  = 1000
        self._meta_line_ids = meta_line_ids

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
            f'<hp:pagePr landscape="WIDELY" width="{self.pf.page_w}" height="{self.pf.page_h}" gutterType="LEFT_ONLY">'
            f'<hp:margin header="{self.pf.mh}" footer="{self.pf.mf}" gutter="0" '
            f'left="{self.pf.ml}" right="{self.pf.mr}" top="{self.pf.mt}" bottom="{self.pf.mb}"/>'
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
            f'<hp:noteLine length="{self.pf.col_w}" type="SOLID" width="0.12 mm" color="#000000"/>'
            '<hp:noteSpacing betweenNotes="2834" belowLine="567" aboveLine="850"/>'
            '<hp:numbering type="CONTINUOUS" newNum="1"/>'
            '<hp:placement place="END_OF_DOCUMENT" beneathText="0"/>'
            '</hp:endNotePr>'
            '<hp:masterPage idRef="masterpage0"/>'
            '</hp:secPr>'
            '<hp:ctrl>'
            f'<hp:colPr id="" type="NEWSPAPER" layout="LEFT" colCount="{self.pf.col_count}" sameSz="1" sameGap="{self.pf.col_gap}"/>'
            '</hp:ctrl>'
            '</hp:run>'
            '<hp:linesegarray>'
            f'<hp:lineseg textpos="0" vertpos="0" vertsize="1000" textheight="1000" '
            f'baseline="850" spacing="600" horzpos="0" horzsize="{self.pf.col_w}" flags="393216"/>'
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
        # 서술형(100+N)은 "서술형 N", 일반은 "N번"
        prob_label = f'서술형 {prob_no - 100}' if prob_no > 100 else f'{prob_no}번'
        cell_texts = ['', school, prob_label, exam_code, difficulty, score_txt]
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
            f'baseline="2052" spacing="720" horzpos="0" horzsize="{self.pf.col_w}" flags="393216"/>'
            '</hp:linesegarray>'
            '</hp:p>'
        )

    # ── 매 문제 앞 1×2 번호줄 (수학비서 전용) ─────────────────────────

    def _prob_meta_line_para(
        self,
        title_line: str,
        prob_no: int,
        score: float,
        difficulty: str,
    ) -> str:
        """"{제목} {번호} [{배점}점]" | "난이도 {상/중/하}" 1×2 표 — 매 문제 앞 반복.

        서울세종고 header에 없는 스타일이라 build_typer_hwpx가 미리 주입한
        _meta_line_ids(charPr/paraPr)를 사용한다. 표 테두리는 borderFillIDRef="1"
        (무테두리, 두 header 공통 정의)로 재사용해 header 패치를 최소화한다.
        """
        ids = self._meta_line_ids
        prob_label = f'서술형 {prob_no - 100}' if prob_no > 100 else str(prob_no)
        cell0_text = f'{title_line} {prob_label} [{score:.2f}점]'
        cell1_text = f'난이도 {difficulty}'.rstrip()

        tbl_h   = 2032
        total_w = self.pf.col_w
        w0      = round(total_w * _PMETA_W0_RATIO)
        w1      = total_w - w0

        def _cell(text: str, col_addr: int, width: int, para_pr: int, char_pr: int) -> str:
            horzsize = width - 1020
            return (
                '<hp:tc name="" header="0" hasMargin="0" protect="0" editable="0" dirty="0" '
                'borderFillIDRef="1">'
                '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" '
                'linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" '
                'hasTextRef="0" hasNumRef="0">'
                f'<hp:p id="0" paraPrIDRef="{para_pr}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
                f'<hp:run charPrIDRef="{char_pr}"><hp:t>{_xe(text)}</hp:t></hp:run>'
                '<hp:linesegarray>'
                f'<hp:lineseg textpos="0" vertpos="0" vertsize="900" textheight="900" '
                f'baseline="765" spacing="584" horzpos="500" horzsize="{horzsize}" flags="393216"/>'
                '</hp:linesegarray></hp:p></hp:subList>'
                f'<hp:cellAddr colAddr="{col_addr}" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/>'
                f'<hp:cellSz width="{width}" height="{tbl_h}"/>'
                '<hp:cellMargin left="510" right="510" top="141" bottom="141"/></hp:tc>'
            )

        cells_xml = (
            _cell(cell0_text, 0, w0, ids['para_label'], ids['char_label'])
            + _cell(cell1_text, 1, w1, ids['para_diff'], ids['char_diff'])
        )

        tbl_xml = (
            f'<hp:tbl id="{self._tid()}" zOrder="{self._ez()}" '
            'numberingType="TABLE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" '
            'lock="0" dropcapstyle="None" pageBreak="CELL" repeatHeader="1" '
            'rowCnt="1" colCnt="2" cellSpacing="0" borderFillIDRef="1" noAdjust="0">'
            f'<hp:sz width="{total_w}" widthRelTo="ABSOLUTE" height="{tbl_h}" '
            'heightRelTo="ABSOLUTE" protect="0"/>'
            '<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
            'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="PARA" vertAlign="TOP" '
            'horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
            '<hp:outMargin left="0" right="0" top="0" bottom="0"/>'
            '<hp:inMargin left="0" right="0" top="566" bottom="566"/>'
            f'<hp:tr>{cells_xml}</hp:tr>'
            '</hp:tbl>'
        )

        return (
            f'<hp:p id="{self._pid()}" paraPrIDRef="8" styleIDRef="0" '
            'pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="0">{tbl_xml}<hp:t/></hp:run>'
            '<hp:linesegarray>'
            f'<hp:lineseg textpos="0" vertpos="0" vertsize="{tbl_h}" textheight="{tbl_h}" '
            f'baseline="{round(tbl_h * 0.85)}" spacing="{round(tbl_h * 0.3)}" horzpos="0" '
            f'horzsize="{total_w}" flags="393216"/>'
            '</hp:linesegarray>'
            '</hp:p>'
        )

    # ── 1단 → 2단 단락 변환 ─────────────────────────────────────────

    def _adapt_para(self, para_xml: str) -> str:
        """1단 단락 XML → 2단 스타일/크기로 변환."""
        xml = para_xml

        # 단락 ID 재발급 (외부 hp:p의 첫 번째 id= 만)
        xml = re.sub(r'\bid="[^"]*"', f'id="{self._pid()}"', xml, count=1)

        # 스타일 재매핑(타이퍼 전용): 외부 hp:p 첫 출현만 교체.
        # 수학비서는 1단 스타일 ID(paraPr8/style0/charPr0)를 그대로 둔다 — 서울세종고
        # header에 style id가 0 하나뿐이라 1로 매핑하면 깨짐.
        if self.pf.remap_styles:
            xml = xml.replace('paraPrIDRef="8"', 'paraPrIDRef="5"', 1)
            xml = xml.replace('styleIDRef="0"',  'styleIDRef="1"',  1)
            xml = xml.replace('charPrIDRef="0"', 'charPrIDRef="8"', 1)

        # lineseg horzsize → 2단 열폭
        xml = re.sub(r'horzsize="\d+"', f'horzsize="{self.pf.col_w}"', xml)

        # 그림(hp:pic) 단락은 기하를 열폭에 맞게 따로 스케일 (표/수식 규칙 미적용)
        if '<hp:pic' in xml:
            return self._scale_pic_para(xml)

        ratio = self.pf.col_w / _1DAN_TW

        # 수식: ID/zOrder 재발급 + 2단 폰트에 맞춰 sz 비례 축소 (수식 블록 한정)
        def _renumber_eq(m: re.Match) -> str:
            s = m.group(0)
            s = re.sub(r'(?<=\s)id="[^"]*"', f'id="{self._eid()}"', s, count=1)
            s = re.sub(r'zOrder="[^"]*"',    f'zOrder="{self._ez()}"', s, count=1)
            s = re.sub(
                r'<hp:sz width="(\d+)"',
                lambda mm: (f'<hp:sz width="{round(int(mm.group(1)) * ratio)}"'
                            if int(mm.group(1)) <= _1DAN_TW else mm.group(0)),
                s)
            return s
        xml = re.sub(r'<hp:equation\b.*?</hp:equation>', _renumber_eq, xml, flags=re.DOTALL)

        # 표(조건/보기/데이터): 단 폭을 '넘을 때만' 비례 축소(fit).
        # 이미 단보다 좁으면 자연 크기 유지 — 1단 비율로 과소축소(예: 60%)하지 않고
        # 단을 제대로 채운다. 표 자신의 sz(첫 출현)와 모든 cellSz를 동일 배율로.
        def _fit_table(mt: re.Match) -> str:
            block = mt.group(0)
            sm = re.search(r'<hp:sz width="(\d+)"', block)
            if not sm:
                return block
            tw = int(sm.group(1))
            if tw <= self.pf.col_w:
                return block  # 이미 단에 들어감
            f = self.pf.col_w / tw
            block = re.sub(r'<hp:sz width="\d+"',
                           f'<hp:sz width="{round(tw * f)}"', block, count=1)
            block = re.sub(
                r'<hp:cellSz width="(\d+)"',
                lambda mm: f'<hp:cellSz width="{round(int(mm.group(1)) * f)}"', block)
            return block
        xml = re.sub(r'<hp:tbl\b.*?</hp:tbl>', _fit_table, xml, flags=re.DOTALL)

        return xml

    def _scale_pic_para(self, xml: str) -> str:
        """그림 단락 기하를 2단 열폭에 맞게 비율 유지 축소.

        열폭(여백 6% 차감)보다 넓은 그림만 줄이고, 작으면 원본 크기 유지.
        폭·높이·좌표를 동일 배율로 일괄 조정해 왜곡을 막는다. zOrder는
        섹션 단위 재발급(_renumber_zorders)이 처리하므로 여기선 건드리지 않음.
        """
        m = re.search(r'<hp:orgSz width="(\d+)" height="(\d+)"', xml)
        if not m:
            return xml
        w0 = int(m.group(1))
        usable = round(self.pf.col_w * 0.94)
        if w0 <= usable:
            return xml  # 이미 열 안에 들어감 — 원본 유지

        f = usable / w0

        def _sc(mm: "re.Match") -> str:
            return f'{mm.group(1)}="{round(int(mm.group(2)) * f)}"'

        # 그림 기하·세로줄 높이를 동일 배율로 (가로 horzsize는 이미 _COL_W)
        return re.sub(
            r'\b(width|height|x|y|right|bottom|dimwidth|dimheight'
            r'|centerX|centerY|vertsize|textheight|baseline)="(\d+)"',
            _sc, xml)

    # ── 수학비서 상단 제목블록 ──────────────────────────────────────

    @staticmethod
    def _title_line(parts: list[str], subj_abbr: str, school: str) -> str:
        try:
            yy, grade, sem = parts[0][2:], parts[1], parts[2]
            mid = '중간' if parts[3] == 'a' else '기말'
            return f'(기출) {yy} 고{grade}-{sem} {mid} {subj_abbr} {school}'.strip()
        except Exception:
            return school

    def _title_block(self, title: str, subj_ab: str, range_text: str) -> str:
        """ref_template(서울세종고)에서 제목블록 6단락을 추출해 텍스트 3개만 치환.

        로고(image2)·쪽번호표(autoNum)·과목 박스(hp:rect) 구조는 그대로 보존.
        실패하면 '' 반환(제목블록 없이 진행 — 안전).
        """
        try:
            with zipfile.ZipFile(self.pf.ref_template) as zf:
                ref = zf.read('Contents/section0.xml').decode('utf-8')
            body  = ref[ref.find('</hp:secPr>') + len('</hp:secPr>'):]
            paras = _extract_top_paras(body)[:6]
            if len(paras) < 6:
                return ''
            block   = ''.join(paras)
            subject = _SUBJECT_MAP.get(subj_ab, subj_ab)
            block = block.replace('(강남 기출) 24 고1-1 중간 수상 서울세종고', _xe(title))
            block = block.replace('<hp:t>수학상</hp:t>', f'<hp:t>{_xe(subject)}</hp:t>')
            block = block.replace('다항식의 연산 ~ 이차함수와 이차방정식', _xe(range_text or ''))
            # 외부 단락 id 재발급(우리 본문과 충돌 방지)
            return ''.join(
                re.sub(r'\bid="[^"]*"', f'id="{self._pid()}"', p, count=1)
                for p in _extract_top_paras(block)
            )
        except Exception:
            return ''

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
        self.prob_count = len(problems)

        # XML 조립
        ec      = exam_code.split('_')
        subj_ab = ec[4] if len(ec) > 4 else ''
        title_line = self._title_line(ec, subj_ab, school)

        parts: list[str] = [self._secpr_para()]
        # 수학비서: 상단 제목블록(로고+제목+쪽번호+과목박스+범위) 주입
        if self.pf.title_block:
            tb = self._title_block(title_line, subj_ab, _find_range(top_paras))
            if tb:
                parts.append(tb)
        for prob_no, score, paras in problems:
            difficulty = difficulty_map.get(prob_no, '')
            # 타이퍼만 문제별 1×6 메타표 삽입. 수학비서는 문제별 1×2 번호줄 삽입.
            if self.pf.meta_table:
                parts.append(self._meta_table_para(school, prob_no, exam_code, difficulty, score))
            if self.pf.prob_meta_line:
                parts.append(self._prob_meta_line_para(title_line, prob_no, score, difficulty))
            for p in paras:
                # 내용 없는 순수 빈 단락은 제외 (메타 표가 구분자 역할)
                # ★ 그림(hp:pic) 단락은 텍스트·수식·표가 없어도 보존해야 함
                if (not _para_text(p) and '<hp:equation' not in p
                        and '<hp:tbl' not in p and '<hp:pic' not in p):
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
    profile: FormatProfile = TYPER,
) -> Path:
    """
    1단 HWPX → 2단 양식 변환 (기본 타이퍼, profile로 수학비서 등 선택).

    Args:
        one_dan_path:   1단 파이프라인 출력 HWPX
        registry_key:   레지스트리 키 (exam_code/학교명 추출용)
        out_path:       출력 HWPX 경로
        ref_template:   header.xml 소스 override. None이면 profile.ref_template
        difficulty_map: {문제번호: 난이도문자열} — 빈 셀이면 '' (타이퍼 메타표용)
        school_name:    학교명 override (없으면 registry_key에서 추출)
        profile:        출력 양식 프로필 (TYPER | SUHBISEO)

    Returns:
        저장된 HWPX 경로
    """
    if ref_template is None:
        ref_template = profile.ref_template
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

    # 제목블록 양식(수학비서): 로고 등 BinData를 ref_template에서 가져온다(없으면 무시)
    if profile.title_block:
        try:
            with zipfile.ZipFile(ref_template, 'r') as zf:
                for name in zf.namelist():
                    if name.startswith('BinData/'):
                        bindata.setdefault(name, zf.read(name))
        except Exception:
            pass

    # 멱등 가드: 이미 2단 타이퍼 양식(2단 colCount 또는 1×6 메타표)이면
    # 이중 변환하지 않고 그대로 복사 — 본 변환이 2단을 내보내므로 필요.
    if 'colCount="2"' in one_dan_xml or 'rowCnt="1" colCnt="6"' in one_dan_xml:
        if Path(one_dan_path) != Path(out_path):
            shutil.copyfile(one_dan_path, out_path)
        return out_path

    # 2단 헤더 읽기 (스타일/폰트 정의)
    with zipfile.ZipFile(ref_template, 'r') as zf:
        header_xml = zf.read('Contents/header.xml')

    # 수학비서: 매 문제 앞 번호줄 스타일(charPr/paraPr/borderFill) 신규 ID로 주입
    meta_line_ids = None
    if profile.prob_meta_line:
        header_xml, meta_line_ids = _patch_header_for_meta_line(header_xml)

    # section0.xml 생성
    writer    = _TyprWriter(profile, meta_line_ids)
    sec_xml   = writer.build_section(one_dan_xml, school, exam_code, difficulty_map)
    sec_bytes = sec_xml.encode('utf-8')

    prob_count = writer.prob_count
    eq_count   = sec_xml.count('<hp:equation')
    para_count = sec_xml.count('<hp:p ')
    print(f'  [{profile.name}] 문제 {prob_count}건 / 수식 {eq_count}건 / 단락 {para_count}건')

    # ZIP 패키징
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix('.tmp.hwpx')

    with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zout:
        zout.writestr(zipfile.ZipInfo('mimetype'), 'application/hwp+zip')
        zout.writestr('version.xml',              _VERSION_XML)
        zout.writestr('Contents/header.xml',      header_xml)
        zout.writestr('Contents/masterpage0.xml', _masterpage_xml(profile.col_w))
        zout.writestr('Contents/section0.xml',    sec_bytes)
        zout.writestr('Contents/content.hpf',     _content_hpf_xml(list(bindata.keys()), profile.prv_title))
        zout.writestr('META-INF/container.xml',   _CONTAINER_XML)
        zout.writestr('META-INF/container.rdf',   _CONTAINER_RDF)
        zout.writestr('META-INF/manifest.xml',    _MANIFEST_XML)
        zout.writestr('settings.xml',             _SETTINGS_XML)
        zout.writestr('Preview/PrvText.txt',      profile.prv_title.encode('utf-8'))
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


def build_suhbiseo_hwpx(
    one_dan_path: Path,
    registry_key: str,
    out_path: Path,
    ref_template: Path | None = None,
    school_name: str = '',
) -> Path:
    """1단 HWPX → 2단 수학비서(학원) 양식 (B4 2단 명조, 메타표 없음)."""
    return build_typer_hwpx(
        one_dan_path, registry_key, out_path,
        ref_template=ref_template, school_name=school_name, profile=SUHBISEO,
    )


def build_by_format(
    one_dan_path: Path,
    registry_key: str,
    out_path: Path,
    fmt: str,
    **kwargs,
) -> Path:
    """양식명('타이퍼'|'수학비서')으로 2단 변환 디스패치."""
    profile = _PROFILES.get(fmt)
    if profile is None:
        raise ValueError(f"알 수 없는 양식: {fmt} (가능: {', '.join(_PROFILES)})")
    return build_typer_hwpx(one_dan_path, registry_key, out_path, profile=profile, **kwargs)
