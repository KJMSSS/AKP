"""
HWPX 파일에서 학습 데이터 추출.

문단 토큰(text / eq / img)을 순회하며 문제 번호 경계로 그룹화.
이미지는 위치만 기록 (Phase 0 — 내용 파싱 X).
"""
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

# ── 정규식 ───────────────────────────────────────────────────────
# 11b 타이퍼 양식: 별도 토큰으로 "N번" 형태
_PROB_NUM_RE = re.compile(r"^\s*(\d{1,2})번\s*$")
# 2024 수학비서 양식: "(지역)[YYYY_x_x_a_과목_학교명 N [N.NN점]" 헤더 토큰 내 번호
_NEW_PROB_NUM_RE = re.compile(r"\(.+?\)\[.+?_[가-힣]+\s+(\d{1,3})\s+\[[\d.]+점\]")
_SCORE_TEXT_RE = re.compile(r"(\d+(?:\.\d+)?)점")
_CIRCLE_RE = re.compile(r"^([①②③④⑤])\s*$")

# 2024 양식 메타 토큰 (본문 추가에서 제외 — 헤더 누출 방지)
_META_TOKEN_PATTERNS = (
    re.compile(r"^\[\s*출처\s*\]$"),
    re.compile(r"^\[\s*해설\s*\]$"),
    re.compile(r"^\[\s*정답\s*\]$"),
    re.compile(r"^[∙∘•◦]+\s*(?:쉬움|보통|중간|어려움)\d*$"),  # 난이도 표시
)

# [정답] 마커 — 모든 양식의 문제 끝을 표시 (99%+ 적중)
_ANSWER_MARK_IN_RE = re.compile(r"\[\s*정답\s*\]")

# 라벨 토큰 판별: 배점(숫자 1~2자리) / 보기 선택지(A~E) / 원숫자(①~⑩)
_LABEL_PAT = re.compile(r"^\s*(?:[A-E]|[0-9]{1,2}|[①-⑩])\s*$")

# XML 토큰 스캐너: text / equation / image
_TOKEN_RE = re.compile(
    r"<hp:t[^>]*>([^<]+)</hp:t>"
    r"|<hp:script>(.*?)</hp:script>"
    r"|(<hp:pic\b)",
    re.DOTALL,
)


@dataclass
class Token:
    kind: str   # "text" | "eq" | "img"
    value: str  # text 내용 또는 HWP 스크립트 ("" for img)


@dataclass
class ChoiceItem:
    bullet: str                           # ① ~ ⑤
    tokens: list[Token] = field(default_factory=list)


@dataclass
class ProblemData:
    num: int
    kind: str = "choice"                  # "choice" | "essay" | "other"
    score: float | None = None
    tokens: list[Token] = field(default_factory=list)
    choices: list[ChoiceItem] = field(default_factory=list)

    # ── 편의 접근자 ──────────────────────────────────────────────
    def text_spans(self) -> list[str]:
        return [t.value for t in self.tokens if t.kind == "text"]

    def equations(self) -> list[str]:
        return [t.value for t in self.tokens if t.kind == "eq"]

    def real_equations(self) -> list[str]:
        """라벨 토큰(배점·보기번호 등) 제외한 실제 수식만 반환."""
        return [t.value for t in self.tokens if t.kind == "eq" and not is_label_only(t.value)]

    def label_count(self) -> int:
        """라벨 토큰 개수."""
        return sum(1 for t in self.tokens if t.kind == "eq" and is_label_only(t.value))

    def image_count(self) -> int:
        return sum(1 for t in self.tokens if t.kind == "img")

    def to_dict(self) -> dict:
        return {
            "num": self.num,
            "kind": self.kind,
            "score": self.score,
            "text": " ".join(t.value for t in self.tokens if t.kind == "text"),
            "eq_count": sum(1 for t in self.tokens if t.kind == "eq"),
            "img_count": self.image_count(),
            "equations": self.equations(),
            "choices": [
                {
                    "bullet": c.bullet,
                    "text": " ".join(t.value for t in c.tokens if t.kind == "text"),
                    "equations": [t.value for t in c.tokens if t.kind == "eq"],
                }
                for c in self.choices
            ],
        }


def is_label_only(hwp_eq: str) -> bool:
    """배점·보기번호 등 비수식 라벨 토큰 판별. (백틱 제거 후 단순 문자/숫자만이면 True)"""
    core = hwp_eq.replace("`", "").strip()
    return bool(_LABEL_PAT.match(core))


# ── 공개 API ─────────────────────────────────────────────────────

