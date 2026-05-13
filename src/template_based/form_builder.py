"""
양식(Form) HWPX 빌더

전략:
  - 기존 워드초벌에서 헤더(섹션 설정 + 상단 정보 영역)만 추출
  - 학교명·과목·연도·학년·학기·시험종류 자동 교체
  - 범위·지역은 비워두기 (사용자가 한글에서 직접 입력)
  - 본문은 문항 수만큼 빈 슬롯 + 배점만 채움
"""
import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape as _xe

# ── 파일명 파싱 ──────────────────────────────────────────────────────────
# 패턴: [2024_1_1_b_수상_문성고]  또는  [2025_2_1_a_확통_경신여고]
_FNAME_RE = re.compile(r'\[(\d{4})_(\d)_(\d)_([ab])_([^_\]]+)_([^\]]+)\]')

_SUBJECT_DISPLAY: dict[str, str] = {
    '수상': '수(상)', '수하': '수(하)',
    '수1': '수학I',  '수2': '수학II',
    '확통': '확통',  '기하': '기하',
    '미적분': '미적분',
    '공수1': '공통수학1', '공수2': '공통수학2',
}
# 식별자 문자열에서 과목 표기 (공수1 → 수1 등)
_SUBJECT_IDENT: dict[str, str] = {'공수1': '수1', '공수2': '수2'}
_TYPE_DISPLAY: dict[str, str] = {'a': '중간', 'b': '기말'}
# 과목 교체 대상이 되는 모든 텍스트 값
_ALL_SUBJECT_VALUES: frozenset[str] = frozenset(
    _SUBJECT_DISPLAY.values()) | frozenset(_SUBJECT_DISPLAY.keys()
)


def _school_full_name(short: str) -> str:
    """학교 약칭 → 전체 이름.
    경신여고 → 경신여자고등학교,  문성고 → 문성고등학교
    """
    if short.endswith('여고'):
        return short[:-2] + '여자고등학교'
    return short + '등학교'


def parse_filename(pdf_path: Path) -> dict[str, str]:
    """파일명에서 메타데이터 파싱."""
    m = _FNAME_RE.search(pdf_path.stem)
    if not m:
        raise ValueError(
            f"파일명 형식 불일치: {pdf_path.name}\n"
            f"  예: [2024_1_1_b_수상_문성고].pdf"
        )
    year, grade, semester, typ, subj_key, school = m.groups()
    ident_subj = _SUBJECT_IDENT.get(subj_key, subj_key)
    return {
        'year':            year,
        'grade':           grade,
        'semester':        semester,
        'type':            typ,
        'subject_key':     subj_key,
        'school_short':    school,
        'subject_display': _SUBJECT_DISPLAY.get(subj_key, subj_key),
        'type_display':    _TYPE_DISPLAY.get(typ, typ),
        'school_full':     _school_full_name(school),
        'year_info':       (f"{year}년 {grade}학년 {semester}학기 "
                            f"{_TYPE_DISPLAY.get(typ, typ)}"),
        'identifier':      f"{year}_{grade}_{semester}_{typ}_{ident_subj}",
    }


# ── 템플릿 선택 ──────────────────────────────────────────────────────────

def find_template(info: dict[str, str], samples_dir: Path) -> Path:
    """가장 적합한 워드초벌 템플릿 반환.

    우선순위:
      1) 학교 약칭 일치
      2) 과목 키워드 일치
      3) 아무 워드초벌
    """
    candidates = sorted(
        (f for f in samples_dir.glob('*워드초벌*.hwpx')
         if not f.name.endswith(']1.hwpx')),
        key=lambda p: p.name,
    )
    if not candidates:
        raise FileNotFoundError(f"워드초벌 파일이 없습니다: {samples_dir}")

    for f in candidates:
        if info['school_short'] in f.name:
            return f

    subj_kw = {info['subject_key'], info['subject_display'],
               _SUBJECT_IDENT.get(info['subject_key'], '')}
    for f in candidates:
        if any(kw and kw in f.name for kw in subj_kw):
            return f

    return candidates[0]


# ── XML 파싱 헬퍼 ─────────────────────────────────────────────────────────

_P_RE = re.compile(r'(<hp:p [^>]+>)(.*?)(</hp:p>)', re.DOTALL)
_T_RE = re.compile(r'(<hp:t[^>]*>)(.*?)(</hp:t>)', re.DOTALL)
_SCORE_TEXT_RE = re.compile(r'^\d+\.?\d*점$')


