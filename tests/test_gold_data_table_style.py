"""데이터표 골드 양식(헤더 이중선 + 위치별 borderFill) 회귀 테스트.

본문 마크다운 표가 일반 격자가 아니라 학원장 골드 양식(헤더 윗변 DOUBLE_SLIM
이중선 + 9-구역 borderFill)으로 렌더되는지 검증. 표 없으면 무사 통과(no-op).
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from src.text_only.text_builder import build_from_markdown
from src.common.hwpx_table_inserter import restyle_data_tables_to_gold
from src.common.hwpx_validator import validate_hwpx

_TPL = Path("samples/template.hwpx")

_MD = """1. 다음 표를 보자. [3점]

| z | P |
|---|---|
| 1.0 | 0.3413 |
| 1.5 | 0.4332 |

① 1 ② 2 ③ 3 ④ 4 ⑤ 5
"""


def _build(md: str, out: Path) -> Path:
    res = build_from_markdown(md, out, _TPL)
    return Path(res["output"]) if isinstance(res, dict) else out


def test_gold_style_applied(tmp_path):
    out = _build(_MD, tmp_path / "t.hwpx")
    restyle_data_tables_to_gold(out)

    z = zipfile.ZipFile(out)
    hdr = z.read("Contents/header.xml").decode("utf-8")
    sec = z.read("Contents/section0.xml").decode("utf-8")

    assert "DOUBLE_SLIM" in hdr                      # 이중선 borderFill 주입됨
    assert not validate_hwpx(str(out))               # 구조 유효
    # 이중선 borderFill ID가 실제 셀에서 사용됨 (헤더행)
    dbl = set(re.findall(
        r'<hh:borderFill id="(\d+)"[^>]*>(?:(?!</hh:borderFill>).)*?DOUBLE_SLIM',
        hdr, re.DOTALL))
    used = set(re.findall(r'<hp:tc\b[^>]*borderFillIDRef="(\d+)"', sec))
    assert dbl & used, "이중선 borderFill이 셀에 적용되지 않음"


def test_noop_when_no_tables(tmp_path):
    out = _build("1. 표 없는 문제. [3점]\n\n① 1 ② 2 ③ 3\n", tmp_path / "n.hwpx")
    restyle_data_tables_to_gold(out)                 # 예외 없이 통과해야 함
    assert not validate_hwpx(str(out))