def _read_hwpx_by_answer_marker(xml: str) -> list[ProblemData]:
    """Fallback: '[정답]' 토큰으로 문제 분할.

    헤더 양식이 다양해 _PROB_NUM_RE / _NEW_PROB_NUM_RE 모두 실패할 때 사용.
    문제 번호는 등장 순서대로 1, 2, 3, ... 부여.
    """
    problems: list[ProblemData] = []
    cur_tokens: list[Token] = []
    cur_choice: ChoiceItem | None = None
    started = False  # 첫 [정답] 만나기 전 까지는 헤더로 간주, 무시
    seg_count = 0

    def _flush():
        nonlocal cur_tokens, cur_choice, seg_count
        if not cur_tokens and not cur_choice:
            return
        seg_count += 1
        p = ProblemData(num=seg_count, kind="choice")
        p.tokens = cur_tokens
        # dummy ChoiceItem 추가: align()의 gold_choice 조건 (len(p.choices) > 0) 통과를 위해
        # 11b는 fallback 안 타므로 영향 없음
        p.choices = [ChoiceItem(bullet="_fallback_")]
        cur_tokens = []
        cur_choice = None
        problems.append(p)

    for m in _TOKEN_RE.finditer(xml):
        txt, scr, pic = m.group(1), m.group(2), m.group(3)

        if txt is not None:
            txt_stripped = txt.strip()
            # [정답] 마커 → 직전 segment를 문제로 등록, 새 segment 시작
            if _ANSWER_MARK_IN_RE.search(txt_stripped):
                if started:
                    _flush()
                started = True
                continue
            # 첫 [정답] 이전은 헤더로 무시
            if not started:
                continue
            # 메타 토큰 (출처/해설/난이도) 제외
            if any(pat.match(txt_stripped) for pat in _META_TOKEN_PATTERNS):
                continue
            # 보기 마커 ①②③④⑤
            circle_m = _CIRCLE_RE.fullmatch(txt_stripped)
            if circle_m:
                if problems and problems[-1].num == seg_count + 1:
                    pass
                cur_choice = ChoiceItem(bullet=txt_stripped)
                continue
            tok = Token(kind="text", value=txt)
            if cur_choice is not None:
                cur_choice.tokens.append(tok)
            else:
                cur_tokens.append(tok)

        elif scr is not None:
            if not started:
                continue
            tok = Token(kind="eq", value=scr)
            if cur_choice is not None:
                cur_choice.tokens.append(tok)
            else:
                cur_tokens.append(tok)

        elif pic is not None:
            if not started:
                continue
            tok = Token(kind="img", value="")
            if cur_choice is not None:
                cur_choice.tokens.append(tok)
            else:
                cur_tokens.append(tok)

    # 마지막 segment flush
    _flush()
    return problems


def read_hwpx(hwpx_path: Path) -> list[ProblemData]:
    """
    HWPX 파일을 파싱해 문제별 ProblemData 리스트 반환.
    선택형: 'N번' 마커로 경계 구분.
    서술형: 'N번' 마커 없이 이어지는 나머지 (kind='essay').

    Fallback: 'N번' / '(...)[...학교 N [점수]' 마커 모두 실패하면
              '[정답]' 마커로 문제 분할 (헤더 양식 다양 대응).
    """
    with zipfile.ZipFile(hwpx_path, "r") as zf:
        xml = zf.read("Contents/section0.xml").decode("utf-8")

    problems: list[ProblemData] = []
    current: ProblemData | None = None
    current_choice: ChoiceItem | None = None
    seen_nums: set[int] = set()

    for m in _TOKEN_RE.finditer(xml):
        txt, scr, pic = m.group(1), m.group(2), m.group(3)

        if txt is not None:
            txt_stripped = txt.strip()

            # 문제 번호 경계 확인 (11b "N번" 형태)
            prob_m = _PROB_NUM_RE.fullmatch(txt_stripped)
            if prob_m:
                n = int(prob_m.group(1))
                if n not in seen_nums:
                    seen_nums.add(n)
                    current_choice = None
                    current = ProblemData(num=n, kind="choice")
                    problems.append(current)
                    continue

            # 2024 수학비서 양식: 헤더 토큰 안에 문제 번호 + 점수
            new_m = _NEW_PROB_NUM_RE.search(txt_stripped)
            if new_m:
                n = int(new_m.group(1))
                if n not in seen_nums:
                    seen_nums.add(n)
                    current_choice = None
                    current = ProblemData(num=n, kind="choice")
                    # 같은 토큰의 점수도 추출
                    score_m = _SCORE_TEXT_RE.search(txt_stripped)
                    if score_m:
                        try:
                            current.score = float(score_m.group(1))
                        except ValueError:
                            pass
                    problems.append(current)
                    continue

            # 2024 메타 토큰 (출처/해설/정답/난이도 표시) — 본문 추가에서 제외
            if any(pat.match(txt_stripped) for pat in _META_TOKEN_PATTERNS):
                continue

            # 점수 텍스트 — 현재 문제에 기록
            score_m = _SCORE_TEXT_RE.search(txt_stripped)
            if score_m and current:
                try:
                    current.score = float(score_m.group(1))
                except ValueError:
                    pass

            # 원문자 마커 — 보기 경계
            circle_m = _CIRCLE_RE.fullmatch(txt_stripped)
            if circle_m and current:
                current_choice = ChoiceItem(bullet=txt_stripped)
                current.choices.append(current_choice)
                continue

            # 일반 텍스트 — 현재 문제 또는 보기에 추가
            tok = Token(kind="text", value=txt)
            if current_choice is not None:
                current_choice.tokens.append(tok)
            elif current is not None:
                current.tokens.append(tok)

        elif scr is not None:
            tok = Token(kind="eq", value=scr)
            if current_choice is not None:
                current_choice.tokens.append(tok)
            elif current is not None:
                current.tokens.append(tok)

        elif pic is not None:
            tok = Token(kind="img", value="")
            if current_choice is not None:
                current_choice.tokens.append(tok)
            elif current is not None:
                current.tokens.append(tok)

    # Fallback: 두 패턴 모두 실패 (problems 비어있음) → [정답] 마커 기반
    if not problems and "[정답]" in xml:
        problems = _read_hwpx_by_answer_marker(xml)

    return problems


def build_problem_map(problems: list[ProblemData]) -> dict[int, ProblemData]:
    return {p.num: p for p in problems}
