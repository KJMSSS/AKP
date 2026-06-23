"""보기 가/나/다 블록쿼트 감지 회귀 테스트.

대성여고 13번: 보기가 ㄱ/ㄴ/ㄷ가 아니라 가/나/다 음절 + `>` 블록쿼트 +
한 줄 여러 항목 + 연속줄이라 미감지됐다(박스 누락). parse_problems가 이를
boilerplate로 잡고 rebuild_markdown이 보기 마커를 내는지 검증한다. 비-보기
인용블록(시행 설명 등)은 오분류하지 않아야 한다.
"""
from __future__ import annotations

from src.text_only.problem_segmenter import parse_problems, rebuild_markdown


_P13 = """13. 정규분포 곡선이다. 다음 보기 중 옳은 것만을 모두 고르면? [4.5점]

> 가. $E(X) < E(Y)$　　나. $\\sigma(X) > \\sigma(Y)$
> 다. $E(X) = m_{1}$, $E(Y) = m_{2}$일 때,
> $P(m_{1} \\leq X \\leq a)$

① 가 ② 나 ③ 가, 나 ④ 나, 다 ⑤ 가, 나, 다
"""


def test_gana_blockquote_bogi_detected():
    header, segs = parse_problems(_P13)
    s = segs[0]
    assert s.number == 13
    # 가/나(1줄) + 다(1줄) + 연속줄($P(...)$) = 3줄 수집
    assert len(s.boilerplate) == 3
    assert s.boilerplate[0].startswith("가.")
    assert not s.boilerplate[0].startswith(">")      # 인용부호 제거됨
    rb = rebuild_markdown(header, segs)
    assert "【★ 보기시작:13번】" in rb
    assert "【★ 보기끝:13번】" in rb


def test_non_bogi_blockquote_not_misclassified():
    # "> 주사위를…" 시행 설명 인용블록은 보기로 잡으면 안 됨 (오분류 가드)
    md = """8. 다음 시행을 한다. [4.1점]

> 주사위를 한 번 던져 나온 수가 $n$일 때, 약수 전구를 누른다.

① 90 ② 112 ③ 120 ④ 121 ⑤ 122
"""
    _, segs = parse_problems(md)
    assert segs[0].number == 8
    assert len(segs[0].boilerplate) == 0
