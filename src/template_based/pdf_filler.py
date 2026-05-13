"""
PDF 토큰 ↔ HWPX 슬롯 매칭 + 채우기

전략 B: 문항 단위 그룹핑 후 타입별 매칭
  - 답지 슬롯  ← 답지 토큰 (answer_num 위치 대응)
  - 본문 슬롯  ← 수식·수량 토큰 (내용 기반 정확 매칭만)
"""
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from src.template_based.builder import _extract_zip, _pack_zip, _xml_escape
from src.template_based.change_log import ChangeRecord
from src.template_based.slot_analyzer import SlotGroup, SlotInfo, analyze_slots, build_slot_map
from src.template_based.pdf_parser import PdfToken, ProblemTokens, parse_pdf_markdown

_SECTION = "Contents/section0.xml"
_SCRIPT_RE  = re.compile(r'<hp:script>(.*?)</hp:script>', re.DOTALL)
_EQ_OPEN_RE = re.compile(r'<hp:equation\b([^>]*?)>')


# ── 정규화 (내용 비교용) ─────────────────────────────────────────

def _norm(s: str) -> str:
    """비교용 정규화: HWP/LaTeX 표기 차이를 흡수해 동등한 수식을 같게 만든다."""
    s = s.replace('&gt;', '>').replace('&lt;', '<').replace('&amp;', '&')
    s = s.replace('`', '')
    s = re.sub(r'\{([^{}\s]+)\}', r'\1', s)   # {x} → x (단일 토큰 중괄호 제거)
    s = re.sub(r',+\s*', ', ', s)              # ",, " → ", "
    s = re.sub(r'\s+', ' ', s).strip().lower()
    return s


def _confidence(original: str, applied: str) -> float:
    """
    변경 신뢰도. 정규화 후 동일하면 1.0 (형식 차이), 다르면 0.3 (내용 변경).
    Mathpix per-formula confidence 연동 시 0.3 → 실제 값으로 교체 예정.
    """
    return 1.0 if _norm(original) == _norm(applied) else 0.3


# ── 결과 보고 ────────────────────────────────────────────────────

@dataclass
class FillReport:
    total_slots: int
    filled: int
    answer_filled: int
    content_filled: int
    skipped: int
    problem_stats: dict[int, dict]   # prob_num → {filled, total, answer, content}
    changes: list[ChangeRecord] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return self.filled / self.total_slots if self.total_slots else 0.0


# ── 매칭 로직 ────────────────────────────────────────────────────

def _match_slots(
    group: SlotGroup,
    ptokens: ProblemTokens,
    *,
    min_confidence: float = 0.0,
) -> tuple[dict[int, str], list[ChangeRecord]]:
    """
    하나의 문항에 대해 슬롯 인덱스 → 새 내용 사전 + ChangeRecord 목록을 반환한다.
    min_confidence 미만인 변경은 replacements에서 제외된다(원본 유지).
    """
    replacements: dict[int, str] = {}
    records: list[ChangeRecord] = []

    # ── 1. 답지 슬롯 (answer_num 위치 기반) ─────────────────────
    answer_tokens = ptokens.answers  # sorted by answer_num
    for i, slot in enumerate(group.answer_slots):
        target_num = i + 1
        tok = next((t for t in answer_tokens if t.answer_num == target_num), None)
        if tok is None:
            continue
        if tok.content == slot.content:  # 동일 값 — 변경 불필요
            continue
        conf = _confidence(slot.content, tok.content)
        if conf < min_confidence:
            continue
        replacements[slot.idx] = tok.content
        records.append(ChangeRecord(
            slot_idx=slot.idx,
            problem_num=group.problem,
            slot_kind='answer',
            answer_num=target_num,
            original=slot.content,
            applied=tok.content,
            confidence=conf,
            is_suspicious=conf < 1.0,
        ))

    # ── 2. 본문 슬롯 (내용 기반 정확 매칭) ──────────────────────
    content_repl = _content_match(group.content_slots, ptokens.non_answer_tokens)
    for slot in group.content_slots:
        new_val = content_repl.get(slot.idx)
        if new_val is None:
            continue
        if new_val == slot.content:          # 동일 값 — 변경 불필요
            continue
        conf = _confidence(slot.content, new_val)
        if conf < min_confidence:
            continue
        replacements[slot.idx] = new_val
        records.append(ChangeRecord(
            slot_idx=slot.idx,
            problem_num=group.problem,
            slot_kind='content',
            answer_num=0,
            original=slot.content,
            applied=new_val,
            confidence=conf,
            is_suspicious=conf < 1.0,
        ))

    return replacements, records


def _content_match(
    slots: list[SlotInfo],
    tokens: list[PdfToken],
) -> dict[int, str]:
    """
    내용 기반 매칭 (정확 매칭 전용 — 순서 폴백 없음).
    _norm 후 동일한 슬롯만 교체, 미매칭은 원본 유지.
    """
    replacements: dict[int, str] = {}
    used: set[int] = set()

    for slot in slots:
        norm_slot = _norm(slot.content)
        for ti, tok in enumerate(tokens):
            if ti in used:
                continue
            if _norm(tok.content) == norm_slot:
                replacements[slot.idx] = tok.content
                used.add(ti)
                break

    return replacements


# ── XML 치환 + 하이라이트 ─────────────────────────────────────────

