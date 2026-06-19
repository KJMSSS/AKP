"""문제만 공정척도(포함도·문항수) 회귀 테스트.

골드가 정답·해설을 더 포함해도(scope 불일치) 우리 '문제만' 출력이 부당 감점되지
않아야 한다. 포함도는 우리 chars 기준 recall이라 골드의 추가 풀이에 무감점.
"""
from __future__ import annotations

from scripts.gold_compare import containment, problem_count, normalize


def test_normalize_xml_escape_canonicalized():
    """XML 이스케이프 차이는 동일 수식으로 표준화(행렬 &amp; 등 공정 비교)."""
    assert normalize("$1 &amp; 2$") == normalize("$1 & 2$")
    assert normalize("$a &lt; b &gt; c$") == normalize("$a < b > c$")


def test_normalize_strips_whitespace_for_operator_spacing():
    """연산자 간격 차이는 공백 제거로 흡수(x - 1 == x-1)."""
    assert normalize("x ^ {3} - 1 = 0") == normalize("x^{3}-1=0")


def test_containment_full_subset_is_one():
    our = "문제1의값은얼마인가"
    gold = our + "[해설]따라서답은3이다출처광주"   # 골드에 풀이가 더 붙음
    assert containment(our, gold) == 1.0          # 우리 전부가 골드에 들어있음


def test_containment_scope_robust_long_gold():
    """골드가 5배 길어도(풀이 잔뜩) 우리 내용이 다 들어있으면 ~1.0."""
    our = "삼각형ABC의넓이를구하시오"
    gold = our + "[해설]" + ("이므로따라서계산하면넓이는12이다" * 5)
    assert containment(our, gold) >= 0.95


def test_containment_partial():
    our = "AAAAABBBBB"
    gold = "AAAAA"          # 절반만 일치
    assert 0.4 <= containment(our, gold) <= 0.6


def test_containment_empty_our_is_zero():
    assert containment("", "무엇이든") == 0.0


def test_containment_garbage_not_in_gold_lowers():
    """우리 출력에 골드에 없는 환각/중복이 있으면 포함도가 떨어진다(품질 신호)."""
    gold = "정답문제내용"
    our = "정답문제내용" + "Z" * 20      # 골드에 없는 잡음 20자
    assert containment(our, gold) < 0.5


def test_problem_count_uses_choices_by_default():
    text = "1번①②③④⑤ 2번①②③④⑤ 3번①②③④⑤"
    assert problem_count(text) == 3          # ① 3개


def test_problem_count_uses_source_blocks_for_solution_gold():
    """2024 풀이포함 골드: [출처] 블록 = 진짜 문항수 (① 는 풀이 enumerator로 부풀려짐)."""
    text = ("[출처]1①②③④⑤[해설]①단계②단계 "
            "[출처]2①②③④⑤[해설]①단계②단계 "
            "[출처]3①②③④⑤[해설]①단계")
    # ① 는 8개지만 [출처] 블록은 3개 → 3 반환해야
    assert text.count("①") > 3
    assert problem_count(text) == 3
