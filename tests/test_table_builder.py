"""TableBuilder: 격자 표 + 테두리 상자(1×1 다중 문단 셀) 생성 테스트 (2026-07-19).

조건·과정 상자를 한글 네이티브 표로 재현하는 경로의 회귀 방지:
- 셀 텍스트의 '\n' 은 hp:t 에 남기지 않고 문단(hp:p)으로 분리해야 한다
  (hp:t 안 개행은 한글에서 줄바꿈으로 렌더되지 않음).
- 인라인 수식 \\( .. \\) 은 hp:equation(수식 스크립트)으로 변환된다.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from pipeline.table import TableBuilder, _split_lines  # noqa: E402

TEMPLATE = str(Path(__file__).resolve().parents[1] / "backend" / "templates" / "base.hwpx")
NS_P = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"


def _tbl(para):
    return para.find(f"{NS_P}run/{NS_P}tbl")


def test_grid_table_basic():
    b = TableBuilder(TEMPLATE)
    para = b.build([["계급", "도수"], ["1~5", "3"]])
    tbl = _tbl(para)
    assert tbl is not None
    assert tbl.get("rowCnt") == "2" and tbl.get("colCnt") == "2"


def test_box_single_cell_multiline_paragraphs():
    # 조건·과정 상자 = 1×1 표, 셀 안 줄 수만큼 hp:p — '\n' 이 hp:t 에 남으면 안 된다
    b = TableBuilder(TEMPLATE)
    lines = ["\\(x^2-6x-6=0\\)에서", "\\(x^2-6x+A=6+A\\)", "\\((x-B)^2=C\\)"]
    para = b.build([[{"text": "\n".join(lines)}]])
    tbl = _tbl(para)
    assert tbl.get("rowCnt") == "1" and tbl.get("colCnt") == "1"
    sub = tbl.find(f"{NS_P}tr/{NS_P}tc/{NS_P}subList")
    paras = sub.findall(f"{NS_P}p")
    assert len(paras) == 3                      # 줄당 문단 하나
    xml = ET.tostring(tbl, encoding="unicode")
    assert "\n" not in "".join(t.text or "" for t in tbl.iter(f"{NS_P}t"))
    assert "equation" in xml                    # 수식이 스크립트로 변환됨


def test_split_lines_keeps_eqn_runs_whole():
    runs = [{"type": "text", "text": "첫줄\n둘째줄 "},
            {"type": "eqn", "hwp_script": "x^{2}"},
            {"type": "text", "text": "\n셋째줄"}]
    lines = _split_lines(runs)
    assert len(lines) == 3
    assert lines[1][-1]["type"] == "eqn"        # 수식은 둘째 줄에 통째로
    assert lines[2][0]["text"] == "셋째줄"
