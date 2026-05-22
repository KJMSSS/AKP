"""
골드 HWPX 완전 분석 — samples/11b/*.hwpx → data/gold_manifest/*.json

핵심 전략:
  1. XML 문자열에서 모든 <hp:tbl> 위치를 중첩 카운터로 파악
  2. 메타 표(1×6, 학교명·N번·코드·난이도·배점)의 [start, end] 범위 확정
  3. 메타 표 end_i ~ meta 표 start_{i+1} 구간 = 문제 내용 XML
  4. 해당 구간에서 <hp:p> 단락 텍스트 + <hp:script> 수식 추출

사용:
  python scripts/analyze_gold_hwpx.py          # 전 학교
  python scripts/analyze_gold_hwpx.py 동성고    # 특정 학교
"""
from __future__ import annotations

import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT    = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "samples" / "11b"
OUT_DIR = ROOT / "data" / "gold_manifest"
OUT_DIR.mkdir(parents=True, exist_ok=True)

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
_B64_RE       = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")
_SCORE_RE     = re.compile(r"\[(\d+(?:\.\d+)?)점\]")
_CHOICE_CHARS = "①②③④⑤"
_DANDAP_RE    = re.compile(r"\[단답형(\d+)\]")
_SEOSUL_RE    = re.compile(r"\[서술형(\d+)\]")
_REVIEW_RE    = re.compile(r"검수사항을 기재")          # 검수 메모 칸 필터
_SOURCE_RE    = re.compile(r"\s*\d{4}_\d+_\d+_\w+_\S+")  # 소스 파일명 제거


# ── 표 범위 계산 (중첩 카운터) ────────────────────────────────────────────────

def _calc_tbl_ranges(raw: str) -> list[tuple[int, int]]:
    """
    XML 문자열에서 모든 <hp:tbl> 요소의 (start, end) 위치를 반환.
    중첩 표도 올바르게 처리.
    """
    opens  = [m.start() for m in re.finditer(r"<hp:tbl\s", raw)]
    closes = [m.start() for m in re.finditer(r"</hp:tbl>", raw)]
    close_len = len("</hp:tbl>")

    ranges: list[tuple[int, int]] = []
    stack:  list[int] = []
    oi = ci = 0
    while ci < len(closes):
        while oi < len(opens) and opens[oi] < closes[ci]:
            stack.append(opens[oi])
            oi += 1
        if stack:
            ranges.append((stack.pop(), closes[ci] + close_len))
        ci += 1
    return ranges


# ── 메타 표 식별 ──────────────────────────────────────────────────────────────

_NS_WRAPPER = (
    'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
    'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" '
    'xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"'
)


def _parse_meta_xml(tbl_xml: str) -> dict | None:
    """
    표 XML에서 메타 정보 추출.
    유효한 메타 표 = 1×6, 셀[2]에 'N번' 패턴.
    """
    if 'colCnt="6"' not in tbl_xml and "colCnt='6'" not in tbl_xml:
        return None
    try:
        root = ET.fromstring(f"<root {_NS_WRAPPER}>{tbl_xml}</root>")
    except Exception:
        return None

    tcs   = root.findall(f".//{{{HP}}}tc")
    if len(tcs) < 6:
        return None

    def cell_text(tc):
        parts = []
        for e in tc.iter():
            tag = e.tag.split("}")[-1]
            if tag == "t" and e.text:
                parts.append(_B64_RE.sub("", e.text))
        return "".join(parts).strip()

    texts = [cell_text(tc) for tc in tcs]
    num_m = re.match(r"^(서술형\s*)?(\d+)번$", texts[2])
    if not num_m:
        return None

    is_subj = bool(num_m.group(1))
    num     = int(num_m.group(2))
    score_m = re.search(r"([\d.]+)점", texts[5])
    score   = None
    if score_m:
        try:
            score = float(re.sub(r"\.{2,}", ".", score_m.group(1)))
        except ValueError:
            pass
    return {
        "school":        texts[1],
        "number":        num,
        "is_subjective": is_subj,
        "difficulty":    texts[4],
        "score":         score,
        "code":          texts[3],
    }


# ── 문제 내용 구간 파싱 ───────────────────────────────────────────────────────

