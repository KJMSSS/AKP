"""
HWPX 문제 블록 생성기.

base.hwpx(기준 템플릿)에서 실제 구조 골격을 '수확'해서, 거기에 새 내용을 채워
문제 블록을 만든다. 골격을 그대로 재사용하므로 스타일/표 테두리/여백이 원본과 동일.

수확 대상:
  - META 표 : 1×6 <hp:tbl> (셀 = [공란, 학교, 번호, 코드, 난이도, 배점])
  - 수식 객체: <hp:equation> (스크립트만 교체해서 재사용)

지문/보기 단락은 템플릿 스타일 ID(paraPrIDRef=5, charPrIDRef=8)를 그대로 사용.

run 모델:
  {"type": "text", "text": "..."}            -> <hp:t>
  {"type": "eqn",  "hwp_script": "x^{2}"}     -> <hp:equation><hp:script>
"""
from __future__ import annotations

import copy
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterable

from .assemble_hwpx import NS, _q, set_equation_script, KEEP_PARA_PR

# 본문 텍스트/보기 단락의 템플릿 스타일 (header.xml 에 정의됨)
# 문제 단락은 KEEP_PARA_PR(keepWithNext/keepLines=1)을 써서 메타+지문+보기가 단/페이지
# 경계에서 쪼개지지 않고 한 덩어리로 움직이게 한다(명시 break 대신 한글 자동흐름에 맡김).
BODY_PARA_PR = KEEP_PARA_PR
BODY_CHAR_PR = "8"
CIRCLED = ["①", "②", "③", "④", "⑤"]


def _read_section(template_path: str) -> str:
    return zipfile.ZipFile(template_path).read("Contents/section0.xml").decode("utf-8")


def _meta_table_of(p: ET.Element):
    for tbl in p.iter(_q("hp", "tbl")):
        if tbl.get("colCnt") == "6":
            return tbl
    return None


def _noncell_text_count(p: ET.Element, tbl: ET.Element) -> int:
    cell_ids = {id(t) for t in tbl.iter(_q("hp", "t"))}
    return sum(1 for t in p.iter(_q("hp", "t"))
               if id(t) not in cell_ids and (t.text or "").strip())


def harvest_meta_paragraph(template_path: str) -> ET.Element:
    """문제별 '깨끗한' 메타박스 단락(1×6 표, '..번' 포함)을 추출.

    주의: 문서의 첫 메타 단락(p#0)은 저작권/과목/학교명 등 '문서 헤더'가 함께 붙어
    있고 표도 여러 개라 잘못 쓰면 헤더가 매 문제마다 반복된다. 따라서 colCnt6 표를
    가지면서 표 밖 텍스트가 가장 적은(=헤더 없는) 단락을 고른다.
    표는 <hp:container> 안에 위치정보와 함께 있어 단락을 통째로 복제해야 안전하다.
    """
    root = ET.fromstring(zipfile.ZipFile(template_path).read("Contents/section0.xml"))
    best, best_noise = None, None
    for p in root.findall("hp:p", NS):
        tbl = _meta_table_of(p)
        if tbl is None:
            continue
        texts = [t.text or "" for t in tbl.iter(_q("hp", "t"))]
        if not any("번" in x for x in texts):
            continue
        noise = _noncell_text_count(p, tbl)
        if best is None or noise < best_noise:
            best, best_noise = p, noise
            if noise == 0:
                break
    if best is None:
        raise ValueError("메타표(1×6) 단락을 찾지 못했습니다.")
    return copy.deepcopy(best)


def harvest_equation(template_path: str) -> ET.Element:
    sec = zipfile.ZipFile(template_path).read("Contents/section0.xml")
    m = re.search(rb"<hp:equation\b.*?</hp:equation>", sec, re.S)
    wrap = b'<w xmlns:hp="%s" xmlns:hc="%s">%s</w>' % (
        NS["hp"].encode(), NS["hc"].encode(), m.group(0))
    return ET.fromstring(wrap).find("hp:equation", NS)


