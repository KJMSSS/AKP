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


# ── _diff_fragments: 검수 전→후 최소 변경 토막 ─────────────────────────────

def test_diff_fragments_replacement():
    """치환: 'abc XXX def' → 'abc YYY def' → 토막 XXX→YYY."""
    frags = cl._diff_fragments("abc XXX def", "abc YYY def")
    assert any(f["old"] == "XXX" and f["new"] == "YYY" for f in frags), frags


def test_diff_fragments_deletion():
    """삭제: 'A f'(x) B' → 'A B' → 토막 f'(x)→(빈)."""
    frags = cl._diff_fragments("A f'(x) B", "A B")
    assert any("f'(x)" in f["old"] and f["new"] == "" for f in frags), frags


def test_diff_fragments_no_change_and_none():
    """무변경(공백차만)·None 입력 → 토막 없음."""
    assert cl._diff_fragments("a  b", "a b") == []
    assert cl._diff_fragments("같은 내용", "같은 내용") == []
    assert cl._diff_fragments(None, "x") == []
    assert cl._diff_fragments("x", None) == []


# ── 메모 없는 자동 기록(검수 전→후) → 내용 diff 로 묶임 ─────────────────────

def _add_edit(before, after, prob, job="J1"):
    """검수 보기 제출이 만드는 자동 기록 모사 — 메모 없음, 검수 전/후만."""
    cl.append_correction({
        "job_id": job, "problem_number": prob, "problem_text": before,
        "correction_note": "", "corrected_text": after, "pdf_name": "X.pdf",
        "source": "review-edit",
    })


def test_memoless_grouped_by_content_diff(logfile):
    """메모 없이 같은 f'(x) 삭제가 2개 시험지에서 → 내용 diff 로 반복 그룹."""
    _add_edit("값 f'(x) 끝", "값 끝", prob=1, job="A")
    _add_edit("답 f'(x) 끝", "답 끝", prob=2, job="B")
    a = cl.analyze_corrections(days=365)
    assert a["total"] == 2
    hot = [g for g in a["groups"] if g["count"] == 2]
    assert hot, a["groups"]
    g = hot[0]
    assert "f'(x)" in g["note"]                 # 내용 변화가 그룹 라벨로
    assert g["job_span"] == 2                    # 두 시험지에 걸침 → 체계적
    # occurrence 에 최소 토막이 실려 패턴 등록이 깔끔
    assert g["occurrences"][0]["problem_text"] == "f'(x)"
    assert g["occurrences"][0]["corrected_text"] == ""


def test_memo_and_diff_coexist(logfile):
    """메모 있는 항목은 메모로, 메모 없는 항목은 내용으로 — 한 분석에 공존."""
    _add("집합기호 없음", 3)                      # 메모 그룹
    _add_edit("p DEL q", "p q", prob=5, job="A")  # 내용 diff 그룹
    a = cl.analyze_corrections(days=365)
    notes = [g["note"] for g in a["groups"]]
    assert "집합기호 없음" in notes
    assert any("DEL" in n for n in notes)