def _iter_para_exclude_eq(elem, _in_eq: bool = False):
    """
    ET 요소 재귀 순회 — hp:equation 안의 hp:p는 건너뜀.
    최상위 hp:p 요소만 yield.
    """
    tag = elem.tag.split("}")[-1]
    if tag == "equation":
        _in_eq = True
    if tag == "p" and not _in_eq:
        yield elem
        return  # 단락 안으로 더 내려가지 않음 (iter 따로 함)
    for child in elem:
        yield from _iter_para_exclude_eq(child, _in_eq)


def _para_content(p_elem) -> tuple[str, list[str]]:
    """
    단락 하나에서 (표시 텍스트, 수식 소스 리스트) 추출.

    주의:
    - <hp:t>: e.text 와 자식(<hp:tab> 등)의 .tail 모두 수집
    - <hp:script>: 수식 소스 (equation 안에 위치)
    - <hp:caption>: 수식 주석 — B64 인코딩 정답 보호 → 건너뜀
    """
    parts: list[str] = []
    eqs:   list[str] = []

    def _walk(elem):
        tag = elem.tag.split("}")[-1]

        if tag == "caption":           # 수식 캡션(B64) 건너뜀
            return

        if tag == "script" and elem.text:
            src = elem.text.strip()
            eqs.append(src)
            parts.append(f"[식:{src[:30]}]")
            return

        if tag == "t":
            # e.text: 첫 자식 이전의 텍스트
            if elem.text:
                c = _B64_RE.sub("", elem.text)
                if c.strip():
                    parts.append(c)
            # 자식(tab 등)의 .tail: 자식 이후 텍스트 — ② ③ 마커가 여기 있음
            for child in elem:
                if child.tail:
                    c = _B64_RE.sub("", child.tail)
                    if c.strip():
                        parts.append(c)
            return  # <hp:t> 자식 안으로는 더 내려가지 않음

        for child in elem:
            _walk(child)

    _walk(p_elem)
    return "".join(parts).strip(), eqs


