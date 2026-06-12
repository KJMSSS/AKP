"""problem_segmenter 조건 (가)(나) 감지 회귀 테스트.

핵심 시나리오: 여러 줄 조건 병합, 참조 문장 오탐 제외, 공백·수식 래핑 허용.
"""
from __future__ import annotations

from src.text_only.problem_segmenter import _is_cond_label, parse_problems


class TestCondLabel:
    def test_basic_label(self):
        assert _is_cond_label("（가） 모든 실수 x에 대하여")
        assert _is_cond_label("(나) $f(0)=1$")

    def test_label_only_line(self):
        assert _is_cond_label("（가）")

    def test_spaces_inside_parens(self):
        assert _is_cond_label("( 가 ) 조건 내용")

    def test_math_wrapped_label(self):
        assert _is_cond_label("$(가)$ 조건 내용")

    def test_no_space_content_is_still_label(self):
        # 내용이 조사와 같은 글자로 시작해도 두 번째 레이블이 없으면 조건
        assert _is_cond_label("（가）가장 작은 자연수를 구하시오")
        assert _is_cond_label("（나）모든 실수에 대하여 성립한다")

    def test_reference_with_wa(self):
        assert not _is_cond_label("(가)와 (나)를 모두 만족시키는")
        assert not _is_cond_label("（가）와 （나）를 동시에 만족시키는")

    def test_reference_with_comma(self):
        assert not _is_cond_label("(가), (나)를 모두 만족시키는 함수")

    def test_reference_josa_with_second_label(self):
        assert not _is_cond_label("(가)를 만족시키고 (나)를 만족시키지 않는")

    def test_non_label(self):
        assert not _is_cond_label("일반 문장입니다")
        assert not _is_cond_label("(1) 소문제")


class TestMultilineCondition:
    def test_wrapped_condition_merged(self):
        md = "\n".join([
            "1. 다음 조건을 만족시키는 함수의 개수는? [4점]",
            "（가） 모든 실수 x에 대하여",
            "$f(x+2)=f(x)$이다.",
            "（나） $f(0)=1$",
            "",
            "① 1",
            "② 2",
            "③ 3",
            "④ 4",
            "⑤ 5",
        ])
        _, segs = parse_problems(md)
        assert len(segs) == 1
        seg = segs[0]
        assert len(seg.conditions) == 2
        # 꺾인 둘째 줄이 (가) 항목에 병합
        assert seg.conditions[0] == "（가） 모든 실수 x에 대하여 $f(x+2)=f(x)$이다."
        assert seg.conditions[1] == "（나） $f(0)=1$"
        assert len(seg.choices) == 5

    def test_reference_sentence_stays_in_problem_text(self):
        md = "\n".join([
            "2. 두 조건",
            "(가)와 (나)를 모두 만족시키는 함수의 개수는? [3점]",
            "① 1",
            "② 2",
            "③ 3",
            "④ 4",
            "⑤ 5",
        ])
        _, segs = parse_problems(md)
        seg = segs[0]
        assert seg.conditions == []
        assert "(가)와 (나)를" in seg.problem_text

    def test_single_line_conditions_unchanged(self):
        md = "\n".join([
            "3. 다음 조건을 만족시킬 때 [4점]",
            "（가） $a_1 = 1$",
            "（나） $a_{n+1} = 2a_n$",
            "",
            "① 1",
            "② 2",
            "③ 4",
            "④ 8",
            "⑤ 16",
        ])
        _, segs = parse_problems(md)
        seg = segs[0]
        assert seg.conditions == ["（가） $a_1 = 1$", "（나） $a_{n+1} = 2a_n$"]

    def test_condition_block_stops_at_score_line(self):
        # 조건이 score 이전 본문에 있는 경우 — 점수 줄을 삼키지 않는다
        md = "\n".join([
            "4. 함수 f에 대하여",
            "（가） $f(1)=0$",
            "값을 구하면? [4점]",
            "① 1",
            "② 2",
            "③ 3",
            "④ 4",
            "⑤ 5",
        ])
        _, segs = parse_problems(md)
        seg = segs[0]
        assert len(seg.conditions) == 1
        assert "[4점]" not in seg.conditions[0]
        assert "값을 구하면?" in seg.conditions[0] or "값을 구하면?" in seg.problem_text
