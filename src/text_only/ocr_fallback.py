"""
OCR Fallback — 손상 영역 플레이스홀더 삽입 모듈

Mathpix OCR 결과에서 복구 불가능한 손상을 감지하면 두 가지 형태의
플레이스홀더로 치환한다:

  - 인라인 마커 `[★ 확인]`
      수식 내 한글, 빈 함수 정의 같이 본문 중간에 박히는 케이스
  - 블록 마커 `[★ 본문 손상 — 원본 PDF의 N번 참조]`
      이미지 대체 줄, 디스플레이 수식 한글 같이 줄 단위 손상

누더기 본문(짧은 마커가 한 줄에 여러 개 박힌 형태)을 막기 위한 사후 정리:
  - 한 줄에 인라인 마커 3개 이상 → 줄 전체를 블록 마커로 교체
  - 연속된 블록 마커 → 첫 번째 1개로 통합

이전에 시도한 Claude Vision 자동 재처리는 수식 환각 사례
(y=√a → y=√6, nroot126 → nroot46)이 발견되어 비활성화 상태이다.
학원 운영에서 환각은 치명적이므로 "감지 + 사람이 직접 확인"을 기본 동작으로 한다.

Vision 재처리 함수와 비용 계산 코드는 ENABLE_VISION_FALLBACK 플래그
아래에 보존되어 있어, 환각 안전장치를 추가한 뒤 재활성화할 수 있다.

손상 판단 기준:
  1. 수식 구분자($...$, $$...$$) 안에 한글 포함
  2. Mathpix가 텍스트 대신 이미지 링크를 반환한 영역
     (https://cdn.mathpix.com/cropped/... 패턴)
  3. 빈 함수 정의 (= 우변이 비어 있음)
"""

import base64
import os
import re
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from src.learn.apply_corrections import apply_corrections, summarize_log
from src.text_only.layout_filter import apply_layout_filter
from src.text_only.vision_stage_a import run_vision_stage_a

_CORRECTIONS_PATH = Path(__file__).resolve().parent.parent / "learn" / "corrections.json"

load_dotenv()

# ── 자동 Vision 재처리 안전 스위치 ─────────────────────────────────────
# False = 손상 영역에 플레이스홀더만 삽입 (기본)
# True  = Claude Vision으로 전체 재처리 (환각 위험 — 안전장치 후 사용)
ENABLE_VISION_FALLBACK = False

# ── 손상 감지 패턴 ────────────────────────────────────────────────────
_KOREAN = re.compile("[가-힣]")

# Mathpix가 OCR 포기하고 이미지로 대체한 영역 — 한 줄 통째로 잡는다
_MATHPIX_IMG_LINE = re.compile(r"^.*!\[\]\(https://cdn\.mathpix\.com/[^\n]*$", re.MULTILINE)
_MATHPIX_IMG = re.compile(r"!\[\]\(https://cdn\.mathpix\.com/")

# 인라인 수식 안 한글: $...[가-힣]...$
_INLINE_MATH = re.compile(r"(?<!\$)\$(?!\$)((?:[^\$\n])+?)(?<!\$)\$(?!\$)")

# 디스플레이 수식 블록: $$...$$
_DISPLAY_MATH = re.compile(r"\$\$([\s\S]+?)\$\$")

# 빈 함수 정의: $f(x)=$, $y=  $ 같은 형태 (= 뒤가 공백뿐, 최소 1자 이상 내용 필수)
# $=$ (등호 단독) 는 제외 — [^$\n]+? 로 1자 이상 내용 요구
_EMPTY_DEF = re.compile(r"\$[^$\n]+?=\s*\$")

# 페이지 번호 추적용 — Mathpix는 페이지 경계에 "\page{N}" 또는 "---"를 넣기도 함
_PAGE_MARKER = re.compile(r"\\page\{(\d+)\}")

# ── 플레이스홀더 ──────────────────────────────────────────────────────
# 두 가지 형태:
#  - 인라인 마커: 짧고 본문 중간에 박힐 수 있는 형태
#  - 블록 마커:   한 줄 단독, 어느 문항에서 손상이 났는지 표시
# 본문 누더기를 막기 위해, 한 줄에 인라인 마커가 3개 이상 모이거나
# 블록 마커가 연달아 나오면 한 줄 블록 마커로 통합한다.
_PLACEHOLDER_INLINE     = "【★ 확인 필요】"
_PLACEHOLDER_BLOCK_BASE = "【★ 본문 손상 — 원본 PDF"
_PLACEHOLDER_KEY        = "【★"  # 인라인·블록 공통 접두