def _parse_chunk(chunk: str) -> dict:
    """
    메타 표 end ~ 다음 메타 표 start 구간 XML에서
    (본문 텍스트, 수식 목록, 선택지, 점수위치, 미주여부) 추출.
    """
    has_endnote = "<hp:endNote " in chunk

    # 미주 내부 XML 제거 (해설 페이지 내용 — 본문과 분리)
    chunk_body = re.sub(r"<hp:endNote\b[^>]*>.*?</hp:endNote>", "", chunk, flags=re.DOTALL)

    # 메타 표가 단락 안에 내포되므로, chunk 앞뒤에 고아 태그 단편이 존재.
    # 앞: </hp:run></hp:p> 같은 닫힘 단편 → 첫 완전한 <hp:p 부터
    # 뒤: 다음 메타 표를 담은 단락이 열리다 잘림 → 마지막 </hp:p> 까지
    first_p = chunk_body.find("<hp:p ")

    # pre-para 구간: </hp:tbl> 직후 ~ 첫 <hp:p> 이전
    # [서술형N] 레이블과 수식이 <hp:p> 없이 직접 붙는 경우 있음 (국제고/금호고 서술형 케이스)
    pre_lines: list[str] = []
    pre_eqs:   list[str] = []
    pre_label_type = None
    pre_label_num  = None
    if first_p > 0:
        pre_para = chunk_body[:first_p]
        for t_m in re.finditer(r"<hp:t>([^<]*)</hp:t>", pre_para):
            text = _B64_RE.sub("", t_m.group(1)).strip()
            if text:
                pre_lines.append(text)
                m = _DANDAP_RE.search(text)
                if m:
                    pre_label_type, pre_label_num = "short_answer", int(m.group(1))
                m = _SEOSUL_RE.search(text)
                if m:
                    pre_label_type, pre_label_num = "essay", int(m.group(1))
        for sc_m in re.finditer(r"<hp:script>([^<]*)</hp:script>", pre_para):
            src = sc_m.group(1).strip()
            if src:
                pre_eqs.append(src)
        chunk_body = chunk_body[first_p:]

    last_p_close = chunk_body.rfind("</hp:p>")
    if last_p_close != -1:
        chunk_body = chunk_body[:last_p_close + len("</hp:p>")]

    # ET 파싱 (네임스페이스 래퍼 추가)
    try:
        root = ET.fromstring(f"<root {_NS_WRAPPER}>{chunk_body}</root>")
    except Exception:
        return {
            "problem_text": " ".join(pre_lines).strip(),
            "equations": pre_eqs, "eq_count": len(pre_eqs),
            "choices": {}, "score_position": "없음", "has_endnote": has_endnote,
            "label_type": pre_label_type, "label_num": pre_label_num,
        }

    # equation 바깥의 단락만 순회
    lines: list[str] = []
    all_eqs: list[str] = []
    for p in _iter_para_exclude_eq(root):
        txt, eqs = _para_content(p)
        all_eqs.extend(eqs)
        if txt:
            lines.append(txt)

    # pre-para 내용을 앞에 병합, 검수 메모 칸 + 소스 파일명 제거
    merged = []
    for l in pre_lines + lines:
        if _REVIEW_RE.search(l):
            continue
        l = _SOURCE_RE.sub("", l).strip()
        if l:
            merged.append(l)
    lines    = merged
    all_eqs  = pre_eqs   + all_eqs

    # 정답/해설 경계 (해설 페이지 분리자)
    ans_i = next((i for i, l in enumerate(lines) if l.strip() == "[정답]"), None)
    exp_i = next((i for i, l in enumerate(lines)
                  if re.match(r"^[<\[]?\s*해설\s*[>\]]?$", l.strip())), None)

    # 선택지 시작 = ①이 있는 첫 번째 줄
    choice_i = next(
        (i for i, l in enumerate(lines) if any(l.startswith(m) for m in _CHOICE_CHARS)),
        None,
    )

    # 본문 끝 = 선택지/정답/해설 중 가장 먼저 나오는 것
    cutoffs = [x for x in (choice_i, ans_i, exp_i) if x is not None]
    body_end  = min(cutoffs) if cutoffs else len(lines)
    body_text = " ".join(lines[:body_end]).strip()

    # 점수 위치
    score_pos = "없음"
    sm = _SCORE_RE.search(body_text)
    if sm:
        ratio     = sm.start() / max(len(body_text), 1)
        score_pos = "앞" if ratio < 0.15 else "끝" if ratio > 0.75 else "중간"

    # 선택지 추출
    ch_start = choice_i if choice_i is not None else body_end
    choices: dict[str, str] = {}
    for cp in lines[ch_start:]:
        # 정답/해설 이후 줄은 건너뜀
        if cp.strip() == "[정답]" or re.match(r"^[<\[]?\s*해설\s*[>\]]?$", cp.strip()):
            break
        for marker in _CHOICE_CHARS:
            if marker in cp and marker not in choices:
                pat = re.compile(re.escape(marker) + r"(.*?)(?=[①②③④⑤]|$)", re.DOTALL)
                cm  = pat.search(cp)
                if cm:
                    choices[marker] = re.sub(r"\s+", " ", cm.group(1)).strip()

    # 단답형/서술형 레이블 감지 — pre-para에서 이미 찾은 경우 우선 사용
    label_type = pre_label_type
    label_num  = pre_label_num
    if label_type is None:
        for line in lines[:body_end + 1]:
            m = _DANDAP_RE.search(line)
            if m:
                label_type, label_num = "short_answer", int(m.group(1))
                break
            m = _SEOSUL_RE.search(line)
            if m:
                label_type, label_num = "essay", int(m.group(1))
                break

    return {
        "problem_text":  body_text,
        "equations":     all_eqs,
        "eq_count":      len(all_eqs),
        "choices":       choices,
        "score_position": score_pos,
        "has_endnote":   has_endnote,
        "label_type":    label_type,   # "short_answer" / "essay" / None
        "label_num":     label_num,    # [단답형N]의 N / None
    }


# ── 헤더 표 파싱 ──────────────────────────────────────────────────────────────

def _parse_header(tbl_xml: str) -> dict:
    try:
        root = ET.fromstring(f"<root {_NS_WRAPPER}>{tbl_xml}</root>")
    except Exception:
        return {}
    tcs   = root.findall(f".//{{{HP}}}tc")
    texts = []
    for tc in tcs:
        parts = []
        for e in tc.iter():
            if e.tag.split("}")[-1] == "t" and e.text:
                parts.append(_B64_RE.sub("", e.text).strip())
        texts.append("".join(parts).strip())
    return {
        "subject":  texts[0] if len(texts) > 0 else "",
        "range":    texts[1] if len(texts) > 1 else "",
        "district": texts[2] if len(texts) > 2 else "",
        "school":   texts[3] if len(texts) > 3 else "",
        "term":     texts[4] if len(texts) > 4 else "",
    }


