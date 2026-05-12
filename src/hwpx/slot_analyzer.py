"""
HWPX XML → 문항별 슬롯 그룹화

구조 파악 (확통 경신여고 분석 기준):
  TEXT '경신여고' → TEXT 'N번' → 본문 TEXT+SLOT 혼재 → TEXT '① '~'⑤ ' + 답지 SLOT
  문제 경계: TEXT 'N번' (정수)
  답지 슬롯 표시: TEXT '① '·'② '·'③ '·'④ '·'⑤ '
"""
import re
from dataclasses import dataclass, field

# ── 정규식 ──────────────────────────────────────────────────────
_TEXT_RE   = re.compile(r'<hp:t[^>]*>([^<]+)</hp:t>')
_SCRIPT_RE = re.compile(r'<hp:script>(.*?)</hp:script>', re.DOTALL)
_TOKEN_RE  = re.compile(r'<hp:t[^>]*>([^<]+)</hp:t>|<hp:script>(.*?)</hp:script>', re.DOTALL)

# 원문자 답지 마커 ① ② ③ ④ ⑤
_CIRCLE_NUMS = {'① ', '② ', '③ ', '④ ', '⑤ '}


@dataclass
class SlotInfo:
    idx: int         # 전체 슬롯에서의 1-based 인덱스
    content: str     # 현재 내용
    is_answer: bool  # True: 답지 슬롯 (앞에 원문자 마커)


@dataclass
class SlotGroup:
    problem: int               # 문항 번호 (1-22)
    content_slots: list[SlotInfo] = field(default_factory=list)  # 본문 슬롯
    answer_slots: list[SlotInfo]  = field(default_factory=list)  # 답지 슬롯

    @property
    def all_slots(self) -> list[SlotInfo]:
        return self.content_slots + self.answer_slots

    def total(self) -> int:
        return len(self.content_slots) + len(self.answer_slots)


# ── 공개 API ─────────────────────────────────────────────────────

def analyze_slots(xml: str) -> list[SlotGroup]:
    """
    section0.xml 원문에서 문항별 SlotGroup 리스트를 반환한다.
    """
    groups: list[SlotGroup] = []
    current_group: SlotGroup | None = None
    slot_idx = 0
    next_is_answer = False  # 직전 TEXT가 원문자 마커였는지

    for m in _TOKEN_RE.finditer(xml):
        txt, scr = m.group(1), m.group(2)

        if txt is not None:
            txt_stripped = txt.strip()

            # 문항 경계: 'N번' 패턴
            prob_m = re.fullmatch(r'(\d+)번', txt_stripped)
            if prob_m:
                current_group = SlotGroup(problem=int(prob_m.group(1)))
                groups.append(current_group)
                next_is_answer = False
                continue

            # 답지 마커: ① ~ ⑤
            if txt in _CIRCLE_NUMS or txt_stripped in {s.strip() for s in _CIRCLE_NUMS}:
                next_is_answer = True
            else:
                next_is_answer = False

        else:
            # hp:script 슬롯
            slot_idx += 1
            if current_group is not None:
                info = SlotInfo(idx=slot_idx, content=scr, is_answer=next_is_answer)
                if next_is_answer:
                    current_group.answer_slots.append(info)
                else:
                    current_group.content_slots.append(info)
            next_is_answer = False

    return groups


def build_slot_map(groups: list[SlotGroup]) -> dict[int, SlotGroup]:
    """문항 번호 → SlotGroup 사전."""
    return {g.problem: g for g in groups}