# (호환용 별칭 — 외부 코드가 import해서 쓰던 상수 유지)
_PLACEHOLDER_NO_PAGE   = "【★ 본문 손상 — 원본 PDF 참조】"
_PLACEHOLDER_WITH_PAGE = "【★ 본문 손상 — 원본 PDF p.{page} 참조】"

# ── 문항 경계 (filter 결과에서 손상 카운트를 매칭하기 위함) ─────────────
# 선택형 N. 또는 N． (전각). 뒤에 공백 + 비숫자(소수점 "1.5" 차단) 또는
# 공백 없이 한글/영문이 바로 와도 매치 (예: "16．남학생...")
_ITEM_BOUNDARY = re.compile(
    r"^(\d{1,2})[.．](?:\s+|(?=[^\d\s]))",
    re.MULTILINE,
)

# 서술형: "서술형 1", "서술형문항 1" + OCR 손상형 "서습/서슴/서술형/서술헝/문항/문향" 등
_ESSAY_BOUNDARY = re.compile(
    r"^#{0,3}\s*서.{0,1}[형헝]\s*(?:문.{0,1}\s*)?(\d+)",
    re.MULTILINE,
)

# ── 브루털 본문 교체 패턴 ──────────────────────────────────────────────
_CIRCLED_ANY   = re.compile(r"[①②③④⑤]")
_SCORE_PATTERN = re.compile(r"\[\d+\.?\d*점\]")
_STAR_MARKER   = re.compile(r"【★[^】]*】")


# ── 손상 감지 ─────────────────────────────────────────────────────────

def _has_damage(md: str) -> tuple[bool, list[str]]:
    """
    Mathpix 마크다운에서 손상 패턴을 감지.
    반환: (손상 여부, 감지된 패턴 목록)
    """
    reasons: list[str] = []

    img_matches = _MATHPIX_IMG.findall(md)
    if img_matches:
        reasons.append(f"Mathpix 이미지 대체 {len(img_matches)}건")

    for m in _INLINE_MATH.finditer(md):
        if _KOREAN.search(m.group(1)):
            reasons.append(f"수식 내 한글: {m.group(0)[:50]}")
            break

    for m in _DISPLAY_MATH.finditer(md):
        inner = m.group(1)
        non_ws = re.sub(r"\s", "", inner)
        if non_ws:
            korean_ratio = len(_KOREAN.findall(non_ws)) / len(non_ws)
            if korean_ratio >= 0.3:
                reasons.append(f"디스플레이 수식 한글 비율 {korean_ratio:.0%}")
                break

    empty_defs = _EMPTY_DEF.findall(md)
    if empty_defs:
        reasons.append(f"빈 함수 정의 {len(empty_defs)}건")

    return bool(reasons), reasons


# ── 페이지·문항 인덱스 ───────────────────────────────────────────────

def _build_page_index(md: str) -> list[tuple[int, int]]:
    """마크다운 내 (offset, page_num) 쌍 리스트."""
    return [(m.start(), int(m.group(1))) for m in _PAGE_MARKER.finditer(md)]


def _page_at(offset: int, page_index: list[tuple[int, int]]) -> int | None:
    page = None
    for off, p in page_index:
        if off <= offset:
            page = p
        else:
            break
    return page


def _build_item_index(md: str) -> list[tuple[int, str]]:
    """마크다운 내 (offset, 문항 표시) 쌍 리스트. 예: '5번', '서술형 2'."""
    boundaries: list[tuple[int, str]] = []
    for m in _ITEM_BOUNDARY.finditer(md):
        boundaries.append((m.start(), f"{m.group(1)}번"))
    for m in _ESSAY_BOUNDARY.finditer(md):
        boundaries.append((m.start(), f"서술형 {m.group(1)}"))
    boundaries.sort()
    return boundaries


def _item_at(offset: int, item_index: list[tuple[int, str]]) -> str | None:
    item = None
    for off, key in item_index:
        if off <= offset:
            item = key
        else:
            break
    return item


