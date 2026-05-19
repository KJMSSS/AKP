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
_CHOICE_RE = re.compile(r'^[（(]\s*([1-5])\s*[）)]\s*')
_COND_RE   = re.compile(r'^[（(][가-힣][）)]\s*')         # （가）, (나) 조건문
_BOGI_RE   = re.compile(r'^[ㄱ-ㅎ]\s*[.．]\s*|^보기\s*$')  # ㄱ. ㄴ. ㄷ. 또는 "보기" 헤더
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
            boundaries.append((i, int(m.group(1)), False))
            continue
        m = _SUBJ_RE.match(s)
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
    # score bracket 첫 번째 위치
    score_idx = -1
    for j, bline in enumerate(block_lines):
        if _SCORE_RE.search(bline):
            score_idx = j
            break

    if score_idx == -1:
        return "\n".join(block_lines), [], [], []

    # 선택지가 score bracket 이전에 등장하는 경우 (2컬럼 OCR 역전)
    # 예: 17번 — 선택지 → 이미지 → [점수] → 그림 순서로 OCR됨
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

    if is_subj:
        return "\n".join(prob_lines), [], [], []

    choices: list[str]     = []
    conditions: list[str]  = []
    boilerplate: list[str] = []
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

        # 조건문 （가）/（나）/（다）
        if _COND_RE.match(s):
            conditions.append(s)
            i += 1
            continue

        # 보기 헤더 또는 ㄱ/ㄴ/ㄷ 항목
        if _BOGI_RE.match(s):
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
        if choices and _looks_like_unlabeled_choice(s):
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


def rebuild_markdown(
    header: str,
    segments: list[ProblemSegment],
    data_table_items: AbstractSet[str] | None = None,
    textbox_items: AbstractSet[str] | None = None,
) -> str:
    """
    정제된 문제 목록을 마크다운으로 재조립.

    구조 플레이스홀더 (삽입 후 hwpx_table_inserter가 교체):
      【★ 조건시작:N번】/【★ 조건끝:N번】 → conditions  → 1×1 hp:tbl 박스
      【★ 보기시작:N번】/【★ 보기끝:N번】 → boilerplate → 1×1 hp:tbl 박스
      【★ 데이터표:N번】                  → (빌드 스크립트 지정) → N×M hp:tbl
      【★ 글상자:N번】                    → (빌드 스크립트 지정) → hp:rect 글상자

    data_table_items: 데이터 표 플레이스홀더 삽입 문제 번호 집합 {"19"}
    textbox_items:    글상자 플레이스홀더 삽입 문제 번호 집합 {"5"}
    """
    parts: list[str] = []
    if header.strip():
        parts.append(header)

    for seg in segments:
        num_str = str(seg.number if seg.number < 100 else seg.number - 100)

        parts.append(seg.problem_text)

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

        parts.append("")  # 문제 사이 빈 줄

    return "\n".join(parts)
