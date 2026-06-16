"""골드셋 비교 하네스 순수 함수 회귀 테스트 (무과금·자족적).

extract_text / normalize / similarity / doc_metrics 의 동작 고정.
(실제 골드셋은 git 미추적 로컬 전용이라 합성 HWPX로 검증)
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from scripts.gold_compare import extract_text, normalize, similarity, doc_metrics


def _make(tmp_path: Path, section_body: str) -> Path:
    section = (
        '<?xml version="1.0"?>'
        '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
        'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
        + section_body + "</hs:sec>"
    )
    p = tmp_path / "t.hwpx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("Contents/section0.xml", section)
    return p


def test_extract_text_and_equation(tmp_path):
    body = ('<hp:p><hp:run><hp:t>전체집합 </hp:t>'
            '<hp:equation><hp:script>U</hp:script></hp:equation>'
            '<hp:t>의 부분집합</hp:t></hp:run></hp:p>')
    p = _make(tmp_path, body)
    t = extract_text(p)
    assert "전체집합" in t and "의 부분집합" in t
    assert "$U$" in t  # 수식 스크립트는 $…$로


def test_normalize_strips_boilerplate_and_base64():
    raw = ("이 자료의 2차 저작권은 광주 전남 타이퍼에 있습니다."
           "공통수학1 문제내용 GtOyEbcnDZHBSidyzABCDEFGHIJKLMNOPQRSTUV 끝")
    n = normalize(raw)
    assert "저작권" not in n          # 보일러플레이트 제거
    assert "공통수학1" not in n
    assert "GtOyE" not in n            # base64 잡음 제거
    assert "문제내용" in n and "끝" in n
    assert " " not in n               # 공백 제거


def test_similarity_bounds(tmp_path):
    assert similarity("같은텍스트", "같은텍스트") == 1.0
    assert similarity("", "") == 1.0
    assert similarity("abc", "") == 0.0
    assert 0.0 <= similarity("문제1번", "문제2번") < 1.0


def test_doc_metrics(tmp_path):
    body = ('<hp:p><hp:run><hp:t>1. 문제 【★ 확인 필요】</hp:t>'
            '<hp:equation><hp:script>x+1</hp:script></hp:equation></hp:run></hp:p>'
            '<hp:p><hp:run><hp:equation><hp:script>y</hp:script></hp:equation></hp:run></hp:p>')
    p = _make(tmp_path, body)
    m = doc_metrics(p)
    assert m["equations"] == 2
    assert m["markers"] == 1
    assert m["chars"] > 0
    assert "_norm" in m


def test_self_similarity_full(tmp_path):
    body = '<hp:p><hp:run><hp:t>가나다라 마바사</hp:t></hp:run></hp:p>'
    p = _make(tmp_path, body)
    m = doc_metrics(p)
    assert similarity(m["_norm"], m["_norm"]) == 1.0
