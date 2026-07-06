"""<보기> 상자 라벨 오독 교정 테스트 (backend/pipeline/vision_claude).

2026-07-06 실사고: opus 가 원문자 ㉣→㉤, ㉤→㉢ 으로 오독해 빌드본에 ㉠㉡㉢㉤㉢ 이
찍혔다. 라벨은 사전순 연속이 절대 관례 — 위치 기반 강제 교정을 검증한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from pipeline.vision_claude import (  # noqa: E402
    _fix_bogi_jamo,
    _fix_boki_stem_labels,
)


def _prob(stem, choices=None):
    return [{"number": "1", "stem": stem, "choices": choices or []}]


# ── 원문자 라벨 (2026-07-06 실사고 케이스) ──────────────────────────

def test_circled_misread_4th_5th():
    stem = "<보기> ㉠ y=x  ㉡ y=-2x  ㉢ y=-3x  ㉤ (2,1)  ㉢ (0,0)  중 옳은 것은?"
    fixed = _fix_boki_stem_labels(_prob(stem))[0]["stem"]
    assert "㉣ (2,1)" in fixed and "㉤ (0,0)" in fixed
    assert fixed.count("㉢") == 1   # 중복 제거됨


def test_circled_correct_is_noop():
    stem = "<보기> ㉠ 가  ㉡ 나  ㉢ 다  ㉣ 라"
    assert _fix_boki_stem_labels(_prob(stem))[0]["stem"] == stem


def test_no_header_no_touch():
    # <보기> 헤더가 없으면 원문자가 있어도 건드리지 않는다 (본문 참조 보호)
    stem = "㉠ 과 ㉢ 을 비교하면?"
    assert _fix_boki_stem_labels(_prob(stem))[0]["stem"] == stem


def test_first_label_wrong_no_touch():
    # 첫 라벨이 ㉠ 이 아니면 보수적으로 skip (라벨 아닌 원문자 사용일 수 있음)
    stem = "<보기> ㉢ 하나  ㉣ 둘"
    assert _fix_boki_stem_labels(_prob(stem))[0]["stem"] == stem


def test_single_label_no_touch():
    stem = "<보기> ㉠ 하나뿐"
    assert _fix_boki_stem_labels(_prob(stem))[0]["stem"] == stem


# ── 자모 라벨 ('ㄱ.' 꼴 — 구두점 필수) ──────────────────────────────

def test_jamo_misread_4th():
    stem = "<보기> ㄱ. x=1  ㄴ. x=2  ㄷ. x=3  ㅎ. x=4  를 보고 고르시오"
    fixed = _fix_boki_stem_labels(_prob(stem))[0]["stem"]
    assert "ㄹ. x=4" in fixed and "ㅎ." not in fixed


def test_jamo_reference_without_punct_untouched():
    # 항목 본문의 역참조('ㄱ과 ㄴ')는 구두점이 없어 라벨로 안 잡힌다
    stem = "<보기> ㄱ. A다  ㄴ. B다  ㄷ. ㄱ과 ㄴ이 모두 참이다"
    fixed = _fix_boki_stem_labels(_prob(stem))[0]["stem"]
    assert "ㄷ. ㄱ과 ㄴ이" in fixed


# ── choices 원문자 조합 교차검증 ────────────────────────────────────

def test_circled_choices_m_to_r():
    # 상자 라벨이 ㉠~㉣ 4개뿐인데 choices 에 ㉤ → ㉣ 오독으로 보고 교정
    p = _prob("<보기> ㉠ 가 ㉡ 나 ㉢ 다 ㉣ 라",
              ["㉠, ㉡", "㉠, ㉤", "㉡, ㉢", "㉢, ㉣", "㉠, ㉢, ㉣"])
    fixed = _fix_bogi_jamo(_fix_boki_stem_labels(p))[0]["choices"]
    assert fixed[1] == "㉠, ㉣"


def test_circled_choices_valid_m_kept():
    # 상자에 ㉤ 이 실제로 있으면(라벨 5개) choices 의 ㉤ 은 그대로
    p = _prob("<보기> ㉠ 가 ㉡ 나 ㉢ 다 ㉣ 라 ㉤ 마",
              ["㉠, ㉤", "㉡, ㉢"])
    fixed = _fix_bogi_jamo(_fix_boki_stem_labels(p))[0]["choices"]
    assert fixed[0] == "㉠, ㉤"


def test_plain_jamo_choices_still_work():
    # 기존 자모 교정 회귀 확인 (ㅎ→ㄹ)
    p = _prob("<보기> ㄱ. 가 ㄴ. 나 ㄷ. 다 ㄹ. 라",
              ["ㄱ, ㅎ", "ㄴ, ㄷ"])
    fixed = _fix_bogi_jamo(p)[0]["choices"]
    assert fixed[0] == "ㄱ, ㄹ"


def test_stem_fix_feeds_choices_validation():
    # 실사고 통합: 상자 라벨(㉤ 오독)을 먼저 고쳐야 choices 의 ㉤→㉣ 교정이 성립
    p = _prob("<보기> ㉠ 가  ㉡ 나  ㉢ 다  ㉤ 라",   # ㉣ 이 ㉤ 으로 오독된 상자
              ["㉠, ㉤", "㉡, ㉢"])
    out = _fix_bogi_jamo(_fix_boki_stem_labels(p))[0]
    assert "㉣ 라" in out["stem"]
    assert out["choices"][0] == "㉠, ㉣"
