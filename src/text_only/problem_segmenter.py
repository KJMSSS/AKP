"""
문제 단위 파서 — Mathpix MD를 문제 블록으로 분리·정렬·정제.

parse_problems(md)  → (header, segments)
rebuild_markdown(header, segments, table_items) → str

Mathpix 2컬럼 인터리빙, 학생 풀이(스크래치), 누락 선택지를 처리.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import AbstractSet

# ── 패턴 ──────────────────────────────────────────────────────────────────────
_PROB_RE   = re.compile(r'^(\d{1,2})[.．]\s*(?=\S)')  # "1. " "1．$" "22. "
_SUBJ_RE   = re.compile(r'(?:\[\^\d+\])?\s*서술형\s*(\d+)\s*[.．]?\s*')
_SCORE_RE  = re.compile(r'[\[［][\d.．]+점[\]］]')  # [3점] ／ [4.5점] ／ ［4．7점］
_CHOICE_RE = re.compile(r'^(?:[（(]\s*[1-5]\s*[）)]|[①②③④⑤])\s*')
# （가）/(나)/$(가)$ 조건 레이블 — 괄호 안팎 공백·수식 래핑 허용
_COND_LABEL_RE = re.compile(r'^(?:\$[（(]\s*[가-힣]\s*[）)]\$|[（(]\s*[가-힣]\s*[）)])')
_COND_LABEL_INNER = re.compile(r'[（(]\s*[가-힣]\s*[）)]')  # 줄 중간 탐색용 (비앵커)
_COND_RE   = _COND_LABEL_RE  # 하위 호환 별칭
_BOGI_RE   = re.compile(r'^[ㄱ-ㅎ]\s*[.．]\s*|^보기\s*$')  # ㄱ. ㄴ. ㄷ. 또는 "보기" 헤더
_ROMAN_ITEM_RE = re.compile(r'^\([ivxIVX]+\)\s+')          # (i) (ii) (iii) 로마자 번호 항목
# 질문 문장 신호 — 조건 연속 줄 병합을 끊는다 (본문 질문이 조건 박스에 빨려드는 것 방지)
_QUESTION_RE = re.compile(r'구하시오|구하여라|구하라|쓰시오|답하시오|보이시오|나타내시오|서술하시오')


def _is_bogi_line(s: str) -> bool:
    """보기 항목 판정: ㄱ/ㄴ/ㄷ 또는 로마자 (i)(ii)(iii) — 연결문 '(i) 또는 (ii)' 제외."""
    if _BOGI_RE.match(s):
        return True
    return bool(_ROMAN_ITEM_RE.match(s)) and '또는 (' not in s


def _is_cond_label(s: str) -> bool:
    """조건 항목 레이블 판정.

    '(가) 내용' / '（가）내용'은 조건이지만, 본문이 조건을 가리키는
    '(가)와 (나)를 모두 만족…' 같은 참조 문장은 제외해야 한다.
    구분 신호: 닫는 괄호에 조사·쉼표가 공백 없이 바로 붙으면 참조.
    단 '（가）가장 작은…'처럼 내용이 조사와 같은 글자로 시작할 수 있어,
    와/과/쉼표 외에는 같은 줄에 다른 레이블이 또 있을 때만 참조로 본다.
    """
    m = _COND_LABEL_RE.match(s)
    if not m:
        return False
    rest = s[m.end():]
    if not rest or rest[0].isspace():
        return True  # 레이블 단독 줄 또는 '(가) 내용'
    head = rest[0]
    if head == ',' or head in '와과':
        return False  # '(가),' '(가)와' — 명백한 참조
    if head in '을를은는이가의에도' and _COND_LABEL_INNER.search(rest):
        return False  # '(가)를 … (나)…' — 조사 + 두 번째 레이블 = 참조 문장
    return True


def _consume_cond_block(lines: list[str], i: int) -> tuple[str, int]:
    """lines[i]가 조건 레이블일 때, OCR 줄바꿈으로 꺾인 연속 줄까지 한 항목으로 병합.

    빈 줄/다음 레이블/선택지/보기/점수/새 문제/이미지/마커에서 끊는다.
    또한 디스플레이 수식(\\[, $$)·표 잔해(\\begin 등)·질문 문장(? 포함)은
    학생 스크래치·본문일 가능성이 높아 병합하지 않는다 (실데이터 차등 검증 근거).
    반환: (병합된 조건 항목, 다음 인덱스)
    """
    block = [lines[i].strip()]
    j = i + 1
    while j < len(lines):
        t = lines[j].strip()
        if (not t or _is_cond_label(t) or _CHOICE_RE.match(t) or _is_bogi_line(t)
                or _PROB_RE.match(t) or _SUBJ_RE.search(t) or _SCORE_RE.search(t)
                or t.startswith("![") or t.startswith("【★")
                or t.startswith(("\\[", "$$", "\\begin", "\\end", "\\hline"))
                or "?" in t or _QUESTION_RE.search(t)):
            break
        block.append(t)
        j += 1
    return " ".join(block), j


def _extract_cond_blocks(lines: list[str]) -> tuple[list[str], list[str]]:
    """줄 목록에서 조건 블록들을 추출. 반환: (조건 항목 목록, 나머지 줄 목록)."""
    conds: list[str] = []
    remaining: list[str] = []
    i = 0
    while i < len(lines):
        if _is_cond_label(lines[i].strip()):
            item, i = _consume_cond_block(lines, i)
            conds.append(item)
        else:
            remaining.append(lines[i])
            i += 1
    return conds, remaining
_IMAGE_RE  = re.compile(r'!\[.*?\]\((https://cdn\.mathpix\.com/[^)]+)\)')
_DISPLAY_MATH_RE = re.compile(r'^\$\$|^\\begin\{')
_BIGVEE_RE   = re.compile(r'\s*\$\\bigvee_\{(\d+)\}\$\s*$')  # ① OCR 아티팩트


@dataclass
class ProblemSegment:
    number: int           # 1–22 객관식 | 101–104 서술형
    problem_text: str     # 문제 본문 (score bracket 포함, 조건문 제외)
    choices: list[str]    # 선택지 원본 줄 (LLM 정규화 전)
    conditions: list[str] # （가）/（나）/（다） 조건문 줄  → 조건 표 (1×1 hp:tbl)
    boilerplate: list[str]# 보기 ㄱ/ㄴ/ㄷ 줄             → 보기 표 (1×1 hp:tbl)
    images: list[str]     # Mathpix CDN URL 목록
    is_subjective: bool
    raw_block: str        # 원본 블록 (롤백용)


def parse_problems(md: str) -> tuple[str, list[ProblemSegment]]:
    """
    마크다운을 헤더와 문제 단위로 분리.
    반환: (header_text, problems_sorted_by_number)
    """
    lines = md.split("\n")

    # 문제 시작 위치 탐색
    boundaries: list[tuple[int, int, bool]] = []  # (line_idx, sort_num, is_subj)
    for i, line in enumerate(lines):
        s = line.strip()
        m = _PROB_RE.match(s)
        if m:
            if not _SUBJ_RE.search(s):  # "20. 서술형 1." 같은 경우 서술형으로 처리
                boundaries.append((i, int(m.group(1)), False))
                continue
        m = _SUBJ_RE.search(s)  # "N. 서술형 M." 형식 허용
        if m:
            boundaries.append((i, 100 + int(m.group(1)), True))

    if not boundaries:
        return md, []

    header = "\n".join(lines[: boundaries[0][0]])
    segments: list[ProblemSegment] = []

    for idx, (start, num, is_subj) in enumerate(boundaries):
        end = boundaries[idx + 1][0] if idx + 1 < len(boundaries) else len(lines)
        block_lines = lines[start:end]
        block_text  = "\n".join(block_lines)

        prob_text, choices, conditions, boilerplate = _split_block(block_lines, is_subj)
        images = _IMAGE_RE.findall(block_text)

        segments.append(ProblemSegment(
            number=num,
            problem_text=prob_text,
            choices=choices,
            conditions=conditions,
            boilerplate=boilerplate,
            images=images,
            is_subjective=is_subj,
            raw_block=block_text,
        ))

    segments.sort(key=lambda s: s.number)
    return header, segments


def _split_block(
    block_lines: list[str],
    is_subj: bool,
) -> tuple[str, list[str], list[str], list[str]]:
    """
    한 문제 블록에서 (문제 텍스트, 선택지, 조건문, 보기) 분리.

    반환: (problem_text, choices, conditions, boilerplate)
    · score bracket 뒤 선택지 5개 수집 → 이후 스크래치 제거
    · 조건문 （가）/（나）/（다） → conditions (→ 조건 표)
    · 보기 ㄱ./ㄴ./ㄷ. → boilerplate (→ 보기 표)
    · 서술형은 score 이후 전부 제거
    """
    # score bracket 위치 탐색
    # 서술형: 마지막 [N점]을 기준으로 (소문제 개별 점수 [1점][2점] 무시)
    # 객관식: 첫 번째 [N점]
    score_idx = -1
    if is_subj:
        for j in range(len(block_lines) - 1, -1, -1):
            if _SCORE_RE.search(block_lines[j]):
                score_idx = j
                break
    else:
        for j, bline in enumerate(block_lines):
            if _SCORE_RE.search(bline):
                score_idx = j
                break

    if score_idx == -1:
        if is_subj:
            conditions, clean = _extract_cond_blocks(block_lines)
            return "\n".join(clean), [], conditions, []
        return "\n".join(block_lines), [], [], []

    # 선택지가 score bracket 이전에 등장하는 경우 (2컬럼 OCR 역전)
    # 예: 17번 — 선택지 → 이미지 → [점수] → 그림 순서로 OCR됨
    # 서술형에서는 (1)(2)(3)이 소문제이므로 선택지로 분리하지 않음
    if is_subj:
        first_choice_before = -1
    else:
        first_choice_before = next(
            (j for j, l in enumerate(block_lines[:score_idx]) if _CHOICE_RE.match(l.strip())),
            -1,
        )
    if first_choice_before > 0:
        # 첫 번째 선택지 직전까지 문제 텍스트 (단, 첫 선택지 앞 1~2줄이 $(1)$ 같은 선택지
        # 후보면 그것도 포함: 선택지 시작은 first_choice_before-1이 아닌 최대 1줄 앞까지 허용)
        choice_region_start = first_choice_before
        # $(1)$ 같은 패턴: 숫자만 있는 한 줄짜리 math가 직전에 있으면 함께 포함
        if choice_region_start > 1:
            prev = block_lines[choice_region_start - 1].strip()
            if re.match(r'^\$\(([1-5])\)\$$', prev):
                choice_region_start -= 1

        pre_score = block_lines[:score_idx]
        early_choices = [l for l in pre_score[choice_region_start:] if not l.strip().startswith("![")]
        image_lines   = [l for l in pre_score[choice_region_start:] if l.strip().startswith("![")]
        prob_lines    = list(block_lines[:choice_region_start]) + image_lines + [block_lines[score_idx]]
        after_lines   = early_choices + list(block_lines[score_idx + 1:])
    else:
        prob_lines  = list(block_lines[: score_idx + 1])
        after_lines = block_lines[score_idx + 1 :]

    # Fix: score 줄 끝 $\bigvee_{N}$ → 선택지 ① N 으로 추출 (OCR 아티팩트)
    if prob_lines:
        bv_m = _BIGVEE_RE.search(prob_lines[-1])
        if bv_m:
            prob_lines[-1] = _BIGVEE_RE.sub('', prob_lines[-1]).rstrip()
            after_lines = [f'（1） {bv_m.group(1)}'] + list(after_lines)

    # 조건문이 score 이전 prob_lines에 있는 경우 추출 (예: 19번, 서술형 3)
    # 여러 줄로 꺾인 조건은 블록 단위로 병합 수집
    pre_conds, prob_lines = _extract_cond_blocks(prob_lines)

    # 보기 항목(ㄱ/ㄴ/ㄷ)이 score 이전 prob_lines에 있는 경우 추출
    # 로마자 (i)(ii)(iii)는 위치가 prob_lines 중간이라 자동 추출 불가
    # → 빌드 스크립트에서 【★ 보기시작/끝:N번】 마커를 올바른 위치에 직접 삽입
    pre_bogi = [l.strip() for l in prob_lines if _BOGI_RE.match(l.strip())]
    prob_lines = [l for l in prob_lines if not _BOGI_RE.match(l.strip())]

    if is_subj:
        post_conds, _ = _extract_cond_blocks(after_lines)
        return "\n".join(prob_lines), [], pre_conds + post_conds, []

    choices: list[str]     = []
    conditions: list[str]  = list(pre_conds)  # score 이전 조건 선삽입
    boilerplate: list[str] = list(pre_bogi)   # score 이전 보기 항목 선삽입
    in_bogi = False

    i = 0
    while i < len(after_lines) and len(choices) < 5:
        s = after_lines[i].strip()

        if not s:
            i += 1
            continue

        # 새 문제 시작 → 중단
        if _PROB_RE.match(s) or _SUBJ_RE.match(s):
            break

        # 조건문 （가）/（나）/（다） — 꺾인 연속 줄까지 한 항목으로
        if _is_cond_label(s):
            item, i = _consume_cond_block(after_lines, i)
            conditions.append(item)
            continue

        # 보기 헤더 또는 ㄱ/ㄴ/ㄷ/(i)/(ii)/(iii) 항목
        if _is_bogi_line(s):
            if s == "보기":
                in_bogi = True
            else:
                boilerplate.append(s)
            i += 1
            continue

        # 보기 진행 중이면 짧은 줄도 보기 항목으로
        if in_bogi and _looks_like_unlabeled_choice(s):
            boilerplate.append(s)
            i += 1
            continue
        else:
            in_bogi = False

        # 수식 스타일 선택지 번호: $(1)$ $(2)$ 등 (Mathpix OCR 아티팩트)
        if re.match(r'^\$\(([1-5])\)\$$', s):
            choices.append(s)
            i += 1
            continue

        # 선택지 레이블 있는 줄
        if _CHOICE_RE.match(s):
            choices.append(s)
            i += 1
            continue

        # 선택지 1개 이상 있고 짧은 줄 → 번호 없는 선택지 후보
        # ①②③④⑤ 형식이면 한글 길이 제한 없이 선택지로 수집
        if choices and (_CHOICE_RE.match(s) or _looks_like_unlabeled_choice(s)):
            choices.append(s)
            i += 1
            continue

        # 선택지 없는 상태에서 첫 15줄 내 탐색 허용
        if not choices and i < 15:
            i += 1
            continue

        break

    return "\n".join(prob_lines), choices, conditions, boilerplate


def _looks_like_unlabeled_choice(s: str) -> bool:
    """번호 없는 선택지 후보인지 판단."""
    if len(s) > 80:
        return False
    if s.startswith("!["):
        return False
    if _DISPLAY_MATH_RE.match(s):
        return False
    # 한글 글자가 6자 이상 → 문제 텍스트 또는 스크래치
    if len(re.findall("[가-힣]", s)) > 5:
        return False
    return True


def _normalize_score_to_end(text: str) -> str:
    """score bracket을 항상 problem_text 마지막 줄 끝으로 이동.
    마지막 [N점]을 기준으로 처리 (서술형 소문제 개별 점수 [1점][2점] 무시)."""
    m = None
    for m_ in _SCORE_RE.finditer(text):
        m = m_
    if not m:
        return text
    bracket = m.group(0)
    cleaned = (text[:m.start()] + text[m.end():]).rstrip()
    return cleaned + ' ' + bracket


def rebuild_markdown(
    header: str,
    segments: list[ProblemSegment],
    data_table_items: AbstractSet[str] | None = None,
    textbox_items: AbstractSet[str] | None = None,
    figure_items: AbstractSet[str] | None = None,
) -> str:
    """
    정제된 문제 목록을 마크다운으로 재조립.

    구조 플레이스홀더 (삽입 후 hwpx_table_inserter / hwpx_image_inserter가 교체):
      【★ 조건시작:N번】/【★ 조건끝:N번】 → conditions  → 1×1 hp:tbl 박스
      【★ 보기시작:N번】/【★ 보기끝:N번】 → boilerplate → 1×1 hp:tbl 박스
      【★ 데이터표:N번】                  → (빌드 스크립트 지정) → N×M hp:tbl
      【★ 글상자:N번】                    → (빌드 스크립트 지정) → hp:rect 글상자
      【★ 그림:N번】                      → hwpx_image_inserter → BinData 이미지

    data_table_items: 데이터 표 플레이스홀더 삽입 문제 번호 집합 {"19"}
    textbox_items:    글상자 플레이스홀더 삽입 문제 번호 집합 {"5"}
    figure_items:     그림 플레이스홀더 삽입 문제 번호 집합 {"12", "15"}
    """
    parts: list[str] = []
    if header.strip():
        parts.append(header)

    _subprob_re = re.compile(r'(?m)^( *)(\([1-9]\))( +)')

    for seg in segments:
        num_str = str(seg.number if seg.number < 100 else seg.number - 100)

        pt = seg.problem_text
        if seg.is_subjective:
            # 서술형 소문제 (N) 형식을 수식으로 감싸서 ①②③ 변환 방지
            pt = _subprob_re.sub(r'\1$\2$\3', pt)
        parts.append(_normalize_score_to_end(pt))

        # 조건문 — text_builder가 수식 처리, table_inserter가 박스로 감쌈
        if seg.conditions:
            parts.append(f"【★ 조건시작:{num_str}번】")
            for c in seg.conditions:
                parts.append(c)
            parts.append(f"【★ 조건끝:{num_str}번】")

        # 보기 (ㄱ/ㄴ/ㄷ) — 동일 방식
        if seg.boilerplate:
            parts.append(f"【★ 보기시작:{num_str}번】")
            for b in seg.boilerplate:
                parts.append(b)
            parts.append(f"【★ 보기끝:{num_str}번】")

        # 선택지
        if seg.choices:
            for c in seg.choices:
                parts.append(c)

        # 데이터 표 플레이스홀더 (빌드 스크립트 지정)
        if data_table_items and num_str in data_table_items:
            parts.append(f"【★ 데이터표:{num_str}번】")

        # 글상자 플레이스홀더 (빌드 스크립트 지정)
        if textbox_items and num_str in textbox_items:
            parts.append(f"【★ 글상자:{num_str}번】")

        # 그림 플레이스홀더 (image_extractor 감지 결과 또는 빌드 스크립트 지정)
        if figure_items and num_str in figure_items:
            parts.append(f"【★ 그림:{num_str}번】")

        parts.append("")  # 문제 사이 빈 줄

    return "\n".join(parts)