def _para_text(inner_xml: str) -> str:
    """단락 inner XML에서 모든 <hp:t> 텍스트 연결."""
    return ''.join(m.group(2) for m in _T_RE.finditer(inner_xml))


def _replace_first_t(inner_xml: str, new_text: str) -> str:
    """단락 inner XML에서 첫 <hp:t> 텍스트 교체."""
    done = False

    def _repl(m: re.Match) -> str:
        nonlocal done
        if not done:
            done = True
            return m.group(1) + _xe(new_text) + m.group(3)
        return m.group()

    return _T_RE.sub(_repl, inner_xml)


def _clear_all_t(inner_xml: str) -> str:
    """단락 inner XML의 모든 <hp:t> 텍스트를 비운다."""
    return _T_RE.sub(lambda m: m.group(1) + m.group(3), inner_xml)


# ── 헤더 추출 + 패치 ─────────────────────────────────────────────────────

def _extract_header_xml(section_xml: str) -> str:
    """첫 배점 단락 이전까지의 XML (XML 선언 + hs:sec 태그 포함)."""
    for m in _P_RE.finditer(section_xml):
        if _SCORE_TEXT_RE.match(_para_text(m.group(2)).strip()):
            return section_xml[:m.start()]
    return section_xml


def _patch_header(header_xml: str, info: dict[str, str]) -> str:
    """헤더 XML에서 자동 채울 필드 교체, 비워야 할 필드 비우기.

    자동 교체: 과목, 학교full, 연도+학년+학기+시험, 학교short, 식별자
    비우기:    지역(XX구), 범위(~ 포함)
    그 외:     원문 그대로
    """
    def process_para(m: re.Match) -> str:
        open_tag, inner, close_tag = m.groups()
        text = _para_text(inner).strip()

        # 과목 → 자동 교체
        if text in _ALL_SUBJECT_VALUES:
            return open_tag + _replace_first_t(inner, info['subject_display']) + close_tag

        # 학교 전체 이름 → 자동 교체 (e.g. "문성고등학교")
        if re.search(r'고등학교$', text):
            return open_tag + _replace_first_t(inner, info['school_full']) + close_tag

        # 연도+학년+학기+시험 → 자동 교체 (e.g. "2024년 1학년 1학기 중간")
        if re.match(r'\d{4}년 \d학년 \d학기', text):
            return open_tag + _replace_first_t(inner, info['year_info']) + close_tag

        # 학교 약칭 → 자동 교체 (e.g. "문성고", "경신여고")
        if re.match(r'^[가-힣]{2,6}(고|여고)$', text):
            return open_tag + _replace_first_t(inner, info['school_short']) + close_tag

        # 식별자 → 자동 교체 (e.g. "2024_1_1_a_수상")
        if re.match(r'^\d{4}_\d_\d_[ab]_', text):
            return open_tag + _replace_first_t(inner, info['identifier']) + close_tag

        # 지역 → 비우기 (e.g. "남구", "서구", "광산구")
        if re.match(r'^[가-힣]{1,3}구$', text):
            return open_tag + _clear_all_t(inner) + close_tag

        # 범위 → 비우기 (~ 포함 텍스트)
        if '~' in text:
            return open_tag + _clear_all_t(inner) + close_tag

        return m.group()

    return _P_RE.sub(process_para, header_xml)


# ── OCR 마크다운에서 배점 추출 ────────────────────────────────────────────

_SCORE_BRACKET_RE = re.compile(r'\[(\d+(?:\.\d+)?점)\]')


def extract_scores(md_text: str) -> list[str]:
    """마크다운에서 [X점] 패턴을 순서대로 추출."""
    return _SCORE_BRACKET_RE.findall(md_text)


# ── 본문 빈 슬롯 XML 생성 ────────────────────────────────────────────────

