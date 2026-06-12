"""표 템플릿 스켈레톤 오염 가드 회귀 테스트.

실사고: 추출기가 6×4 답안표를 조건 박스 스켈레톤으로 저장 →
조건 내용이 폭 2623 셀에 세로로 흐르고 (가)(나)(다)·①~⑤ 잔존 노출.
오염 스켈레톤은 거부하고 내장 1×1 박스로 폴백해야 한다.
"""
from __future__ import annotations

from src.common.table_template_builder import (
    build_condition_box,
    build_boilerplate_box,
)

_CLEAN_SKELETON = (
    '<hp:tbl id="{{TBL_ID}}" zOrder="{{ZO}}" rowCnt="1" colCnt="1">'
    '<hp:sz width="29190" height="{{HEIGHT}}"/>'
    '<hp:tr><hp:tc><hp:subList>{{CONTENT}}</hp:subList>'
    '<hp:cellSz width="29190" height="282"/></hp:tc></hp:tr></hp:tbl>'
)

# 실사고 형태: 다중 셀 + 원본 텍스트 잔존
_DIRTY_SKELETON = (
    '<hp:tbl id="{{TBL_ID}}" zOrder="{{ZO}}" rowCnt="6" colCnt="4">'
    '<hp:sz width="31898" height="{{HEIGHT}}"/>'
    '<hp:tr><hp:tc><hp:subList>{{CONTENT}}</hp:subList>'
    '<hp:cellSz width="2623" height="12485"/></hp:tc>'
    '<hp:tc><hp:subList><hp:p><hp:run><hp:t>(가)</hp:t></hp:run></hp:p></hp:subList>'
    '<hp:cellSz width="9758" height="1665"/></hp:tc></hp:tr>'
    '<hp:tr><hp:tc><hp:subList><hp:p><hp:run><hp:t>①</hp:t></hp:run></hp:p></hp:subList>'
    '<hp:cellSz width="2623" height="2231"/></hp:tc></hp:tr></hp:tbl>'
)

_PARA = '<hp:p id="1"><hp:run><hp:t>조건 내용</hp:t></hp:run></hp:p>'


class TestSkeletonGuard:
    def test_dirty_condition_skeleton_rejected(self):
        templates = {"condition_tbl": {"skeleton": _DIRTY_SKELETON, "width": "31898"}}
        xml, h = build_condition_box(templates, [_PARA], 100, 100)
        assert xml == "" and h == 0  # fallback 신호 → 내장 1×1 박스 사용

    def test_dirty_boilerplate_skeleton_rejected(self):
        templates = {"boilerplate_tbl": {"skeleton": _DIRTY_SKELETON, "width": "32095"}}
        xml, h = build_boilerplate_box(templates, [_PARA], 100, 100)
        assert xml == "" and h == 0

    def test_clean_skeleton_accepted(self):
        templates = {"condition_tbl": {"skeleton": _CLEAN_SKELETON, "width": "29190"}}
        xml, h = build_condition_box(templates, [_PARA], 100, 100)
        assert xml != ""
        assert "조건 내용" in xml
        assert 'rowCnt="1" colCnt="1"' in xml
        assert "{{CONTENT}}" not in xml

    def test_no_templates_fallback(self):
        assert build_condition_box(None, [_PARA], 1, 1) == ("", 0)
