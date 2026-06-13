"""프롬프트 교정 패턴 시스템 회귀 테스트 (무과금).

검증: 시드 멱등성, 스코프 필터링(global/school/subject),
패턴→프롬프트 주입 문자열, 시드 LaTeX의 HWP 변환 안전성.
API 호출 없음 — 순수 함수 + 파일 I/O.
"""
from __future__ import annotations

import re

import pytest

import scripts.web.corrections_log as cl
from scripts.web.corrections_log import (
    DEFAULT_SEED_PATTERNS,
    seed_default_patterns,
    get_active_patterns,
)
from src.ocr.claude_pdf_reader import _build_pattern_section
from src.common.latex_to_hwp import convert


@pytest.fixture()
def patt(tmp_path, monkeypatch):
    """패턴 파일을 임시 경로로 격리."""
    pf = tmp_path / "prompt_patterns.json"
    monkeypatch.setattr(cl, "_PATTERN_FILE", pf)
    monkeypatch.setattr(cl, "_LOG_DIR", tmp_path)
    return pf


class TestSeed:
    def test_seed_adds_all_then_idempotent(self, patt):
        n1 = seed_default_patterns()
        assert n1 == len(DEFAULT_SEED_PATTERNS) >= 3
        # 두 번째 호출은 멱등 — 중복 추가 0
        n2 = seed_default_patterns()
        assert n2 == 0
        active = get_active_patterns()
        assert len(active) == len(DEFAULT_SEED_PATTERNS)
        assert all(p["scope"] == "global" and p["active"] for p in active)

    def test_seed_patterns_have_stable_ids(self, patt):
        seed_default_patterns()
        ids = [p["id"] for p in cl._load_patterns()]
        assert ids == [sp["id"] for sp in DEFAULT_SEED_PATTERNS]
        assert len(set(ids)) == len(ids)  # 중복 없음


class TestScopeFilter:
    def _add(self, scope, value, orig, corr):
        cl.approve_as_pattern("c", scope, value, orig, corr, "")

    def test_global_school_subject(self, patt):
        self._add("global",  "",     "g_orig", "g_corr")
        self._add("school",  "경신여고", "s_orig", "s_corr")
        self._add("subject", "공수1",  "sub_orig", "sub_corr")

        # 스코프 없음 → global만
        only_g = get_active_patterns()
        assert {p["original_text"] for p in only_g} == {"g_orig"}

        # 학교만 → global + 해당 학교
        with_school = get_active_patterns(school="경신여고")
        assert {p["original_text"] for p in with_school} == {"g_orig", "s_orig"}

        # 학교 + 과목 → 셋 다
        full = get_active_patterns(school="경신여고", subject="공수1")
        assert {p["original_text"] for p in full} == {"g_orig", "s_orig", "sub_orig"}

        # 다른 학교 → 해당 학교 패턴 제외
        other = get_active_patterns(school="고려고")
        assert {p["original_text"] for p in other} == {"g_orig"}

    def test_inactive_excluded(self, patt):
        cl.approve_as_pattern("c", "global", "", "x^2", "x^{2}", "")
        pid = cl._load_patterns()[0]["id"]
        cl.toggle_pattern(pid, False)
        assert get_active_patterns() == []


class TestInjection:
    def test_pattern_section_contains_correction(self):
        patterns = [{
            "scope": "global", "scope_value": "",
            "original_text": "x^2", "corrected_text": "x^{2}", "note": "지수 중괄호",
        }]
        sec = _build_pattern_section(patterns)
        assert "[알려진 교정 사례 — 반드시 준수]" in sec
        assert "x^2" in sec          # ❌ 틀린 예
        assert "x^{2}" in sec        # ✅ 올바른 표현
        assert "지수 중괄호" in sec   # 메모
        assert "(전체 공통)" in sec   # global 스코프 표기

    def test_empty_patterns_no_section(self):
        assert _build_pattern_section([]) == ""

    def test_seeded_pattern_reaches_section(self, patt):
        seed_default_patterns()
        sec = _build_pattern_section(get_active_patterns())
        # 시드한 기하 표기가 프롬프트 문자열에 실제 등장
        assert r"\angle" in sec
        assert r"\overline" in sec
        assert r"\parallel" in sec


class TestSeedLatexSafe:
    """시드 패턴의 corrected_text가 HWP 변환에서 깨지지 않는지(미지원 명령 잔존 X)."""

    def _math_spans(self, text: str) -> list[str]:
        return re.findall(r"\$([^$]+)\$", text)

    def test_all_seed_latex_converts(self, patt):
        for sp in DEFAULT_SEED_PATTERNS:
            for latex in self._math_spans(sp["corrected_text"]):
                out = convert(latex)
                # 변환 후 원본 백슬래시 명령이 그대로 남아있으면 미지원 → 실패
                leftover = re.findall(r"\\[a-zA-Z]+", out)
                assert not leftover, (
                    f"미변환 LaTeX 명령 {leftover} (입력: {latex!r} → {out!r})"
                )

    def test_known_safe_commands(self):
        # 점검에서 안전 확인된 표기 — 백슬래시 명령 잔존 없어야
        for latex in [r"\angle ABC", r"\triangle ABC", r"\overline{AB}",
                      r"\vec{AB}", r"\parallel", r"\perp", r"\sim", r"\equiv"]:
            out = convert(latex)
            assert not re.findall(r"\\[a-zA-Z]+", out), f"{latex} → {out}"
