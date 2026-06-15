"""검수 마커 빨강 강조(highlight_markers) 회귀 테스트."""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from src.common.hwpx_marker_highlighter import highlight_markers

_HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head">'
    '<hh:refList><hh:charProperties itemCnt="1">'
    '<hh:charPr id="0" height="1000" textColor="#000000" shadeColor="none">'
    '<hh:fontRef hangul="0"/></hh:charPr>'
    '</hh:charProperties></hh:refList></hh:head>'
)


def _make(tmp_path: Path, runs: str) -> Path:
    section = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
        'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
        f'<hp:p id="1" paraPrIDRef="0">{runs}</hp:p></hs:sec>'
    )
    p = tmp_path / "t.hwpx"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("Contents/header.xml", _HEADER)
        zf.writestr("Contents/section0.xml", section)
    return p


def _read(p: Path):
    with zipfile.ZipFile(p) as zf:
        return (zf.read("Contents/section0.xml").decode("utf-8"),
                zf.read("Contents/header.xml").decode("utf-8"))


def test_marker_gets_red_run(tmp_path):
    p = _make(tmp_path, '<hp:run charPrIDRef="0"><hp:t>전체집합 【★ 확인 필요】의 부분집합</hp:t></hp:run>')
    n = highlight_markers(p)
    sec, hdr = _read(p)
    assert n == 1
    red = re.findall(r'<hh:charPr id="(\d+)"[^>]*textColor="#FF0000"', hdr)
    assert red, "빨강 charPr 추가돼야"
    rid = red[0]
    # 마커가 빨강 run에 분리됨 + 앞뒤 텍스트는 원래 색 유지
    assert f'<hp:run charPrIDRef="{rid}"><hp:t>【★ 확인 필요】</hp:t></hp:run>' in sec
    assert "전체집합 " in sec and "의 부분집합" in sec
    assert '【★' in sec  # 마커 텍스트는 보존
    # itemCnt 증가
    assert 'itemCnt="2"' in hdr


def test_marker_in_run_with_trailing_equation(tmp_path):
    # 마커 run 뒤에 수식이 붙어 있어도 깨지지 않게 분리
    p = _make(tmp_path,
        '<hp:run charPrIDRef="0"><hp:t>집합 【★ 확인 필요】 의 </hp:t>'
        '<hp:equation id="9"><hp:script>x</hp:script></hp:equation></hp:run>')
    n = highlight_markers(p)
    sec, hdr = _read(p)
    assert n == 1
    # 수식 보존
    assert "<hp:equation" in sec and "<hp:script>x</hp:script>" in sec
    rid = re.findall(r'<hh:charPr id="(\d+)"[^>]*textColor="#FF0000"', hdr)[0]
    assert f'charPrIDRef="{rid}"><hp:t>【★ 확인 필요】</hp:t>' in sec


def test_multiple_markers_one_line(tmp_path):
    p = _make(tmp_path, '<hp:run charPrIDRef="0"><hp:t>세 집합 【★ 확인 필요】, 【★ 확인 필요】 에 대하여</hp:t></hp:run>')
    n = highlight_markers(p)
    assert n == 2


def test_no_marker_no_change(tmp_path):
    p = _make(tmp_path, '<hp:run charPrIDRef="0"><hp:t>평범한 문장입니다</hp:t></hp:run>')
    before = _read(p)
    n = highlight_markers(p)
    assert n == 0
    assert _read(p) == before  # 마커 없으면 파일 미변경


def test_block_marker_highlighted(tmp_path):
    p = _make(tmp_path, '<hp:run charPrIDRef="0"><hp:t>【★ 본문 손상 — 원본 PDF의 5번 참조】</hp:t></hp:run>')
    n = highlight_markers(p)
    sec, hdr = _read(p)
    assert n == 1
    rid = re.findall(r'<hh:charPr id="(\d+)"[^>]*textColor="#FF0000"', hdr)[0]
    assert f'charPrIDRef="{rid}"' in sec