def _block_placeholder(
    offset: int,
    page_index: list[tuple[int, int]],
    item_index: list[tuple[int, str]],
) -> str:
    """블록 마커 — 페이지·문항 컨텍스트를 가능한 한 포함."""
    page = _page_at(offset, page_index)
    item = _item_at(offset, item_index)
    head = _PLACEHOLDER_BLOCK_BASE
    if page is not None and item is not None:
        return f"{head} p.{page}의 {item} 참조】"
    if item is not None:
        return f"{head}의 {item} 참조】"
    if page is not None:
        return f"{head} p.{page} 참조】"
    return f"{head} 참조】"


# ── 플레이스홀더 삽입 ─────────────────────────────────────────────────
#
# 정책:
#  - Mathpix CDN 이미지(줄 단독), 디스플레이 수식 한글 → 블록 마커
#  - 인라인 수식 한글, 빈 함수 정의(인라인) → 인라인 마커
#
# 사후 정리:
#  - 연속된 블록 마커는 1개로 통합
#  - 한 줄에 인라인 마커 3개 이상이면 줄 전체를 블록 마커로 교체

_BLOCK_RUN = re.compile(
    r"(?:【★ 본문 손상 — [^】]+】\s*\n\s*){2,}",
)


def _consolidate_block_runs(md: str) -> tuple[str, int]:
    """연속된 블록 마커를 첫 번째 1개로 압축. 반환: (md, 압축으로 줄어든 개수)."""
    reduced = 0

    def keep_first(m: re.Match) -> str:
        nonlocal reduced
        block_count = len(re.findall(r"【★ 본문 손상 —", m.group(0)))
        reduced += max(0, block_count - 1)
        first = re.match(r"【★ 본문 손상 — [^】]+】", m.group(0))
        return (first.group(0) if first else "") + "\n"

    return _BLOCK_RUN.sub(keep_first, md), reduced


def _consolidate_messy_lines(
    md: str,
    page_index: list[tuple[int, int]],
    item_index: list[tuple[int, str]],
    threshold: int = 3,
) -> tuple[str, int]:
    """
    한 줄에 인라인 마커가 threshold 개 이상이면 그 줄 전체를 블록 마커로 교체.
    반환: (md, 교체된 줄 수)
    """
    replaced = 0
    out_lines: list[str] = []
    cursor = 0
    for line in md.split("\n"):
        line_len = len(line) + 1  # \n 포함
        n = line.count(_PLACEHOLDER_INLINE)
        if n >= threshold:
            out_lines.append(_block_placeholder(cursor, page_index, item_index))
            replaced += 1
        else:
            out_lines.append(line)
        cursor += line_len
    return "\n".join(out_lines), replaced


def _replace_damage_with_placeholders(md: str) -> tuple[str, int]:
    """
    손상 영역마다 플레이스홀더로 치환 후 누더기 정리.
    반환: (치환된 마크다운, 마커 개수)
    """
    page_index = _build_page_index(md)
    item_index_before = _build_item_index(md)

    def sub_img_line(m: re.Match) -> str:
        return _block_placeholder(m.start(), page_index, item_index_before)

    md = _MATHPIX_IMG_LINE.sub(sub_img_line, md)

    def sub_inline(m: re.Match) -> str:
        if _KOREAN.search(m.group(1)):
            return _PLACEHOLDER_INLINE
        return m.group(0)

    md = _INLINE_MATH.sub(sub_inline, md)

    def sub_display(m: re.Match) -> str:
        inner = m.group(1)
        non_ws = re.sub(r"\s", "", inner)
        if non_ws and len(_KOREAN.findall(non_ws)) / len(non_ws) >= 0.3:
            return _block_placeholder(m.start(), page_index, item_index_before)
        return m.group(0)

    md = _DISPLAY_MATH.sub(sub_display, md)
    md = _EMPTY_DEF.sub(lambda _m: _PLACEHOLDER_INLINE, md)

    # 사후 정리: 손상 치환으로 문항 경계가 일부 깨질 수 있어 인덱스 재계산
    item_index_after = _build_item_index(md)
    md, messy_lines = _consolidate_messy_lines(md, page_index, item_index_after)
    md, block_dups  = _consolidate_block_runs(md)

    total = md.count(_PLACEHOLDER_INLINE) + md.count(_PLACEHOLDER_BLOCK_BASE)
    if messy_lines or block_dups:
        print(
            f"  [fallback] 누더기 줄 통합 {messy_lines}건, "
            f"중복 블록 통합 {block_dups}건"
        )
    return md, total


