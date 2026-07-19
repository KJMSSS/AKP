"""
시험지 조립 엔트리포인트 (오프라인).

Problem JSON 리스트 -> base.hwpx 템플릿(A3 세로, 2단 신문형) 위에 조립 -> 결과 hwpx.

배치 규칙(사용자 요구: 페이지당 최대 4문제 = 2단 × 2문제):
  - 2단 신문형. 문제를 순서대로 흘려보내되, '문제 시작 단락(메타표)'에 columnBreak 를
    달아 2문제마다 새 단에서 시작하게 한다:  i % 2 == 0 and i > 0  -> columnBreak.
      → 단당 최대 2문제. 한 단(2단째)이 차면 한글이 자동으로 다음 페이지 1단으로 넘긴다.
  - pageBreak 는 절대 쓰지 않는다. (원본 base.hwpx 검증: pageBreak 0개, columnBreak 11개가
    모두 '메타단락 자신'에 붙어 있었다. 우리도 그 메커니즘을 그대로 따른다.)
  - 높이(mm) 추정은 하지 않는다(과거 실패). 큰 문제는 둘째가 한글 자동흐름으로 다음 단에
    밀려 단당 1문제가 되며, 이때도 과밀(5문제)·쪼개짐은 생기지 않는다.
  - 보조: 문제의 모든 단락(메타+지문+그림+보기)에 keep paraPr(KEEP_PARA_PR/KEEP_PIC_PARA_PR,
    keepWithNext=1)을 달아 한 단 안에서도 지문↔그림이 분리되지 않게 한다.

수식 변환:
  - 각 run 이 {"type":"eqn","latex": "..."} 이면 latex_to_hwp 로 변환.
  - 변환 실패(ok=False)면 run 에 needs_image=True 표시 -> 상위(그림 파이프라인)에서
    수식-이미지로 대체(하이브리드 전략). v1 에서는 원본 LaTeX 를 텍스트로 임시 표기.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .assemble_hwpx import HwpxBuilder
from .blocks import BlockFactory
from .table import TableBuilder
try:
    from mathconv.latex_to_hwp import latex_to_hwp          # cwd=backend
except ImportError:  # pragma: no cover
    from ..mathconv.latex_to_hwp import latex_to_hwp        # cwd=examconv (backend.*)


@dataclass
class BuildReport:
    problems: int = 0
    equations_ok: int = 0
    equations_fallback: int = 0
    fallbacks: list = field(default_factory=list)  # 이미지 폴백 필요한 LaTeX 목록
    figures_inserted: int = 0
    tables_inserted: int = 0
    pages: int = 1                                  # 가변 패킹이 만든 총 페이지 수(추정)


# stem/보기 텍스트에 남는 위치 표시 토큰 '[그림]'/'[표]' 를 제거한다. 실제 그림·표는
# figures 로 따로 삽입되므로 본문의 토큰 글자는 중복이라 지운다(앞쪽 공백/개행까지 정리).
_FIG_TAG = re.compile(r'\s*\[\s*(?:그림|표)\s*\]\s*')


def _strip_figure_tags(runs: list[dict]) -> list[dict]:
    out = []
    for r in runs:
        if r.get("type") == "text":
            t = _FIG_TAG.sub("", r.get("text", "")).rstrip()
            if not t.strip():
                continue  # '[그림]' 만 있던 run 은 통째로 제거
            r = {**r, "text": t}
        out.append(r)
    return out


# 표(이미지)가 삽입되는 문제는 발문에 표 박스 내용((가)/(나)… 조건)이 텍스트로도 들어가
# 중복·겹침이 생긴다. 표가 배정된 문제에 한해 '(가)/(나)… 로 시작하는 순수 조건 줄'만
# 제거한다. 질문이 섞인 줄(값은?/구하 등)은 답을 잃지 않도록 보존한다(자동 분리 불가).
_COND_MARK = re.compile(r'^\s*\([가나다라마바사]\)')
_QWORD = re.compile(r'\?|값은|구하|얼마|몇|합은|곱은|최[댓솟]')


def _has_table(prob: dict) -> bool:
    return any(isinstance(f, dict) and f.get("kind") == "table"
               for f in (prob.get("figures") or []))


# figure 안에 <보기> 상자가 함께 크롭돼 발문의 <보기> 지문과 중복되는 경우를 잡기 위한
# 값싼 사전 게이트. (1) 비-표 그림이 배정돼 있고 (2) 발문에 <보기> 헤더 + ㄱ/ㄴ/ㄷ… 항목이
# 2개 이상이면 후보로 본다. 실제 '그림에 보기 포함' 여부는 clean_stem_against_figure(비전)이
# 최종 판정한다(그림에 없으면 원본 유지 → 오탐 방지). 질문 문장의 '다음 <보기> 중' 한 번만
# 으로는 상자로 보지 않으려고 항목 마커 2개 이상을 요구한다.
_BOKI_HDR = re.compile(r'[<〈]\s*보\s*기\s*[>〉]')
# 항목 라벨: 'ㄱ.' 처럼 구두점 붙은 자모, 또는 원문자 ㉠㉡…㉳(자기구분형 — 뒤 구두점 없음.
# U+3260~U+3273 연속 블록 = ㉠~㉭ 자모 + ㉮~㉳ 음절). 2026-07-02 화정중 9번: '㉠y=…' 꼴을
# 구두점 요구 때문에 못 잡아 <보기> 상자가 발문·그림에 이중으로 들어갔다.
_BOKI_ITEM = re.compile(r'(?:^|\s)[ㄱㄴㄷㄹㅁㅂ][.)．]|[㉠-㉳]')


def _has_boki_box_in_stem(runs: list[dict]) -> bool:
    txt = "".join(r.get("text", "") for r in runs if r.get("type") == "text")
    return bool(_BOKI_HDR.search(txt)) and len(_BOKI_ITEM.findall(txt)) >= 2


# '<보기>' 헤더 바로 뒤에 첫 항목 라벨('ㄱ.' 또는 원문자 ㉠/㉮ — 원문자는 구두점 없이 바로
# 내용/수식이 이어짐)이 오는 = 보기 상자 헤더. 질문의 '다음 <보기> 중'은 뒤에 항목이
# 안 오므로 매칭되지 않는다(질문 보존). 라벨 뒤 수식 run 경계로 텍스트가 끊겨도
# lookahead 는 라벨 문자까지만 요구하므로 매칭된다(㉠ 뒤 내용은 다음 run 인 경우).
_BOKI_BOX_HDR = re.compile(r'[<〈]\s*보\s*기\s*[>〉]\s*(?=[ㄱ]\s*[.)．]|[㉠㉮])')


def _strip_boki_box(runs: list[dict]) -> list[dict]:
    """발문에서 '<보기> 상자(헤더 + ㄱㄴㄷ 항목 블록)'를 제거. 질문/도입부(예 '[그림]')는 유지.
    <보기> 박스는 발문 끝에 오므로, 박스 헤더 지점부터 발문 끝까지 통째로 버린다(수식 run 포함).
    박스 헤더를 못 찾으면 원본 그대로(오탐 방지)."""
    out = []
    for r in runs:
        if r.get("type") == "text":
            m = _BOKI_BOX_HDR.search(r.get("text", ""))
            if m:
                head = r["text"][:m.start()].rstrip()
                if head:
                    out.append({"type": "text", "text": head})
                return out   # 박스 헤더 이후(항목 수식 포함) 전부 제거
        out.append(r)
    return runs


def _figure_image_path(prob: dict) -> str | None:
    """비-표(figure) 항목의 이미지 경로(문자열/딕셔너리 path·orig). 없으면 None."""
    for f in (prob.get("figures") or []):
        if isinstance(f, dict):
            if f.get("kind") == "table":
                continue
            p = f.get("orig") or f.get("path")   # 원본 크롭 우선(보기 지문이 선명)
        else:
            p = f
        if p:
            return p
    return None


def _strip_table_conditions(runs: list[dict]) -> list[dict]:
    """표가 배정된 문제의 stem 에서 '순수 조건 줄'을 제거(표 이미지가 그 내용을 담당)."""
    toks = []   # ('t', 글자) | ('e', eqn-run)
    for r in runs:
        if r.get("type") == "text":
            toks.extend(("t", ch) for ch in r.get("text", ""))
        else:
            toks.append(("e", r))
    lines, cur = [], []        # '\n' 기준 줄 분리
    for k in toks:
        if k[0] == "t" and k[1] == "\n":
            lines.append(cur); cur = []
        else:
            cur.append(k)
    lines.append(cur)
    kept = []
    for ln in lines:
        txt = "".join(c[1] for c in ln if c[0] == "t")
        if _COND_MARK.match(txt) and not _QWORD.search(txt):
            continue           # 순수 조건 줄 → 제거
        kept.append(ln)
    # 앞/뒤/연속 빈 줄 정리
    while kept and kept[0] == []:
        kept.pop(0)
    while kept and kept[-1] == []:
        kept.pop()
    cleaned = []
    for ln in kept:
        if ln == [] and (not cleaned or cleaned[-1] == []):
            continue
        cleaned.append(ln)
    # 줄 사이 '\n' 복원 후 runs 재구성(연속 text 병합)
    new = []
    for li, ln in enumerate(cleaned):
        if li > 0:
            new.append(("t", "\n"))
        new.extend(ln)
    res, buf = [], ""
    for k in new:
        if k[0] == "t":
            buf += k[1]
        else:
            if buf:
                res.append({"type": "text", "text": buf}); buf = ""
            res.append(k[1])
    if buf:
        res.append({"type": "text", "text": buf})
    return res


def _resolve_runs(runs: list[dict], report: BuildReport) -> list[dict]:
    """run 들의 latex 를 한글 수식 스크립트로 변환. 실패분은 폴백 표시."""
    out = []
    for run in runs:
        if run.get("type") == "eqn":
            if "hwp_script" in run:  # 이미 변환됨
                out.append(run)
                report.equations_ok += 1
                continue
            res = latex_to_hwp(run.get("latex", ""))
            if res.ok:
                out.append({"type": "eqn", "hwp_script": res.script})
                report.equations_ok += 1
            else:
                report.equations_fallback += 1
                report.fallbacks.append({"latex": run.get("latex", ""), "reason": res.reason})
                # 변환 실패 → 원본 LaTeX 를 텍스트로 + 검색용 마커(【확인필요】).
                # 직원이 한글에서 Ctrl+F '확인필요' 로 찾아 수동 교정하는 자리다
                # (AKP 추가 2026-07-06 — 구엔진의 【★ 확인 필요】 관례 승계).
                out.append({"type": "text", "text": "【확인필요】" + run.get("latex", "")})
        else:
            out.append(run)
    return out


# 페이지 배치는 '추정'하지 않는다. 원본 시험지(base.hwpx)가 pageBreak 0 으로 한글 자동
# 페이지흐름에 맡기듯, 우리도 명시 columnBreak/pageBreak 를 쓰지 않는다. 대신 문제 단락에
# KEEP_PARA_PR(keepWithNext/keepLines=1)을 달아 '문제 한 덩어리는 단/페이지 경계에서
# 쪼개지지 않고 통째로만 이동'하게 하고, 단/페이지 채움은 한글이 실제 렌더링 높이로 한다.
# (과거의 높이추정 mm 패킹은 한글 실측 높이와 어긋나 과밀·overflow·빈페이지를 유발해 폐기.)
SEOSUL_ANSWER_LINES = 7              # 서술형 풀이 답란(빈 줄 수)


def _is_seosul(prob: dict) -> bool:
    """서술형(주관식) 문항인지 — 번호에 '서술'이 들어가면 서술형으로 본다."""
    return "서술" in str(prob.get("number", ""))


def _is_colbreak(i: int, prob: dict) -> bool:
    """이 문제를 새 단에서 시작(columnBreak)할지 — 2문제마다(i%2==0, i>0).
    단 서술형은 제외한다: 서술형은 풀이 답란(빈 줄)이 커서, 직전 문제의 답란이 단을
    넘치면 그 단이 통째로 비고 다음 문제가 한 단 더 밀린다(3페이지 1단 빔의 원인).
    서술형은 cb 를 강제하지 않고 자연 흐름에 맡긴다."""
    return i % 2 == 0 and i > 0 and not _is_seosul(prob)


def _auto_width_mm(path: str) -> float:
    """이미지 종횡비로 본문 삽입 너비(mm)를 자동 결정.

    단 폭(≈114.5mm = (A3폭297 - 좌우여백60 - 단간격8)/2)에 맞춰 크게 넣는다. 세로로 긴
    도형은 폭을 키워도 add_figure 의 max_height_mm(높이 상한)이 폭을 다시 조절한다.
    (표를 도형 버튼으로 재작도해도, 와이드 그래프여도 단 폭으로 들어가게 하는 안전망.
     명시적 표(kind="table")는 api_build 가 dict 로 폭을 직접 지정한다.)
    """
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
        aspect = (w / h) if h else 1.0
    except Exception:  # noqa: BLE001 (못 읽으면 기본 폭)
        return 96.0
    if aspect >= 1.7:
        return 110.0      # 표·와이드 그래프 → 단 폭 가득
    if aspect >= 1.25:
        return 102.0      # 약간 넓음
    if aspect >= 0.8:
        return 96.0       # 정사각 근처 도형
    return 88.0           # 세로로 긴 도형(높이 상한 max_height_mm 이 폭을 추가 조절)


def build_exam(template_path: str, problems: list[dict], out_path: str, *,
               header: dict | None = None, gap_lines: int = 20) -> BuildReport:
    builder = HwpxBuilder(template_path)
    factory = BlockFactory(template_path)
    table_builder = TableBuilder(template_path)
    report = BuildReport(problems=len(problems))
    if header:
        builder.set_exam_header(
            school=header.get("school", ""), subject=header.get("subject", ""),
            exam_range=header.get("exam_range", ""), term=header.get("term", ""),
            region=header.get("region", ""))

    # 명시 break 없음: 문제를 순서대로 흘려보내고, 단/페이지 나눔은 한글이 keepWithNext/
    # keepLines(KEEP_PARA_PR) 규칙에 따라 자동으로 한다(문제 통째 유지 + 자동 채움).
    for i, prob in enumerate(problems):
        prob = dict(prob)
        stem_runs = _strip_figure_tags(prob.get("stem", []))
        if _has_table(prob):
            stem_runs = _strip_table_conditions(stem_runs)   # 표 내용 중복 줄 제거
        # <보기> 상자를 그림/표로 삽입한 경우, 발문에 텍스트로 남은 <보기> 항목 블록을
        # 패턴으로 결정적 제거(Claude 의미기반은 불안정·다중그림 놓침 → 패턴이 확실).
        # 질문/도입부([그림] 등)는 보존되고, choices 는 아래서 별도 처리되어 보존된다.
        if (_figure_image_path(prob) or _has_table(prob)) and _has_boki_box_in_stem(stem_runs):
            stem_runs = _strip_boki_box(stem_runs)
        prob["stem"] = _resolve_runs(stem_runs, report)
        prob["choices"] = [_resolve_runs(_strip_figure_tags(ch), report)
                           for ch in prob.get("choices", [])]
        blocks = factory.problem_blocks(prob)   # [메타, 지문, *보기]
        # 페이지당 4문제(2단×2): 2문제마다 '문제 시작(메타) 단락'에 columnBreak 를 달아
        # 새 단에서 시작시킨다. pageBreak 는 쓰지 않는다(2단이 차면 한글이 자동으로 다음
        # 페이지 1단으로 넘긴다). 원본 base.hwpx 와 동일한 결정적 배치 방식.
        blocks[0].set("columnBreak", "1" if _is_colbreak(i, prob) else "0")
        blocks[0].set("pageBreak", "0")
        # 메타 + 지문 추가 → 그림(있으면) 삽입 → 보기 추가
        builder.add_block(blocks[0])
        builder.add_block(blocks[1])
        # 표 주입(지문 직후): 구조화 grid 우선, 과대/복잡/실패는 이미지 폴백
        for tab in prob.get("tables", []) or []:
            # 이미지 표는 '원본 크기'(width_mm = 크롭 픽셀÷DPI)로 삽입 — 단 폭(110mm)
            # 고정 확대는 표가 원본보다 커져 페이지가 불어난다(exam-engine 이식).
            tab_w = float(tab.get("width_mm") or 110)
            try:
                if tab.get("source") == "struct" and tab.get("grid"):
                    builder.add_block(table_builder.build(tab["grid"]))
                    report.tables_inserted += 1
                elif tab.get("image"):
                    builder.add_figure(tab["image"], width_mm=tab_w)
                    report.tables_inserted += 1
            except Exception as e:  # noqa: BLE001 (구조화 실패 → 이미지 폴백)
                if tab.get("image"):
                    try:
                        builder.add_figure(tab["image"], width_mm=tab_w)
                        report.tables_inserted += 1
                    except Exception:
                        report.fallbacks.append({"latex": f"[표 {tab.get('label', '')}]", "reason": str(e)})
                else:
                    report.fallbacks.append({"latex": f"[표 {tab.get('label', '')}]", "reason": str(e)})
        for fig in prob.get("figures", []) or []:
            # figures 항목 = 경로(str) 또는 {path, width_mm, max_height_mm}(dict).
            # 표는 단 폭 가득(큰 width_mm)으로 넣기 위해 dict 로 폭/높이상한을 받는다.
            if isinstance(fig, dict):
                fpath = fig.get("path")
                width_mm = float(fig.get("width_mm", 96.0))
                max_height_mm = float(fig.get("max_height_mm", 108.0))
            else:
                fpath = fig
                width_mm = _auto_width_mm(fig)
                max_height_mm = 108.0            # add_figure 기본값과 동일
            if not fpath:
                continue                          # 경로 없는 figure 는 삽입 불가 → 스킵
            try:
                builder.add_figure(fpath, width_mm=width_mm, max_height_mm=max_height_mm)
                report.figures_inserted += 1
            except Exception as e:  # noqa: BLE001
                report.fallbacks.append({"latex": f"[그림 {fpath}]", "reason": str(e)})
        for b in blocks[2:]:
            builder.add_block(b)
        # 서술형: 풀이 답란(빈 줄)을 둬서 학생이 답을 쓸 공간을 만든다.
        if not prob.get("choices") and _is_seosul(prob):
            for _ in range(SEOSUL_ANSWER_LINES):
                builder.add_text("")
        # 문제 사이 간격(마지막 문제 제외): 빈 단락 spacer. 기본 20줄(≈120mm) —
        # 6줄(2026-07-04)·9줄(2026-07-05)·15줄(2026-07-07)로 키워왔으나 짧은 객관식은
        # 여전히 상단 몰림(2026-07-08 학원장 '종종 몰린다'). 15→20줄 상향으로 두 번째 문제를
        # 단 하단 쪽으로 더 내린다(짧은 문제 ~48%→~65%). 큰 문제(~150mm)+gap(120mm)+문제2 여도
        # 둘째가 단(370mm)을 넘치면 한글이 다음 단으로 자연 이동(쪼갬·과밀 아님). '한 단 2문제
        # 고정 + 높이 무관 gap' 이라 여전히 높이 추정 패킹이 아니라 시험지마다 안 깨진다.
        # ⚠ 진짜 세로 justify(남는 공간 균등분배)는 엔진 규칙(높이 추정 금지)상 불가 —
        # 이 gap 상향이 규칙 안에서 가능한 최대 완화이고, 미세조정은 한글 실측으로만(2026-07-08).
        # 단, 다음 문제가 columnBreak(새 단 시작)면 gap 을 넣지 않는다 — 빈 단락이 단/
        # 페이지 경계를 넘치면 그 단이 통째로 비고 다음 문제가 한 단 더 밀린다(3페이지
        # 1단이 통째로 비던 원인). 새 단으로 넘어가는 문제는 어차피 시각적으로 분리된다.
        if i < len(problems) - 1 and not _is_colbreak(i + 1, problems[i + 1]):
            for _ in range(gap_lines):
                builder.add_text("")

    builder.save(out_path)
    return report


if __name__ == "__main__":
    import json, sys
    tpl = "templates/base.hwpx"
    # 데모: 6문제 (페이지당 4 -> 2페이지). latex 입력으로 변환 경로까지 확인.
    demo = []
    for n in range(1, 7):
        demo.append({
            "school": "데모고", "number": f"{n}번", "code": "2025_DEMO",
            "difficulty": "중", "points": "3.5점",
            "stem": [{"type": "text", "text": f"문제 {n}: 다음을 계산하시오 "},
                     {"type": "eqn", "latex": r"\frac{n(n+1)}{2}"}],
            "choices": [[{"type": "eqn", "latex": fr"{k}"}] for k in range(1, 6)],
        })
    rep = build_exam(tpl, demo, "/tmp/examconv_paginated.hwpx")
    print(json.dumps(rep.__dict__, ensure_ascii=False, indent=2))