@dataclass
class BlockFactory:
    """문제 블록(메타표 + 지문 + 보기)을 생성한다."""

    template_path: str

    def __post_init__(self) -> None:
        self._meta_tpl = harvest_meta_paragraph(self.template_path)
        self._eq_tpl = harvest_equation(self.template_path)

    # --- 메타표(단락 통째) -------------------------------------------------
    def meta_paragraph(self, *, school: str, number: str, code: str,
                       difficulty: str, points: str) -> ET.Element:
        """깨끗한 메타박스를 복제해 표의 col1~col5 를 실제 값으로 채운다.

        표 1×6: [col0=빈칸(autoNum=초록 일련번호, 한글이 자동채번) | col1=학교 |
        col2=번호 | col3=코드 | col4=난이도 | col5=배점]. col0 은 건드리지 않는다.
        """
        p = copy.deepcopy(self._meta_tpl)
        p.set("paraPrIDRef", KEEP_PARA_PR)   # 메타도 다음(지문)과 함께 묶이게(번호배너 홀로 떨어짐 방지)
        by_col = {"1": school, "2": number, "3": code, "4": difficulty, "5": points}
        tbl = _meta_table_of(p)
        for tc in tbl.iter(_q("hp", "tc")):
            addr = tc.find(_q("hp", "cellAddr"))
            col = addr.get("colAddr") if addr is not None else None
            if col not in by_col:
                continue
            t = next(tc.iter(_q("hp", "t")), None)
            if t is None:  # 셀에 텍스트 run 이 없으면 첫 단락에 생성
                run = next(tc.iter(_q("hp", "run")), None)
                if run is not None:
                    t = ET.SubElement(run, _q("hp", "t"))
            if t is not None:
                t.text = by_col[col]
        return p

    # --- run 들을 단락에 채우기 -------------------------------------------
    def _fill_runs(self, p: ET.Element, runs: Iterable[dict]) -> None:
        for run in runs:
            r = ET.SubElement(p, _q("hp", "run"))
            r.set("charPrIDRef", BODY_CHAR_PR)
            if run["type"] == "text":
                t = ET.SubElement(r, _q("hp", "t"))
                t.text = run["text"]
            elif run["type"] == "eqn":
                r.append(set_equation_script(self._eq_tpl, run["hwp_script"]))
            else:
                raise ValueError(f"unknown run type: {run['type']}")

    def _para(self) -> ET.Element:
        p = ET.Element(_q("hp", "p"))
        p.set("paraPrIDRef", BODY_PARA_PR)
        p.set("styleIDRef", "1")
        for a in ("pageBreak", "columnBreak", "merged"):
            p.set(a, "0")
        return p

    def stem(self, number: str, runs: list[dict]) -> ET.Element:
        """지문 단락. 앞에 문제번호를 붙인다."""
        p = self._para()
        head = [{"type": "text", "text": f"{number}. "}] + runs
        self._fill_runs(p, head)
        return p

    def choices(self, choices: list[list[dict]]) -> list[ET.Element]:
        """보기 ①~⑤. 각 보기를 한 단락에 (번호 마커 + run들)."""
        out = []
        for i, ch in enumerate(choices):
            p = self._para()
            marker = CIRCLED[i] if i < len(CIRCLED) else f"({i+1})"
            self._fill_runs(p, [{"type": "text", "text": f"{marker} "}] + ch)
            out.append(p)
        return out

    def problem_blocks(self, problem: dict) -> list[ET.Element]:
        """문제 1개 -> [메타표, 지문, 보기들...] 요소 리스트."""
        blocks = [self.meta_paragraph(
            school=problem.get("school", ""),
            number=problem.get("number", ""),
            code=problem.get("code", ""),
            difficulty=problem.get("difficulty", ""),
            points=problem.get("points", ""),
        )]
        blocks.append(self.stem(problem.get("number", ""), problem.get("stem", [])))
        blocks.extend(self.choices(problem.get("choices", [])))
        return blocks