# ── Claude Vision 재처리 (비활성, 환각 안전장치 추가 후 재사용 예정) ───

_VISION_PROMPT = """\
이 수학 시험지를 마크다운으로 변환해줘.

[추출 규칙]
- 인쇄된 본문만 추출. 학생 손글씨 풀이·마킹은 완전히 무시.
- 모든 수식은 LaTeX: 인라인은 $...$, 디스플레이(별도 줄)는 $$...$$
- 문제 번호(1. 2. 3. ...) 유지
- 선택지 ①②③④⑤ 유지 (인쇄된 것만)
- 배점 [N점] 유지
- 도형·그래프가 있으면 [그림] 으로 표시
- 학교명·시험 정보·저작권 문구 유지
- 표는 마크다운 테이블로

결과는 마크다운만, 설명·주석 없이.\
"""

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 8192
_COST_PER_M_INPUT = 3.0
_COST_PER_M_OUTPUT = 15.0


def _vision_reocr(pdf_path: Path) -> tuple[str, float]:
    """
    [비활성] PDF 전체를 Claude Vision으로 재처리.
    Why: 수식 환각 사례 (y=√a → y=√6, nroot126 → nroot46) 발견.
    재활성화 전 환각 검사 단계가 필요.
    """
    pdf_bytes = pdf_path.read_bytes()
    b64 = base64.standard_b64encode(pdf_bytes).decode()

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    t0 = time.time()

    resp = client.messages.create(
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": _VISION_PROMPT},
                ],
            }
        ],
    )

    elapsed = time.time() - t0
    in_tok = resp.usage.input_tokens
    out_tok = resp.usage.output_tokens
    cost = (in_tok * _COST_PER_M_INPUT + out_tok * _COST_PER_M_OUTPUT) / 1_000_000

    print(
        f"  [fallback] Vision 완료: {elapsed:.1f}s  "
        f"입력 {in_tok:,}tok / 출력 {out_tok:,}tok  "
        f"비용 ${cost:.4f} (≈₩{cost * 1400:.0f})"
    )
    return resp.content[0].text, cost


# ── 문항 단위 강제 보존 ───────────────────────────────────────────────

def _split_by_items(md: str) -> list[tuple[str, int, int]]:
    """
    마크다운을 문항 단위로 분할.
    반환: [(item_key, start_offset, end_offset), ...]
    item_key 예: "선택형:5", "서술형:2", "헤더" (첫 문항 이전)
    """
    boundaries: list[tuple[int, str]] = [(0, "헤더")]
    for m in _ITEM_BOUNDARY.finditer(md):
        boundaries.append((m.start(), f"선택형:{m.group(1)}"))
    for m in _ESSAY_BOUNDARY.finditer(md):
        boundaries.append((m.start(), f"서술형:{m.group(1)}"))

    boundaries.sort()
    result: list[tuple[str, int, int]] = []
    for i, (start, key) in enumerate(boundaries):
        end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(md)
        result.append((key, start, end))
    return result


def _count_damage_per_item(md: str) -> dict[str, int]:
    """문항 단위로 손상 패턴 개수를 카운트."""
    items = _split_by_items(md)
    counts: dict[str, int] = {}

    for key, start, end in items:
        segment = md[start:end]
        damage = 0
        damage += len(_MATHPIX_IMG.findall(segment))
        damage += len(_EMPTY_DEF.findall(segment))
        for m in _INLINE_MATH.finditer(segment):
            if _KOREAN.search(m.group(1)):
                damage += 1
        for m in _DISPLAY_MATH.finditer(segment):
            inner = m.group(1)
            non_ws = re.sub(r"\s", "", inner)
            if non_ws and len(_KOREAN.findall(non_ws)) / len(non_ws) >= 0.3:
                damage += 1
        if damage:
            counts[key] = damage
    return counts


def _count_placeholders_per_item(md: str) -> dict[str, int]:
    """문항 단위로 [★ ...] 플레이스홀더(인라인+블록) 개수를 카운트."""
    items = _split_by_items(md)
    counts: dict[str, int] = {}
    for key, start, end in items:
        segment = md[start:end]
        n = segment.count(_PLACEHOLDER_KEY)
        if n:
            counts[key] = n
    return counts


