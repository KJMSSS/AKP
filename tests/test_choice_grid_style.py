"""오지선다 그리드((가)(나)(다)×①②③④⑤) 양식 회귀 테스트.

선택 그리드는 데이터표(격자+이중선 헤더)와 달리 **외곽 프레임만·셀 무테**가
골드 양식. ①②③④⑤ 3개 이상으로 감지해 분기 적용한다.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from src.text_only.text_builder import build_from_markdown
from src.common.hwpx_table_inserter import restyle_data_tables_to_gold, _is_choice_grid
from src.common.hwpx_validator import validate_hwpx

_TPL = Path("samples/template.hwpx")

_MD = """7. 다음 중 옳은 것은? [3점]

|  | (가) | (나) | (다) |
|--|------|------|------|
| ① | a | b | c |
| ② | a | b | c |
| ③ | a | b | c |
| ④ | a | b | c |
| ⑤ | a | b | c |

① 1 ② 2 ③ 3 ④ 4 ⑤ 5
"""


def test_is_choice_grid_unit():
    assert _is_choice_grid("①②③④⑤")
    assert _is_choice_grid("x ① y ② z ③ w")
    assert not _is_choice_grid("① ②")            # 2개 — 선택그리드 아님
    assert not _is_choice_grid("1.0 0.3413 0.4332")  # 데이터표


def test_choice_grid_is_borderless(tmp_path):
    out = tmp_path / "c.hwpx"
    res = build_from_markdown(_MD, out, _TPL)
    out = Path(res["output"]) if isinstance(res, dict) else out
    restyle_data_tables_to_gold(out)

    z = zipfile.ZipFile(out)
    hdr = z.read("Contents/header.xml").decode("utf-8")
    sec = z.read("Contents/section0.xml").decode("utf-8")
    assert not validate_hwpx(str(out))

    none_ids = {
        bf.group(1)
        for bf in re.finditer(r'<hh:borderFill id="(\d+)"[^>]*>(.*?)</hh:borderFill>', hdr, re.DOTALL)
        if all(re.search(r'<hh:%sBorder[^>]*type="NONE"' % s, bf.group(2))
               for s in ("left", "right", "top", "bottom"))
    }
    grid = next(t for t in re.findall(r'<hp:tbl\b.*?</hp:tbl>', sec, re.DOTALL)
                if sum(t.count(c) for c in "①②③④⑤") >= 3)
    cells = set(re.findall(r'<hp:tc\b[^>]*borderFillIDRef="(\d+)"', grid))
    assert cells, "셀 없음"
    assert cells <= none_ids, f"오지선다 셀이 무테가 아님: {cells}"
