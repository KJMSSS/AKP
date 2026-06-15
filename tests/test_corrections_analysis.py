"""반복 교정 분석(analyze_corrections) 회귀 테스트."""
from __future__ import annotations

import json

import pytest

import scripts.web.corrections_log as cl


@pytest.fixture()
def logfile(tmp_path, monkeypatch):
    f = tmp_path / "corrections.jsonl"
    monkeypatch.setattr(cl, "_LOG_DIR", tmp_path)
    monkeypatch.setattr(cl, "_LOG_FILE", f)
    return f


def _add(note, prob, job="J1", corrected=""):
    cl.append_correction({
        "job_id": job, "problem_number": prob, "problem_text": "원본",
        "correction_note": note, "corrected_text": corrected, "pdf_name": "X.pdf",
    })


def test_themes_and_groups(logfile):
    _add("집합기호 없음", 3)
    _add("집합기호 없음", 4)          # 같은 메모 반복 → 그룹 count 2
    _add("루트 오인식", 13)
    a = cl.analyze_corrections(days=365)
    assert a["total"] == 3
    # 테마: 집합이 가장 많이
    themes = {t["keyword"]: t["count"] for t in a["themes"]}
    assert themes.get("집합") == 2 and themes.get("루트") == 1
    # 그룹: 집합기호 없음 2회가 맨 앞
    assert a["groups"][0]["note"] == "집합기호 없음"
    assert a["groups"][0]["count"] == 2
    assert sorted(o["problem_number"] for o in a["groups"][0]["occurrences"]) == [3, 4]


def test_normalized_grouping(logfile):
    _add("집합 기호  없음", 1)        # 공백 다름
    _add("집합 기호 없음", 2)
    a = cl.analyze_corrections(days=365)
    # 공백 정규화로 같은 그룹
    g = [g for g in a["groups"] if g["count"] == 2]
    assert len(g) == 1


def test_job_span(logfile):
    _add("집합기호 없음", 3, job="A")
    _add("집합기호 없음", 3, job="B")   # 다른 시험지 → job_span 2
    a = cl.analyze_corrections(days=365)
    assert a["groups"][0]["job_span"] == 2


def test_has_corrected_flag(logfile):
    _add("분수 틀림", 5, corrected="$\\dfrac{7}{27}$")
    a = cl.analyze_corrections(days=365)
    assert a["groups"][0]["has_corrected"] is True


def test_reverted_excluded(logfile):
    cid = cl.append_correction({
        "job_id": "J", "problem_number": 1, "problem_text": "x",
        "correction_note": "집합기호 없음", "corrected_text": "",
    })
    cl.revert_correction(cid)
    a = cl.analyze_corrections(days=365)
    assert a["total"] == 0 and a["groups"] == []


def test_empty(logfile):
    a = cl.analyze_corrections(days=365)
    assert a == {"themes": [], "groups": [], "total": 0}