def brutal_replace_bodies(md: str) -> tuple[str, int]:
    """
    ★ 마커가 있는 문항의 본문을 단일 블록 마커로 교체 (Option A 브루털).

    보존: 문항 번호, [N점], ①②③④⑤ 선택지
    교체: 본문 전체 → 단일 블록 마커

    반환: (수정된 마크다운, 교체된 문항 수)
    """
    items = _split_by_items(md)
    page_index = _build_page_index(md)
    item_index = _build_item_index(md)

    replacements: list[tuple[int, int, str]] = []

    for key, start, end in items:
        segment = md[start:end]
        if _PLACEHOLDER_KEY not in segment:
            continue
        if key == "헤더":
            continue

        is_essay = key.startswith("서술형:")
        item_num = key.split(":")[1]

        score_m = _SCORE_PATTERN.search(segment)
        score_str = (" " + score_m.group(0)) if score_m else ""

        new_marker = _block_placeholder(start, page_index, item_index)

        if is_essay:
            first_nl = segment.find("\n")
            first_line = segment[:first_nl] if first_nl != -1 else segment
            first_line_clean = _SCORE_PATTERN.sub("", first_line).rstrip()
            new_seg = f"{first_line_clean}\n{new_marker}{score_str}\n"
        else:
            choices_m = _CIRCLED_ANY.search(segment)
            if choices_m:
                line_start = segment.rfind("\n", 0, choices_m.start())
                choices_raw = segment[line_start + 1 if line_start >= 0 else choices_m.start():]
                choices_clean = _STAR_MARKER.sub("", choices_raw).strip()
                new_seg = f"{item_num}. {new_marker}{score_str}\n{choices_clean}\n"
            else:
                new_seg = f"{item_num}. {new_marker}{score_str}\n"

        replacements.append((start, end, new_seg))

    if not replacements:
        return md, 0

    replacements.sort(key=lambda x: x[0], reverse=True)
    result = md
    for s, e, new_text in replacements:
        result = result[:s] + new_text + result[e:]

    return result, len(replacements)


def reinforce_placeholders(filtered_md: str, raw_md: str) -> tuple[str, int]:
    """
    필터 결과에서 ★ 마커가 raw 대비 누락된 문항을 찾아 부족분을 강제 재삽입.

    재삽입은 항상 블록 마커로 들어가며, 문항 블록 끝(다음 문항 시작 직전)에
    1개로 통합 삽입한다. "어느 문항에 OCR 실패가 있었는가" 정보를 100% 보존.

    추가로, 한 문장(마침표/물음표/느낌표 단위)에 마커가 3개 이상 끼어
    누더기처럼 보이면 그 문장 전체를 한 줄짜리 블록 마커로 교체한다.

    반환: (보강된 마크다운, 재삽입 횟수)
    """
    expected = _count_damage_per_item(raw_md)
    actual   = _count_placeholders_per_item(filtered_md)

    items = _split_by_items(filtered_md)
    item_lookup = {key: (start, end) for key, start, end in items}

    page_index = _build_page_index(filtered_md)
    item_index = _build_item_index(filtered_md)

    insertions: list[tuple[int, str]] = []
    total_inserted = 0
    for key, expected_count in expected.items():
        have = actual.get(key, 0)
        missing = expected_count - have
        if missing <= 0:
            continue
        if key not in item_lookup:
            continue
        _, end = item_lookup[key]
        marker = _block_placeholder(end - 1, page_index, item_index)
        # 같은 문항에 여러 손상이 있더라도 한 줄로 통합 (누더기 방지)
        block = "\n" + marker + "\n"
        insertions.append((end, block))
        total_inserted += 1

    if insertions:
        insertions.sort(reverse=True)
        result = filtered_md
        for offset, block in insertions:
            result = result[:offset] + block + result[offset:]
    else:
        result = filtered_md

    # 재삽입 후에도 연속 블록이 생겼다면 한 번 더 정리
    result, _ = _consolidate_block_runs(result)

    # ── 누더기 문장 통째 교체 ────────────────────────────────────────
    # 한 문장(. ! ? 단위)에 「【★ ...】」 마커가 3개 이상이면 그 문장 전체를
    # 「【★ 본문 손상 — 원본 PDF의 문제 N번 참조】」로 교체.
    # 문제 번호는 _build_item_index 로 추적, 못 찾으면 "해당 문제"로 대체.
    page_index2 = _build_page_index(result)
    item_index2 = _build_item_index(result)
    sentence_re = re.compile(r"[^.!?\n]*[.!?]")
    parts: list[str] = []
    last_end = 0
    for m in sentence_re.finditer(result):
        parts.append(result[last_end:m.start()])
        sentence = m.group(0)
        if sentence.count(_PLACEHOLDER_KEY) >= 3:
            page = _page_at(m.start(), page_index2)
            item = _item_at(m.start(), item_index2)
            item_part = f"문제 {item}" if item else "해당 문제"
            head = _PLACEHOLDER_BLOCK_BASE
            if page is not None:
                parts.append(f"{head} p.{page}의 {item_part} 참조】")
            else:
                parts.append(f"{head}의 {item_part} 참조】")
        else:
            parts.append(sentence)
        last_end = m.end()
    parts.append(result[last_end:])
    result = "".join(parts)

    result, brutal_count = brutal_replace_bodies(result)
    if brutal_count:
        print(f"  [brutal] 손상 문항 본문 교체: {brutal_count}건")

    return result, total_inserted


