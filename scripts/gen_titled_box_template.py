"""골드 보기 박스(table[16])를 재사용 가능한 제목-박스 템플릿 JSON으로 추출.

출력: src/common/titled_box_template.json (레포 커밋)
  {
    "skeleton":   table[16] XML, 자리표시자: {{TBL_ID}} {{ZO}} {{TITLE}} {{CONTENT}}
                  borderFillIDRef → {{BF_51}}…{{BF_58}}, char/para 기준 0(제목만 1/7)
    "borderfills": {"51": "<hh:borderFill id=\"{{ID}}\" …>", …, "58": …}
  }
런타임(hwpx_table_inserter)이 이 템플릿으로 조건/보기 박스를 만들고,
borderFill 을 출력 헤더의 빈 번호로 주입한다.

제목 정렬/글꼴은 web 템플릿(samples/template.hwpx) 기준 paraPr 1(가운데)·charPr 7
(한컴바탕 12pt 진하게)을 baked. (web 파이프라인은 단일 템플릿)
"""
from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.common.table_template_extractor import _find_tables  # noqa: E402

GOLD = Path(r"D:\f1\AKP\samples\templates\table_templates.hwpx")
OUT  = Path(__file__).resolve().parent.parent / "src" / "common" / "titled_box_template.json"


def _bf_block(hdr, bid):
    m = re.search(rf'<(\w+):borderFill id="{bid}"', hdr)
    pfx = m.group(1); s = m.start()
    e = hdr.find(f'</{pfx}:borderFill>', s) + len(f'</{pfx}:borderFill>')
    return hdr[s:e]


def main() -> None:
    gsec = zipfile.ZipFile(GOLD).read("Contents/section0.xml").decode("utf-8")
    ghdr = zipfile.ZipFile(GOLD).read("Contents/header.xml").decode("utf-8")
    t = _find_tables(gsec)[16]

    # borderFillIDRef 51~58 → 자리표시자
    for i in range(51, 59):
        t = t.replace(f'borderFillIDRef="{i}"', f'borderFillIDRef="{{{{BF_{i}}}}}"')
    # 글꼴/단락 기준 0 (출력 doc 안전값)
    t = re.sub(r'charPrIDRef="\d+"', 'charPrIDRef="0"', t)
    t = re.sub(r'paraPrIDRef="\d+"', 'paraPrIDRef="0"', t)
    # id/zOrder 동적
    t = re.sub(r'(<hp:tbl\b[^>]*?\bid=")\d+(")', r'\g<1>{{TBL_ID}}\g<2>', t, count=1)
    t = re.sub(r'(zOrder=")\d+(")', r'\g<1>{{ZO}}\g<2>', t, count=1)

    # 내용 셀(부분집합 텍스트 포함)의 subList 내부 → {{CONTENT}}
    ci = t.find("부분집합")
    assert ci != -1, "내용 셀 식별 실패"
    sl_start = t.rfind("<hp:subList ", 0, ci)
    sl_open_end = t.find(">", sl_start) + 1
    sl_close = t.find("</hp:subList>", sl_open_end)
    t = t[:sl_open_end] + "{{CONTENT}}" + t[sl_close:]

    # 제목: 텍스트 자리표시자 + 가운데(paraPr 1) + 한컴바탕12진하게(charPr 7)
    t = t.replace("| 보 기 |", "| {{TITLE}} |")
    marker = "| {{TITLE}} |"
    ti = t.find(marker)
    ps = t.rfind("<hp:p ", 0, ti); pe = t.find(">", ps) + 1
    t = t[:ps] + re.sub(r'paraPrIDRef="\d+"', 'paraPrIDRef="1"', t[ps:pe], count=1) + t[pe:]
    ti = t.find(marker)
    rs = t.rfind("<hp:run ", 0, ti); re_ = t.find(">", rs) + 1
    t = t[:rs] + re.sub(r'charPrIDRef="\d+"', 'charPrIDRef="7"', t[rs:re_], count=1) + t[re_:]

    bfs = {}
    for i in range(51, 59):
        d = _bf_block(ghdr, str(i))
        d = re.sub(r'(id=")\d+(")', r'\g<1>{{ID}}\g<2>', d, count=1)
        bfs[str(i)] = d

    OUT.write_text(json.dumps({"skeleton": t, "borderfills": bfs},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"생성: {OUT} ({OUT.stat().st_size} bytes)")
    # 자리표시자 점검
    for ph in ("{{TBL_ID}}", "{{ZO}}", "{{TITLE}}", "{{CONTENT}}", "{{BF_51}}", "{{BF_58}}"):
        print(f"  {ph}:", "OK" if ph in t else "없음!")
    print("  borderfills:", list(bfs.keys()))


if __name__ == "__main__":
    main()
