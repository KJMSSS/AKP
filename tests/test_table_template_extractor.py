"""table_template_extractor._classify 회귀 테스트.

핵심: [증명 오지선다]·[그림 오지선다] 처럼 (가)(나) 또는 ㄱㄴㄷ 를 가지면서
①②③④⑤ 선택지 그리드인 표를 condition/boilerplate 로 오분류하지 않는다.
(오분류 시 condition_tbl 이 6×4 오지선다 표로 오염돼 폴백 경고가 매번 떴다.)
"""
from __future__ import annotations

from src.common.table_template_extractor import _classify


def _tbl(text: str) -> str:
    # _classify 는 _plain_text(<hp:t>) 만 보므로 셀 텍스트만 흉내내면 충분
    cells = "".join(f"<hp:t>{t}</hp:t>" for t in text.split("|"))
    return f"<hp:tbl>{cells}</hp:tbl>"


def test_proof_choice_grid_not_condition():
    # [증명 오지선다]: (가)(나)(다) + ①②③④⑤ → 선택지 그리드, condition 아님
    xml = _tbl("(가)|(나)|(다)|①|②|③|④|⑤")
    assert _classify(xml) is None


def test_figure_choice_grid_not_boilerplate():
    # [그림 오지선다]: ㄱㄴㄷ 없이 ①②③④⑤ 만 → None
    xml = _tbl("①|②|③|④|⑤")
    assert _classify(xml) is None


def test_real_condition_box_still_condition():
    # 진짜 조건박스: (가)(나) 만, 선택지 없음 → condition
    xml = _tbl("(가) 모든 실수 x|(나) f(0)=1")
    assert _classify(xml) == "condition"


def test_real_boilerplate_still_boilerplate():
    # 진짜 보기: 보 기 + ㄱㄴㄷ, 선택지 없음 → boilerplate
    xml = _tbl("보 기|ㄱ. 성립한다|ㄴ. 거짓이다|ㄷ. 참이다")
    assert _classify(xml) == "boilerplate"


def test_explicit_keyword_priority():
    # 명시 키워드는 선택지 유무와 무관하게 우선
    assert _classify(_tbl("데이터표|①|②|③")) == "data"
    assert _classify(_tbl("조건표|①|②|③")) == "condition"
