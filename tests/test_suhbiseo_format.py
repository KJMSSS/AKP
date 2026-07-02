"""출력 양식 프로필(타이퍼/수학비서) 회귀+신규 검증 (무네트워크).

타이퍼  = A3(84188) 2단 + 문제별 1×6 메타표.
수학비서 = B4(72852) 2단 + 메타표 없음 (서울세종고 명조 header).
둘 다 git추적 template.hwpx / suhbiseo_template.hwpx 로 빌드 → 워크트리에서 실행 가능.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest

from src.text_only.text_builder import build_from_markdown
from src.text_only.typer_builder import (
    build_typer_hwpx, build_suhbiseo_hwpx, build_by_format,
    _REF_TYPER, _REF_SUHBISEO,
    _extract_region, _extract_school, _extract_exam_code,
)

pytestmark = pytest.mark.skipif(
    not _REF_TYPER.exists() or not _REF_SUHBISEO.exists(),
    reason="template.hwpx / suhbiseo_template.hwpx 없음",
)

_MD = "\n".join([
    "1. 두 다항식의 합을 구하면? [4점]",
    "① 1", "② 2", "③ 3", "④ 4", "⑤ 5",
    "",
    "2. 다음을 계산하시오. [3점]",
    "① 1", "② 2", "③ 3", "④ 4", "⑤ 5",
])


def _section(path: Path) -> str:
    return zipfile.ZipFile(path).read("Contents/section0.xml").decode("utf-8")


def _page_w(xml: str) -> int:
    m = re.search(r'<hp:pagePr[^>]*width="(\d+)"', xml)
    return int(m.group(1)) if m else 0


def _one_dan(tmp_path: Path, base: Path) -> Path:
    one = tmp_path / "one.hwpx"
    build_from_markdown(_MD, one, base)
    xml = _section(one)
    assert 'colCount="1"' in xml, "1단 빌드는 colCount=1 이어야"
    return one


def test_typer_unchanged(tmp_path):
    one = _one_dan(tmp_path, _REF_TYPER)
    two = tmp_path / "typer.hwpx"
    build_typer_hwpx(one, "2026_1_1_a_공수1_테스트고", two)
    xml = _section(two)
    assert 'colCount="2"' in xml                 # 2단
    assert _page_w(xml) == 84188                 # A3
    assert 'rowCnt="1" colCnt="6"' in xml        # 메타표 있음(타이퍼)


def test_suhbiseo_b4_no_meta(tmp_path):
    one = _one_dan(tmp_path, _REF_SUHBISEO)
    two = tmp_path / "suhbiseo.hwpx"
    build_suhbiseo_hwpx(one, "2026_1_1_a_공수1_테스트고", two)
    xml = _section(two)
    assert 'colCount="2"' in xml                 # 2단
    assert _page_w(xml) == 72852                 # B4
    assert 'rowCnt="1" colCnt="6"' not in xml    # 메타표 없음(수학비서)
    # 수학비서는 스타일 재매핑 안 함 → 본문 단락이 1단 스타일(styleIDRef=0) 유지
    assert 'styleIDRef="0"' in xml


def test_suhbiseo_gold_body(tmp_path):
    """골드형 본문: 번호/배점 텍스트 제거·자동번호 paraPr6·선택지 가로 패킹·풀이공간.

    근거 = 실제 업로드 골드 (강남)[2024_1_1_a_수상_서울세종고.hwpx 실측:
    번호 텍스트 없음(paraPr6 heading NUMBER가 자동 렌더), '점]' 0회,
    선택지 ①~⑤는 탭으로 2~3개씩 한 단락, 문제 뒤 빈 단락 다수.
    """
    one = _one_dan(tmp_path, _REF_SUHBISEO)
    two = tmp_path / "suhbiseo_gold.hwpx"
    build_suhbiseo_hwpx(one, "2026_1_1_a_공수1_테스트고", two)
    xml = _section(two)

    # (a) 배점 표기 제거
    assert '점]' not in xml
    # (b) 인라인 번호 제거 (원문 "1. 두 다항식…" / "2. 다음을…")
    assert '1. 두' not in xml and '2. 다' not in xml
    assert '두 다항식' in xml                     # 본문 자체는 보존
    # (c) 문제 서두 = paraPr6 (자동 문단번호) — 문제 수(2)와 일치
    assert xml.count('paraPrIDRef="6"') == 2
    # (d) 선택지 가로 패킹 — ①과 ②가 같은 단락(탭 구분)
    packed = [p for p in re.findall(r'<hp:p\b.*?</hp:p>', xml, re.S)
              if '①' in p and '②' in p]
    assert packed and '<hp:tab ' in packed[0]
    # (e) 풀이공간 빈 단락 (문제 2개 × 10)
    assert xml.count('<hp:run charPrIDRef="0"/>') >= 20
    # (f) 본문 90% 줄간격 paraPr8 잔재 없음 (제목블록 밖)
    body = xml.split('</hp:tbl>')[-1]            # 제목블록 표 이후 본문
    assert 'paraPrIDRef="8"' not in body


def test_region_extraction():
    """파일명 앞 (광주)/(강남) → 지역 태그. 지저분한 stem에서도 코드/학교 견고 추출."""
    # 광주 학교 (범위 태그까지 붙은 실제 파일명 stem)
    k = "(광주)[2024_1_1_a_수상_고려고][다항식의 연산 ~ 여러 가지 방정식]"
    assert _extract_region(k) == "광주"
    assert _extract_school(k) == "고려고"
    assert _extract_exam_code(k) == "2024_1_1_a_수상"
    # 강남 학교
    k2 = "(강남)[2024_1_1_a_수상_서울세종고"
    assert _extract_region(k2) == "강남"
    assert _extract_school(k2) == "서울세종고"
    # 지역 없는 깨끗한 키 → 지역 '' (하위호환)
    assert _extract_region("2026_1_1_a_공수1_테스트고") == ""
    assert _extract_school("2026_1_1_a_공수1_테스트고") == "테스트고"
    assert _extract_exam_code("2026_1_1_a_공수1_테스트고") == "2026_1_1_a_공수1"


def test_suhbiseo_title_region(tmp_path):
    """수학비서 제목줄 = canonical 기본명 '(지역)[코드_학교]' 그대로 (구버전 '기출' 확장 폐기)."""
    one = _one_dan(tmp_path, _REF_SUHBISEO)
    # 실제 광주 파일명 stem(범위 태그까지)을 registry_key로 전달 → 범위는 제외한 기본명만
    two = tmp_path / "suhbiseo_region.hwpx"
    build_suhbiseo_hwpx(one, "(광주)[2024_1_1_a_수상_고려고][다항식~방정식]", two)
    xml = _section(two)
    assert "(광주)[2024_1_1_a_수상_고려고]" in xml
    assert "기출" not in xml                              # 구버전 확장형 제거 확인
    # 강남
    three = tmp_path / "suhbiseo_gangnam.hwpx"
    build_suhbiseo_hwpx(one, "(강남)[2024_1_1_a_수상_서울세종고]", three)
    assert "(강남)[2024_1_1_a_수상_서울세종고]" in _section(three)


def test_build_by_format_dispatch(tmp_path):
    one = _one_dan(tmp_path, _REF_SUHBISEO)
    out = build_by_format(one, "x_테스트고", tmp_path / "f.hwpx", "수학비서")
    assert _page_w(_section(out)) == 72852
    with pytest.raises(ValueError):
        build_by_format(one, "x", tmp_path / "g.hwpx", "없는양식")
