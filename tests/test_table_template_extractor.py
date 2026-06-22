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


# ── 업로드 경로: 합성 HWPX → 추출 → 빌드 라운드트립 ─────────────────────────

_COND_TBL = (
    '<hp:tbl id="100" zOrder="5" rowCnt="1" colCnt="1">'
    '<hp:sz width="29190" height="2000"/>'
    '<hp:tr><hp:tc name=""><hp:subList id="">'
    '<hp:p id="1"><hp:run><hp:t>조건표</hp:t></hp:run></hp:p>'
    '</hp:subList><hp:cellSz width="29190" height="2000"/></hp:tc></hp:tr></hp:tbl>'
)
_BOILER_TBL = (
    '<hp:tbl id="150" zOrder="6" rowCnt="1" colCnt="1">'
    '<hp:sz width="29190" height="2000"/>'
    '<hp:tr><hp:tc name=""><hp:subList id="">'
    '<hp:p id="1"><hp:run><hp:t>보기표</hp:t></hp:run></hp:p>'
    '</hp:subList><hp:cellSz width="29190" height="2000"/></hp:tc></hp:tr></hp:tbl>'
)
_DATA_TBL = (
    '<hp:tbl id="200" zOrder="7" rowCnt="2" colCnt="2">'
    '<hp:sz width="20000" height="4000"/>'
    '<hp:tr>'
    '<hp:tc name=""><hp:subList id=""><hp:p><hp:run><hp:t>데이터표</hp:t></hp:run></hp:p></hp:subList><hp:cellSz width="10000" height="2000"/></hp:tc>'
    '<hp:tc name=""><hp:subList id=""><hp:p><hp:run><hp:t>B</hp:t></hp:run></hp:p></hp:subList><hp:cellSz width="10000" height="2000"/></hp:tc>'
    '</hp:tr>'
    '<hp:tr>'
    '<hp:tc name=""><hp:subList id=""><hp:p><hp:run><hp:t>1</hp:t></hp:run></hp:p></hp:subList><hp:cellSz width="10000" height="2000"/></hp:tc>'
    '<hp:tc name=""><hp:subList id=""><hp:p><hp:run><hp:t>2</hp:t></hp:run></hp:p></hp:subList><hp:cellSz width="10000" height="2000"/></hp:tc>'
    '</hp:tr></hp:tbl>'
)


def _make_hwpx(tmp_path, *tables):
    import zipfile
    p = tmp_path / "tpl.hwpx"
    section = "<hml>" + "".join(tables) + "</hml>"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("Contents/section0.xml", section)
    return p


def test_extract_all_three_from_hwpx(tmp_path):
    """합성 HWPX(조건표/보기표/데이터표) → 셋 다 추출."""
    from src.common.table_template_extractor import extract_templates
    hwpx = _make_hwpx(tmp_path, _COND_TBL, _BOILER_TBL, _DATA_TBL)
    t = extract_templates(hwpx)
    assert t["condition_tbl"] is not None
    assert t["boilerplate_tbl"] is not None
    assert t["data_tbl"] is not None


def test_extracted_condition_builds_clean(tmp_path):
    """추출된 조건 스켈레톤이 가드 통과 + 실제 내용 채워 빌드된다 (업로드→적용 경로)."""
    from src.common.table_template_extractor import extract_templates
    from src.common.table_template_builder import build_condition_box, box_skeleton_usable
    hwpx = _make_hwpx(tmp_path, _COND_TBL)
    t = extract_templates(hwpx)
    assert box_skeleton_usable(t["condition_tbl"]["skeleton"])  # 1×1 깨끗

    para = ('<hp:p id="9"><hp:run><hp:t>조건 내용 X</hp:t></hp:run>'
            '<hp:linesegarray><hp:lineseg vertsize="1200"/></hp:linesegarray></hp:p>')
    xml, h = build_condition_box(t, [para], 500, 500)
    assert xml and "조건 내용 X" in xml
    assert "{{CONTENT}}" not in xml and "{{TBL_ID}}" not in xml