def _apply_replacements(xml: str, replacements: dict[int, str]) -> str:
    """슬롯 인덱스 → 새 내용 사전을 XML에 적용한다."""
    slot_idx = 0

    def replacer(m: re.Match) -> str:
        nonlocal slot_idx
        slot_idx += 1
        if slot_idx in replacements:
            return f'<hp:script>{_xml_escape(replacements[slot_idx])}</hp:script>'
        return m.group()

    return _SCRIPT_RE.sub(replacer, xml)


def _highlight_equations(
    xml: str,
    slot_color_map: dict[int, str],
) -> str:
    """
    slot_color_map: slot_idx → 색상코드 (#RRGGBB)
    해당 hp:equation 의 textColor 속성을 변경한다.
    """
    slot_idx = 0

    def replacer(m: re.Match) -> str:
        nonlocal slot_idx
        slot_idx += 1
        if slot_idx not in slot_color_map:
            return m.group()
        color = slot_color_map[slot_idx]
        attrs = m.group(1)
        if 'textColor=' in attrs:
            attrs = re.sub(r'textColor="#[0-9a-fA-F]+"', f'textColor="{color}"', attrs, count=1)
        else:
            attrs += f' textColor="{color}"'
        return f'<hp:equation{attrs}>'

    return _EQ_OPEN_RE.sub(replacer, xml)


def remove_highlights(xml: str, default_color: str = '#000000') -> str:
    """모든 hp:equation의 textColor를 기본값으로 되돌린다."""
    def replacer(m: re.Match) -> str:
        attrs = m.group(1)
        if 'textColor=' in attrs:
            attrs = re.sub(r'textColor="#[0-9a-fA-F]+"', f'textColor="{default_color}"', attrs, count=1)
        return f'<hp:equation{attrs}>'
    return _EQ_OPEN_RE.sub(replacer, xml)


# ── 공개 API ─────────────────────────────────────────────────────

# 하이라이트 색상
COLOR_SUSPICIOUS = '#FF0000'   # 빨강 — 검수 필요
COLOR_SAFE       = '#0066FF'   # 파랑 — 형식만 변경 (안전)


def fill_hwpx_from_pdf_markdown(
    template_path: Path,
    pdf_markdown: str,
    output_path: Path,
    *,
    verbose: bool = False,
    highlight: bool = False,
    min_confidence: float = 0.0,
) -> FillReport:
    """
    PDF 마크다운 → 템플릿 HWPX 채우기.

    Args:
        template_path:   기존 .hwpx 파일
        pdf_markdown:    Mathpix PDF OCR 마크다운
        output_path:     저장할 .hwpx 경로
        highlight:       True면 변경된 수식에 색상 표시
        min_confidence:  이 값 미만의 신뢰도 변경은 건너뜀 (0.0=모두 적용)

    Returns:
        FillReport (커버리지·변경 기록 포함)
    """
    # 1. 입력 파싱
    files = _extract_zip(template_path)
    if _SECTION not in files:
        raise ValueError(f"section0.xml 없음: {template_path}")

    xml = files[_SECTION].decode("utf-8")
    slot_groups  = analyze_slots(xml)
    pdf_problems = parse_pdf_markdown(pdf_markdown)
    pdf_map      = {p.number: p for p in pdf_problems}

    # 2. 문항별 매칭
    all_replacements: dict[int, str] = {}
    all_records: list[ChangeRecord] = []
    problem_stats: dict[int, dict] = {}

    for group in slot_groups:
        pnum    = group.problem
        ptokens = pdf_map.get(pnum)

        if ptokens is None:
            if verbose:
                print(f"  [{pnum:2d}번] PDF 토큰 없음 — 원본 유지")
            problem_stats[pnum] = {
                'filled': 0, 'total': group.total(),
                'answer': 0, 'content': 0, 'pdf_found': False,
            }
            continue

        repl, records = _match_slots(group, ptokens, min_confidence=min_confidence)
        all_replacements.update(repl)
        all_records.extend(records)

        ans_filled = sum(1 for si in group.answer_slots if si.idx in repl)
        cnt_filled = sum(1 for si in group.content_slots if si.idx in repl)

        if verbose:
            total  = group.total()
            filled = len(repl)
            print(
                f"  [{pnum:2d}번] {filled:2d}/{total:2d}개 매칭  "
                f"(답지 {ans_filled}/{len(group.answer_slots)}  "
                f"본문 {cnt_filled}/{len(group.content_slots)})"
            )

        problem_stats[pnum] = {
            'filled': len(repl), 'total': group.total(),
            'answer': ans_filled, 'content': cnt_filled, 'pdf_found': True,
        }

    # 3. XML 치환
    xml_new = _apply_replacements(xml, all_replacements)

    # 4. 하이라이트 (선택)
    if highlight:
        slot_color_map = {
            rec.slot_idx: (COLOR_SUSPICIOUS if rec.is_suspicious else COLOR_SAFE)
            for rec in all_records
        }
        xml_new = _highlight_equations(xml_new, slot_color_map)

    # 5. 저장
    files[_SECTION] = xml_new.encode("utf-8")
    _pack_zip(output_path, files)

    # 6. 통계 집계
    total_slots  = sum(g.total() for g in slot_groups)
    total_filled = len(all_replacements)
    ans_filled   = sum(v['answer'] for v in problem_stats.values())
    cnt_filled   = sum(v['content'] for v in problem_stats.values())

    return FillReport(
        total_slots=total_slots,
        filled=total_filled,
        answer_filled=ans_filled,
        content_filled=cnt_filled,
        skipped=total_slots - total_filled,
        problem_stats=problem_stats,
        changes=all_records,
    )
