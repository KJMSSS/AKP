"""데이터표 fallback 테두리 회귀 테스트.

마커(【★ 데이터표:N번】) 기반 fallback 빌더 `_build_data_table_xml`이 전 셀을
borderFillIDRef=2(사방 SOLID 격자)로 쓰는지 검증. 기존엔 헤더=5(하단만)/데이터=4
(우측만)라 행 사이 가로선이 빠져 부분선만 렌더됐다(text_builder는 이미 bf#2로 수정됨).
"""
from __future__ import annotations

import re

import src.common.hwpx_table_inserter as ti
from src.common.hwpx_table_inserter import TableSpec, _build_data_table_xml


def test_fallback_data_table_all_cells_full_grid(monkeypatch):
    # 템플릿 미지원 강제 → 하드코딩 fallback 경로 진입
    monkeypatch.setattr(ti, "_TEMPLATE_SUPPORT", False)
    spec = TableSpec(
        item="19",
        headers=["기간", "확률"],
        rows=[["1주", "0.2"], ["2주", "0.5"]],
        col_widths=[12000, 12000],
    )
    xml, _h = _build_data_table_xml(spec, tbl_id=999, zo=10)

    cell_bfids = re.findall(r'<hp:tc[^>]*?borderFillIDRef="(\d+)"', xml)
    assert cell_bfids, "셀이 생성되지 않음"
    # 부분선 ID(4=우측만, 5=하단만) 잔존 금지 — 전부 2(사방 SOLID)
    assert set(cell_bfids) == {"2"}, f"부분선 borderFill 잔존: {set(cell_bfids)}"
    assert "4" not in cell_bfids and "5" not in cell_bfids