def _get_para_attrs(
    section_xml: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """section XML에서 빈 단락 / 배점 단락의 스타일 속성 추출."""
    blank_attrs: dict[str, str] = {'ppr': '5', 'sty': '1', 'cpr': '8'}
    score_attrs: dict[str, str] = {'ppr': '6', 'sty': '3', 'cpr': '16'}

    paras = list(_P_RE.finditer(section_xml))
    for i, m in enumerate(paras):
        if not _SCORE_TEXT_RE.match(_para_text(m.group(2)).strip()):
            continue

        # 배점 단락 속성
        ppr = re.search(r'paraPrIDRef="(\d+)"', m.group(1))
        sty = re.search(r'styleIDRef="(\d+)"',  m.group(1))
        run = re.search(r'charPrIDRef="(\d+)"',  m.group(2))
        if ppr: score_attrs['ppr'] = ppr.group(1)
        if sty: score_attrs['sty'] = sty.group(1)
        if run: score_attrs['cpr'] = run.group(1)

        # 배점 바로 앞 빈 단락에서 blank 속성
        for j in range(i - 1, max(0, i - 6), -1):
            if _para_text(paras[j].group(2)).strip():
                continue
            ppr2 = re.search(r'paraPrIDRef="(\d+)"', paras[j].group(1))
            sty2 = re.search(r'styleIDRef="(\d+)"',  paras[j].group(1))
            run2 = re.search(r'charPrIDRef="(\d+)"',  paras[j].group(2))
            if ppr2: blank_attrs['ppr'] = ppr2.group(1)
            if sty2: blank_attrs['sty'] = sty2.group(1)
            if run2: blank_attrs['cpr'] = run2.group(1)
            break
        break

    return blank_attrs, score_attrs


def _make_body_xml(
    scores: list[str],
    blank_attrs: dict[str, str],
    score_attrs: dict[str, str],
    blanks_per_q: int = 8,
) -> str:
    """문항 수만큼 빈 슬롯 + 배점 단락 XML 생성."""
    blank_tmpl = (
        '<hp:p id="0" paraPrIDRef="{ppr}" styleIDRef="{sty}" '
        'pageBreak="0" columnBreak="0" merged="0">'
        '<hp:run charPrIDRef="{cpr}"/>'
        '<hp:linesegarray/>'
        '</hp:p>'
    ).format(**blank_attrs)

    score_tmpl = (
        '<hp:p id="0" paraPrIDRef="{ppr}" styleIDRef="{sty}" '
        'pageBreak="0" columnBreak="0" merged="0">'
        '<hp:run charPrIDRef="{cpr}"><hp:t>{{score}}</hp:t></hp:run>'
        '<hp:linesegarray/>'
        '</hp:p>'
    ).format(**score_attrs)

    parts: list[str] = []
    for score in scores:
        for _ in range(blanks_per_q):
            parts.append(blank_tmpl)
        parts.append(score_tmpl.replace('{score}', _xe(score)))

    return ''.join(parts)


# ── 공개 API ─────────────────────────────────────────────────────────────

def build_form(
    pdf_path: Path,
    samples_dir: Path,
    output_path: Path,
    *,
    md_text: str | None = None,
) -> dict:
    """양식 HWPX 생성.

    Args:
        pdf_path:    PDF 파일 경로 (파일명에서 메타데이터 파싱)
        samples_dir: 워드초벌 템플릿이 있는 디렉터리
        output_path: 저장 경로 (*.hwpx)
        md_text:     Mathpix OCR 마크다운 (None → 22문항 빈 슬롯)

    Returns:
        {'template', 'output', 'n_questions', 'scores', 'info'}
    """
    info     = parse_filename(pdf_path)
    template = find_template(info, samples_dir)

    with zipfile.ZipFile(template, 'r') as zf:
        files = {name: zf.read(name) for name in zf.namelist()}

    section_xml = files['Contents/section0.xml'].decode('utf-8')

    blank_attrs, score_attrs = _get_para_attrs(section_xml)
    header_xml     = _extract_header_xml(section_xml)
    patched_header = _patch_header(header_xml, info)

    scores = extract_scores(md_text) if md_text else []
    if not scores:
        scores = [''] * 22

    body_xml    = _make_body_xml(scores, blank_attrs, score_attrs)
    new_section = patched_header + body_xml + '</hs:sec>'

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zout:
        for name, data in files.items():
            if name == 'Contents/section0.xml':
                zout.writestr(name, new_section.encode('utf-8'))
            else:
                zout.writestr(name, data)

    return {
        'template':    template,
        'output':      output_path,
        'n_questions': len(scores),
        'scores':      scores,
        'info':        info,
    }