# ── 공개 API ──────────────────────────────────────────────────────────

def apply_fallback(md: str, pdf_path: Path) -> str:
    """
    Mathpix OCR 마크다운에 손상 패턴이 있으면 처리.

    기본 (ENABLE_VISION_FALLBACK = False):
        손상 영역마다 [★ OCR 실패 영역 — ...] 플레이스홀더 삽입.
        환각 위험 없음. 사람이 PDF 원본 보고 직접 채워야 함.

    Vision 모드 (ENABLE_VISION_FALLBACK = True):
        Claude Vision으로 PDF 전체 재처리.
        현재 환각 사례 미해결 — 안전장치 후 사용.

    사용 예 (pdf_to_text.py에 한 줄 삽입):
        md = apply_fallback(md, pdf_path)
    """
    # ── 1단계: 승인된 교정 사전 적용 ────────────────────────────────────
    if _CORRECTIONS_PATH.exists():
        md, corr_log = apply_corrections(md, _CORRECTIONS_PATH, domain="markdown")
        applied = [e for e in corr_log if e.get("count", 0) > 0]
        if applied:
            print(f"  [corrections] {len(applied)}건 적용:")
            for e in applied:
                print(f"    [{e['old']}]→[{e['new']}] {e['count']}회")
        else:
            print("  [corrections] 해당 교정 없음")

    # ── 2단계: 레이아웃 필터 (결재선·페이지메타·알파벳 선택지) ─────────────
    md, layout_log = apply_layout_filter(md)
    lf_hits = [e for e in layout_log]
    if lf_hits:
        print(f"  [layout_filter] {len(lf_hits)}종 필터 적용:")
        for e in lf_hits:
            note = f" — {e['note']}" if "note" in e else ""
            print(f"    {e['filter']}: {e['count']}건{note}")
    else:
        print("  [layout_filter] 해당 없음")

    # ── 3단계: Vision Stage A (복잡 레이아웃 감지 시만) ─────────────────
    md, va_log, va_cost = run_vision_stage_a(md, pdf_path)
    va_actions = [e for e in va_log if e.get("action") not in ("skipped",)]
    if va_actions:
        print(f"  [vision_a] 처리 결과:")
        for e in va_actions:
            print(f"    {e}")

    # ── 4단계: 문서별 알려진 OCR 오류 교정 ──────────────────────────────
    # 광주고 14번: g(4)= 를 Mathpix가 g(4)\neq 로 오인
    md = re.sub(r'g\(4\)\s*\\neq', 'g(4)=', md)

    damaged, reasons = _has_damage(md)

    if not damaged:
        print("  [fallback] 손상 패턴 없음 — 원본 유지")
        return md

    print(f"  [fallback] 손상 감지: {'; '.join(reasons)}")

    if ENABLE_VISION_FALLBACK:
        print(f"  [fallback] Claude Vision으로 전체 재처리: {pdf_path.name}")
        result, _ = _vision_reocr(pdf_path)
        return result

    replaced, count = _replace_damage_with_placeholders(md)
    inline_n = replaced.count(_PLACEHOLDER_INLINE)
    block_n  = replaced.count(_PLACEHOLDER_BLOCK_BASE)
    print(
        f"  [fallback] 마커 삽입: 블록 {block_n}건 + 인라인 {inline_n}건 "
        f"(총 {count}건, Vision 비활성)"
    )
    return replaced
