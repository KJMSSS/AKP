"""form_builder.py 한글 호환성 테스트.

검증 항목:
  - mimetype STORED 강제 (HWPX/ODF 스펙 필수)
  - ZIP 파일 수 보존
  - BinData 압축 방식 원본과 동일
  - 모든 XML/hpf 파일 유효성 (ET.fromstring)
  - 배점 단락 개수 일치
"""
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from src.template_based.form_builder import build_form

# ── 최소 section0.xml ────────────────────────────────────────────────────
_MINIMAL_SECTION = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
    '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"'
    ' xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
    # 헤더 단락 1 — 학교명 (비배점)
    '<hp:p id="0" paraPrIDRef="5" styleIDRef="1" pageBreak="0"'
    ' columnBreak="0" merged="0">'
    '<hp:run charPrIDRef="8"><hp:t>문성고등학교</hp:t></hp:run>'
    '<hp:linesegarray/></hp:p>'
    # 헤더 단락 2 — 연도/학년/학기 (비배점)
    '<hp:p id="1" paraPrIDRef="5" styleIDRef="1" pageBreak="0"'
    ' columnBreak="0" merged="0">'
    '<hp:run charPrIDRef="8"><hp:t>2024년 1학년 1학기 기말</hp:t></hp:run>'
    '<hp:linesegarray/></hp:p>'
    # 배점 단락 — 여기서부터 본문
    '<hp:p id="2" paraPrIDRef="6" styleIDRef="3" pageBreak="0"'
    ' columnBreak="0" merged="0">'
    '<hp:run charPrIDRef="16"><hp:t>3.0점</hp:t></hp:run>'
    '<hp:linesegarray/></hp:p>'
    '</hs:sec>'
)

# 배점 단락이 테이블 셀 안에 있는 section0.xml (버그 2 재현 구조)
_NESTED_SCORE_SECTION = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
    '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"'
    ' xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
    # 헤더 외부 단락 (테이블 포함, 배점 없음)
    '<hp:p id="0" paraPrIDRef="5" styleIDRef="1" pageBreak="0"'
    ' columnBreak="0" merged="0">'
    '<hp:run charPrIDRef="8">'
    '<hp:tbl>'
    '<hp:tr><hp:tc><hp:subList>'
    '<hp:p id="10" paraPrIDRef="5" styleIDRef="1" pageBreak="0"'
    ' columnBreak="0" merged="0">'
    '<hp:run charPrIDRef="8"><hp:t>문성고등학교</hp:t></hp:run>'
    '<hp:linesegarray/></hp:p>'
    '</hp:subList></hp:tc></hp:tr>'
    '</hp:tbl>'
    '<hp:t/></hp:run>'
    '<hp:linesegarray/></hp:p>'
    # 첫 번째 본문 외부 단락 (배점 테이블 포함) — 여기서 잘라야 함
    '<hp:p id="1" paraPrIDRef="5" styleIDRef="1" pageBreak="0"'
    ' columnBreak="0" merged="0">'
    '<hp:run charPrIDRef="8">'
    '<hp:tbl>'
    '<hp:tr><hp:tc><hp:subList>'
    '<hp:p id="11" paraPrIDRef="6" styleIDRef="3" pageBreak="0"'
    ' columnBreak="0" merged="0">'
    '<hp:run charPrIDRef="16"><hp:t>4.5점</hp:t></hp:run>'
    '<hp:linesegarray/></hp:p>'
    '</hp:subList></hp:tc></hp:tr>'
    '</hp:tbl>'
    '<hp:t/></hp:run>'
    '<hp:linesegarray/></hp:p>'
    '</hs:sec>'
)


def _make_template(tmp_path: Path, filename: str, section: str) -> Path:
    """미니멀 HWPX 템플릿 생성 — 여러 compress_type 혼용."""
    path = tmp_path / filename
    with zipfile.ZipFile(path, 'w') as zf:
        # mimetype: STORED (올바른 방식)
        info = zipfile.ZipInfo('mimetype')
        info.compress_type = zipfile.ZIP_STORED
        zf.writestr(info, 'application/hwp+zip')
        # section0.xml: DEFLATED
        zf.writestr('Contents/section0.xml',
                    section.encode('utf-8'),
                    compress_type=zipfile.ZIP_DEFLATED)
        # 이미지: STORED (원본 보존 테스트)
        img = zipfile.ZipInfo('BinData/image1.png')
        img.compress_type = zipfile.ZIP_STORED
        zf.writestr(img, b'\x89PNG\r\n\x1a\n')
    return path