# ── HWPX 전체 분석 ────────────────────────────────────────────────────────────

def analyze_hwpx(path: Path) -> dict:
    school = path.stem.strip("[]").replace("2025_1_1_b_공수1_", "")

    with zipfile.ZipFile(str(path)) as z:
        raw      = z.read("Contents/section0.xml").decode("utf-8", errors="ignore")
        bin_files = [n for n in z.namelist() if n.startswith("BinData/")]

    # 전체 통계
    total_eqs   = raw.count("<hp:equation ")
    total_tbls  = raw.count("<hp:tbl ")
    total_pics  = raw.count("<hp:pic ")
    total_notes = raw.count("<hp:endNote ")
    choice_cnt  = sum(raw.count(c) for c in _CHOICE_CHARS)

    # 표 범위 계산
    tbl_ranges = _calc_tbl_ranges(raw)

    # 메타 표 + 헤더 표 식별
    header_info:  dict        = {}
    meta_list: list[tuple[int, int, dict]] = []  # (start, end, meta)
    header_found = False

    for (start, end) in tbl_ranges:
        tbl_xml = raw[start:end]
        meta = _parse_meta_xml(tbl_xml)
        if meta:
            meta_list.append((start, end, meta))
        elif not header_found and 'colCnt="4"' in tbl_xml:
            header_info = _parse_header(tbl_xml)
            header_found = True

    # 문제별 내용 추출
    problems: dict[str, dict] = {}
    for idx, (start, end, meta) in enumerate(meta_list):
        next_start = meta_list[idx + 1][0] if idx + 1 < len(meta_list) else len(raw)
        chunk = raw[end:next_start]
        content = _parse_chunk(chunk)

        is_subj = meta["is_subjective"]
        num     = meta["number"]
        num_key = str(num)   # 임시 key — 서술형은 post-process에서 갱신

        problems[num_key] = {
            "number":        num,
            "is_subjective": is_subj,
            "problem_type":  "multiple_choice",  # post-process에서 갱신
            "label_num":     content["label_num"],
            "difficulty":    meta["difficulty"],
            "score":         meta["score"],
            "problem_text":  content["problem_text"],
            "equations":     content["equations"],
            "eq_count":      content["eq_count"],
            "choices":       content["choices"],
            "choice_count":  len(content["choices"]),
            "score_position": content["score_position"],
            "has_endnote":   content["has_endnote"],
        }

    # Post-process: problem_type 결정
    # 1. [단답형N]/[서술형N] 레이블이 있으면 최우선 적용
    # 2. 없으면 choice_count == 0 → is_subjective (단답형과 서술형을 합쳐 "essay"로)
    for key in list(problems.keys()):
        prob = problems[key]
        ltype = None
        for c in _parse_chunk.__code__.co_consts:  # 사용 안 함 — content에서 직접 가져옴
            break
        # label_type은 content에서 이미 넣었음
        # (재계산: 문제 본문에서 직접 감지)
        body = prob["problem_text"]
        m_dandap = _DANDAP_RE.search(body)
        m_seosul = _SEOSUL_RE.search(body)
        if m_dandap:
            prob["problem_type"] = "short_answer"
            prob["is_subjective"] = True
            new_key = f"단답형{prob['number']}"
            problems[new_key] = problems.pop(key)
        elif m_seosul:
            prob["problem_type"] = "essay"
            prob["is_subjective"] = True
            new_key = f"서술형{prob['number']}"
            problems[new_key] = problems.pop(key)
        elif not prob["is_subjective"] and prob["choice_count"] == 0:
            prob["problem_type"] = "essay"
            prob["is_subjective"] = True
            new_key = f"서술형{prob['number']}"
            problems[new_key] = problems.pop(key)

    obj_nums   = [v["number"] for v in problems.values() if v["problem_type"] == "multiple_choice"]
    subj_nums  = [v["number"] for v in problems.values() if v["is_subjective"]]
    sa_nums    = [v["number"] for v in problems.values() if v["problem_type"] == "short_answer"]
    essay_nums = [v["number"] for v in problems.values() if v["problem_type"] == "essay"]

    return {
        "school":              school,
        "header":              header_info,
        "obj_count":           len(obj_nums),
        "subj_count":          len(subj_nums),
        "short_answer_count":  len(sa_nums),
        "essay_count":         len(essay_nums),
        "obj_numbers":         sorted(obj_nums),
        "subj_numbers":        sorted(subj_nums),
        "short_answer_numbers": sorted(sa_nums),
        "essay_numbers":       sorted(essay_nums),
        "score_list":          {k: v["score"] for k, v in problems.items()},
        "total_equations": total_eqs,
        "total_tables":    total_tbls,
        "total_pics":      total_pics,
        "total_bin_files": len(bin_files),
        "total_endnotes":  total_notes,
        "choice_marker_total": choice_cnt,
        "score_position_stats": dict(Counter(v["score_position"] for v in problems.values())),
        "problems":        problems,
    }


