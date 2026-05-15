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
_PROB_NUM_RE = re.compile(r"^\s*(\d{1,2})번\s*$")
_SCORE_TEXT_RE = re.compile(r"(\d+(?:\.\d+)?)점")
_CIRCLE_RE = re.compile(r"^([①②③④⑤])\s*$")

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

def read_hwpx(hwpx_path: Path) -> list[ProblemData]:
    """
    HWPX 파일을 파싱해 문제별 ProblemData 리스트 반환.
    선택형: 'N번' 마커로 경계 구분.
    서술형: 'N번' 마커 없이 이어지는 나머지 (kind='essay').
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

            # 문제 번호 경계 확인
            prob_m = _PROB_NUM_RE.fullmatch(txt_stripped)
            if prob_m:
                n = int(prob_m.group(1))
                if n not in seen_nums:
                    seen_nums.add(n)
                    current_choice = None
                    current = ProblemData(num=n, kind="choice")
                    problems.append(current)
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

    return problems


def build_problem_map(problems: list[ProblemData]) -> dict[int, ProblemData]:
    return {p.num: p for p in problems}