def _build(tmp_path: Path, section: str, md: str) -> tuple[Path, Path]:
    """템플릿 생성 + build_form() 실행 → (template, output) 반환."""
    tmpl = _make_template(tmp_path, '[2024_1_1_b_수상_문성고][워드초벌].hwpx', section)
    pdf  = tmp_path / '[2024_1_1_b_수상_문성고].pdf'
    out  = tmp_path / 'form_output.hwpx'
    build_form(pdf, tmp_path, out, md_text=md)
    return tmpl, out


# ════════════════════════════════════════════════════════════════
# 1. mimetype 압축 방식
# ════════════════════════════════════════════════════════════════

class TestMimetypeCompression:

    def test_mimetype_stored(self, tmp_path):
        """mimetype 파일이 STORED(비압축)여야 함."""
        _, out = _build(tmp_path, _MINIMAL_SECTION, '[4.5점]')
        with zipfile.ZipFile(out) as zf:
            for info in zf.infolist():
                if info.filename == 'mimetype':
                    assert info.compress_type == zipfile.ZIP_STORED, (
                        f"mimetype compress_type={info.compress_type}, "
                        "STORED(0) 이어야 함"
                    )
                    return
        pytest.fail('mimetype 엔트리 없음')

    def test_mimetype_content_exact(self, tmp_path):
        """mimetype 내용이 'application/hwp+zip' 정확히 일치."""
        _, out = _build(tmp_path, _MINIMAL_SECTION, '[4.5점]')
        with zipfile.ZipFile(out) as zf:
            content = zf.read('mimetype').decode('ascii')
        assert content == 'application/hwp+zip'

    def test_mimetype_is_first_entry(self, tmp_path):
        """mimetype이 ZIP의 첫 번째 엔트리여야 함."""
        _, out = _build(tmp_path, _MINIMAL_SECTION, '[4.5점]')
        with zipfile.ZipFile(out) as zf:
            assert zf.namelist()[0] == 'mimetype'


# ════════════════════════════════════════════════════════════════
# 2. ZIP 구조 보존
# ════════════════════════════════════════════════════════════════

class TestZipStructure:

    def test_file_count_preserved(self, tmp_path):
        """출력 ZIP 파일 수 = 원본 템플릿과 동일."""
        tmpl, out = _build(tmp_path, _MINIMAL_SECTION, '[4.5점]')
        with zipfile.ZipFile(tmpl) as zt, zipfile.ZipFile(out) as zo:
            assert len(zo.namelist()) == len(zt.namelist())

    def test_bindata_compression_preserved(self, tmp_path):
        """BinData/ 이미지 파일의 compress_type이 원본과 동일."""
        tmpl, out = _build(tmp_path, _MINIMAL_SECTION, '[4.5점]')
        with zipfile.ZipFile(tmpl) as zt, zipfile.ZipFile(out) as zo:
            tmpl_ct = {i.filename: i.compress_type for i in zt.infolist()}
            out_ct  = {i.filename: i.compress_type for i in zo.infolist()}
        for name, ct in tmpl_ct.items():
            if name.startswith('BinData/') and name in out_ct:
                assert out_ct[name] == ct, (
                    f'{name}: 원본 compress_type={ct}, '
                    f'출력 compress_type={out_ct[name]}'
                )


# ════════════════════════════════════════════════════════════════
# 3. XML 유효성
# ════════════════════════════════════════════════════════════════