# ── 출력 ─────────────────────────────────────────────────────────────────────

def _print_summary(r: dict):
    s = r["school"]
    print(f"\n{'='*62}")
    print(f"  {s}")
    print(f"{'='*62}")
    h = r["header"]
    print(f"  헤더: {h.get('subject','')} / {h.get('range','')} / "
          f"{h.get('district','')} {h.get('school','')} / {h.get('term','')}")
    print(f"  객관식(선다형) {r['obj_count']}개  {r['obj_numbers']}")
    sa = r.get('short_answer_count', 0)
    es = r.get('essay_count', 0)
    if sa or es:
        print(f"  단답형 {sa}개  {r.get('short_answer_numbers',[])}  /  서술형 {es}개  {r.get('essay_numbers',[])}")
    else:
        print(f"  서술형(미구분) {r['subj_count']}개  {r['subj_numbers']}")
    print(f"  수식 {r['total_equations']}개 | 표 {r['total_tables']}개 | "
          f"이미지 {r['total_pics']}개 | 바이너리 {r['total_bin_files']}개 | "
          f"미주 {r['total_endnotes']}개")
    print(f"  선택지 마커 합계: {r['choice_marker_total']}개")
    print(f"  점수 위치 통계: {r['score_position_stats']}")
    print()
    for key, p in r["problems"].items():
        ptype = p.get("problem_type", "")
        tag = {"multiple_choice": "선다형", "short_answer": "단답형", "essay": "서술형"}.get(ptype, "??")
        body = p["problem_text"][:72].replace("\n", " ")
        ch1  = next(iter(p["choices"].values()), "")[:12] if p["choices"] else ""
        print(f"  [{tag:3s}] {key:>8s}번  배점={p['score']}  난이도={p['difficulty']:1s}  "
              f"수식={p['eq_count']:3d}  선택지={p['choice_count']}  "
              f"점수위치={p['score_position']}  "
              f"{'미주O' if p['has_endnote'] else '미주X'}")
        print(f"           {body}")
        if ch1:
            print(f"           ① {ch1}")


def main():
    args = sys.argv[1:]
    targets = (
        [SRC_DIR / f"[2025_1_1_b_공수1_{a}].hwpx" for a in args]
        if args else
        sorted(SRC_DIR.glob("[[]2025_1_1_b_공수1_*].hwpx"))
    )

    all_results = []
    for path in targets:
        if not path.exists():
            print(f"[없음] {path.name}")
            continue
        print(f"분석 중: {path.name} ...", end="", flush=True)
        try:
            r = analyze_hwpx(path)
            all_results.append(r)
            out = OUT_DIR / f"{r['school']}.json"
            out.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f" 저장")
            _print_summary(r)
        except Exception as e:
            import traceback
            print(f" [오류] {e}")
            traceback.print_exc()

    if len(all_results) > 1:
        print(f"\n{'='*80}")
        print(f"{'학교':<12} {'객관식':>5} {'서술형':>5} {'수식':>5} {'표':>4} "
              f"{'이미지':>5} {'미주':>4} {'선택지마커':>8}")
        print(f"{'-'*80}")
        for r in all_results:
            print(f"{r['school']:<12} {r['obj_count']:>5} {r['subj_count']:>5} "
                  f"{r['total_equations']:>5} {r['total_tables']:>4} "
                  f"{r['total_pics']:>5} {r['total_endnotes']:>4} "
                  f"{r['choice_marker_total']:>8}")


if __name__ == "__main__":
    main()
