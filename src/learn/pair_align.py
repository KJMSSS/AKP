"""
PDF raw 마크다운 + HWPX gold → 문제별 정합 쌍 생성.

사용 흐름:
  1. PDF 마크다운을 문제 번호로 분할 (raw)
  2. HWPX를 hwpx_reader로 파싱 (gold)
  3. 선택형은 번호 기준 정합, 서술형은 순서 기준 정합
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.learn.hwpx_reader import ProblemData, read_hwpx

# ── PDF 마크다운 분할 ─────────────────────────────────────────────

# "N. 내용" 또는 "N．내용" 형태 선택형 경계 (N = 1~30)
# 줄 시작 + 번호 + 마침표(전/반각) + 공백* + 내용 있음 (줄 끝이 아닌 경우)
_CHOICE_SPLIT_RE = re.compile(
    r"(?:^|\n)(\d{1,2})[.．][ \t]*(?![ \t]*\n|[ \t]*$)", re.MULTILINE
)

# 서술형 헤더 (OCR 오타 포함)
# 정상: ## 서술형 1, ## 서술형무항 1
# OCR오타: ## 서명형무망 1, ## 서명면항 2
_ESSAY_HEADER_RE = re.compile(
    r"(?:^|\n)##?\s*서[가-힣]{0,8}\s*(\d+)", re.MULTILINE
)

_INLINE_EQ_RE  = re.compile(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", re.DOTALL)
_DISPLAY_EQ_RE = re.compile(r"\$\$(.*?)\$\$", re.DOTALL)


# ── 데이터 클래스 ─────────────────────────────────────────────────

@dataclass
class RawProblem:
    num: int
    is_essay: bool
    text: str
    essay_order: int = 0                # 서술형 순서 (1, 2, ...) — 선택형은 0
    inline_eqs: list[str] = field(default_factory=list)
    display_eqs: list[str] = field(default_factory=list)


@dataclass
class AlignedPair:
    num: int                  # gold HWPX 기준 문제 번호
    raw: RawProblem
    gold: ProblemData
    align_note: str = ""      # 정합 방식 메모 ("choice" | "essay_order")

    def diff_summary(self) -> dict:
        raw_eq_cnt  = len(self.raw.inline_eqs) + len(self.raw.display_eqs)
        gold_real   = len(self.gold.real_equations())
        gold_label  = self.gold.label_count()
        return {
            "num": self.num,
            "raw_eq": raw_eq_cnt,
            "gold_eq": gold_real,
            "gold_label": gold_label,
            "eq_diff": gold_real - raw_eq_cnt,
            "gold_img": self.gold.image_count(),
            "gold_score": self.gold.score,
            "gold_choices": len(self.gold.choices),
        }

    def to_record(self, source: str = "") -> dict:
        """JSONL 저장용 딕셔너리. gold_eqs는 라벨 토큰 제외 실수식만."""
        raw_eq_cnt = len(self.raw.inline_eqs) + len(self.raw.display_eqs)
        gold_real  = self.gold.real_equations()
        gold_label = self.gold.label_count()
        return {
            "source": source,
            "num": self.num,
            "kind": "essay" if "서술형" in self.align_note else self.gold.kind,
            "score": self.gold.score,
            "align_note": self.align_note,
            "raw_text": self.raw.text,
            "raw_inline_eqs": self.raw.inline_eqs,
            "raw_display_eqs": self.raw.display_eqs,
            "gold_text": self.gold.to_dict()["text"],
            "gold_eqs": gold_real,
            "gold_img_count": self.gold.image_count(),
            "gold_choices": self.gold.to_dict()["choices"],
            "gold_label_count": gold_label,
            "eq_diff": len(gold_real) - raw_eq_cnt,
        }


# ── PDF 파싱 ────────────────────────────────────────────────────

def _eqs_from_text(text: str) -> tuple[list[str], list[str]]:
    inline  = [m.group(1).strip() for m in _INLINE_EQ_RE.finditer(text)]
    display = [m.group(1).strip() for m in _DISPLAY_EQ_RE.finditer(text)]
    return inline, display


def _extract_raw_problems(md: str) -> list[RawProblem]:
    """마크다운에서 문제별 RawProblem 분리 (선택형 + 서술형)."""
    problems: list[RawProblem] = []

    # 선택형 경계
    choice_spans: list[tuple[int, int, int]] = []
    for m in _CHOICE_SPLIT_RE.finditer(md):
        choice_spans.append((m.start(), m.end(), int(m.group(1))))

    # 서술형 경계 (OCR 오타 포함)
    essay_spans: list[tuple[int, int, int]] = []
    for m in _ESSAY_HEADER_RE.finditer(md):
        essay_spans.append((m.start(), m.end(), int(m.group(1))))

    # 전체 경계 통합 + 위치순 정렬
    all_spans: list[tuple[int, int, int, bool]] = (
        [(s, e, n, False) for s, e, n in choice_spans] +
        [(s, e, n, True)  for s, e, n in essay_spans]
    )
    all_spans.sort(key=lambda x: x[0])

    seen_choice: set[int] = set()
    essay_order_cnt = 0

    for i, (start, end, num, is_essay) in enumerate(all_spans):
        if not is_essay and num in seen_choice:
            continue
        if not is_essay:
            seen_choice.add(num)

        next_start = all_spans[i + 1][0] if i + 1 < len(all_spans) else len(md)
        text = md[end:next_start].strip()
        inline, display = _eqs_from_text(text)

        if is_essay:
            essay_order_cnt += 1
            prob = RawProblem(
                num=num, is_essay=True, text=text,
                essay_order=essay_order_cnt,
                inline_eqs=inline, display_eqs=display,
            )
        else:
            prob = RawProblem(
                num=num, is_essay=False, text=text,
                inline_eqs=inline, display_eqs=display,
            )
        problems.append(prob)

    return problems


# ── 정합 ─────────────────────────────────────────────────────────

def _gold_essays(gold_probs: list[ProblemData]) -> list[ProblemData]:
    """서술형 gold 문제: 보기 없음 + 점수 ≥ 8."""
    return sorted(
        [p for p in gold_probs if len(p.choices) == 0 and (p.score or 0) >= 8.0],
        key=lambda p: p.num,
    )


def align(
    md: str, hwpx_path: Path
) -> tuple[list[AlignedPair], list[int], list[int]]:
    """
    Returns:
      pairs       — 정합된 AlignedPair 리스트 (번호순)
      only_raw    — raw에만 있는 번호 (선택형)
      only_gold   — gold에만 있는 번호 (선택형)
    """
    raw_probs  = _extract_raw_problems(md)
    gold_probs = read_hwpx(hwpx_path)

    # ── 선택형 정합 ─────────────────────────────────────────────
    raw_choice  = {p.num: p for p in raw_probs if not p.is_essay}
    gold_choice = {p.num: p for p in gold_probs if len(p.choices) > 0}

    common = sorted(set(raw_choice) & set(gold_choice))
    pairs: list[AlignedPair] = [
        AlignedPair(num=n, raw=raw_choice[n], gold=gold_choice[n], align_note="choice")
        for n in common
    ]

    only_raw  = sorted(set(raw_choice) - set(gold_choice))
    only_gold = sorted(set(gold_choice) - set(raw_choice))

    # ── 서술형 정합 (순서 기준) ─────────────────────────────────
    raw_essays  = sorted(
        [p for p in raw_probs if p.is_essay], key=lambda p: p.essay_order
    )
    gold_essays = _gold_essays(gold_probs)

    for i, (raw_e, gold_e) in enumerate(zip(raw_essays, gold_essays)):
        pairs.append(
            AlignedPair(
                num=gold_e.num,
                raw=raw_e,
                gold=gold_e,
                align_note=f"서술형_{raw_e.num}",
            )
        )

    pairs.sort(key=lambda p: p.num)
    return pairs, only_raw, only_gold