class TestXmlValidity:

    def test_section0_xml_valid(self, tmp_path):
        """section0.xml이 파싱 가능한 유효 XML이어야 함."""
        _, out = _build(tmp_path, _MINIMAL_SECTION, '[4.5점] [5점]')
        with zipfile.ZipFile(out) as zf:
            data = zf.read('Contents/section0.xml').decode('utf-8')
        try:
            ET.fromstring(data.lstrip('﻿'))
        except ET.ParseError as e:
            pytest.fail(f'section0.xml XML 파싱 오류: {e}')

    def test_nested_score_section0_valid(self, tmp_path):
        """테이블 안 배점 구조(버그2 재현)에서도 XML 유효해야 함."""
        _, out = _build(tmp_path, _NESTED_SCORE_SECTION, '[4.5점] [5점]')
        with zipfile.ZipFile(out) as zf:
            data = zf.read('Contents/section0.xml').decode('utf-8')
        try:
            ET.fromstring(data.lstrip('﻿'))
        except ET.ParseError as e:
            pytest.fail(f'nested 구조 section0.xml 오류: {e}')

    def test_section0_no_unclosed_tags(self, tmp_path):
        """section0.xml이 hs:sec로 올바르게 닫혀야 함."""
        _, out = _build(tmp_path, _NESTED_SCORE_SECTION, '[3점]')
        with zipfile.ZipFile(out) as zf:
            data = zf.read('Contents/section0.xml').decode('utf-8')
        assert data.rstrip().endswith('</hs:sec>'), (
            '마지막 태그가 </hs:sec>가 아님'
        )


# ════════════════════════════════════════════════════════════════
# 4. 배점 단락 생성
# ════════════════════════════════════════════════════════════════

class TestScoreParagraphs:

    def test_score_count_matches_md(self, tmp_path):
        """md_text의 배점 수만큼 배점 단락이 생성됨."""
        import re
        _, out = _build(tmp_path, _MINIMAL_SECTION, '[3점] [4점] [5점]')
        with zipfile.ZipFile(out) as zf:
            data = zf.read('Contents/section0.xml').decode('utf-8')
        scores = re.findall(r'<hp:t>\d+\.?\d*점</hp:t>', data)
        assert len(scores) == 3

    def test_nested_header_not_in_body(self, tmp_path):
        """테이블 내 헤더 배점(4.5점)이 본문 배점으로 복사되지 않음."""
        import re
        _, out = _build(tmp_path, _NESTED_SCORE_SECTION, '[3점]')
        with zipfile.ZipFile(out) as zf:
            data = zf.read('Contents/section0.xml').decode('utf-8')
        # 본문에서 생성된 배점은 [3점] 하나뿐이어야 함
        scores = re.findall(r'<hp:t>(\d+\.?\d*점)</hp:t>', data)
        assert scores == ['3점'], f'예상 [3점], 실제 {scores}'


# ════════════════════════════════════════════════════════════════
# 5. 실제 샘플 파일 통합 테스트 (파일 존재 시만 실행)
# ════════════════════════════════════════════════════════════════

_SAMPLES = Path('samples')

@pytest.mark.parametrize('pdf_name,md_name', [
    (
        '[2024_1_1_b_수상_문성고].pdf',
        'output_수상_문성고_2024_1_1.mathpix.md',
    ),
    (
        '[2025_2_1_a_확통_경신여고].pdf',
        'output_확통_경신여고_2025_2_1.mathpix.md',
    ),
    (
        '[2024_2_1_a_수1_인성고][지수 ~ 삼각함수 그래프].pdf',
        'output_수1_인성고_2024_2_1.mathpix.md',
    ),
])
def test_real_sample_hwpx_compatibility(pdf_name, md_name, tmp_path):
    """실제 샘플 build_form() — mimetype STORED + 전체 XML 유효성."""
    pdf_path = _SAMPLES / pdf_name
    md_path  = _SAMPLES / md_name
    if not pdf_path.exists() or not md_path.exists():
        pytest.skip(f'샘플 없음: {pdf_name}')

    out = tmp_path / 'form_real.hwpx'
    build_form(pdf_path, _SAMPLES, out,
               md_text=md_path.read_text(encoding='utf-8'))

    with zipfile.ZipFile(out) as zf:
        # mimetype STORED
        for info in zf.infolist():
            if info.filename == 'mimetype':
                assert info.compress_type == zipfile.ZIP_STORED, \
                    'mimetype DEFLATED — 한글이 파일 손상으로 거부함'

        # 모든 XML/hpf 유효성
        for name in zf.namelist():
            if name.endswith('.xml') or name.endswith('.hpf'):
                data = zf.read(name).decode('utf-8')
                try:
                    ET.fromstring(data.lstrip('﻿'))
                except ET.ParseError as e:
                    pytest.fail(f'{pdf_name} → {name}: {e}')
