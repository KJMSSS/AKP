"""
HWPX 쌍 비교 → 교정 후보 추출 + 사전 관리.

사용 흐름:
  1. diff_hwpx(production, gold)     — 두 HWPX 비교, 교정 후보 추출
  2. update_dictionary(corrections)  — 사전(corrections.json)에 누적
  3. extract_text_units(hwpx)        — 단위별 토큰 목록 (진단용)
"""
import difflib
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from src.learn.hwpx_reader import is_label_only

# production HWPX는 N번 마커가 없어 hwpx_reader.read_hwpx()로 파싱 불가.
# 원시 XML 토큰 추출 패턴을 직접 사용.
_TOKEN_RE = re.compile(
    r"<hp:t[^>]*>([^<]+)</hp:t>"
    r"|<hp:script>(.*?)</hp:script>"
    r"|(<hp:pic\b)",
    re.DOTALL,
)
_WORD_RE = re.compile(r"[가-힣]+|[a-zA-Z0-9]+")

# 노이즈 필터 상수
_MAX_NEW_LEN   = 60    # new 값이 이 길이 초과하면 바이너리/구조 노이즈
_MIN_KOREAN    = 2     # 텍스트 교정의 최소 한글 음절 수 (단순 숫자/알파벳만은 제외)
_DIGIT_ONLY_RE = re.compile(r"^\d+$")
_NOISE_RE      = re.compile(r"[A-Za-z0-9]{20,}")  # 20자 이상 연속 영숫자 → base64 등

# ── 헤더 누출 노이즈 필터 (학원장 합의: 2026-05-16) ─────────────
# 양식(타이퍼/수학비서) 헤더가 본문과 align되며 잘못 교정으로 잡히는 케이스를 차단.
# 학원장이 차츰 헤더 통일 진행 예정 → 자동 필터가 점점 강력해짐.

# [신형 헤더] 수학비서 정형 패턴 — 예: (광주)[2024_2_1_a_확통_광덕고]
# 이 형태 자체가 텍스트에 들어가면 확실히 헤더 누출.
_HEADER_LINE_RE = re.compile(r"\(\w+\)\[\d{4}_\d_\d_[ab]_[\w가-힣]+_[가-힣]+\]")

# [학교명] 광주 지역 40개 — 명시 리스트로 매칭 (정규식 \b는 한글 word boundary 못 잡음)
_SCHOOL_NAMES = {
    "광주고", "광덕고", "광주여고", "광주제일고", "고려고", "국제고",
    "금호고", "금호중앙여고", "대광여고", "대동고", "대성여고",
    "동명고", "동성고", "동신여고", "동아여고", "문성고", "보문고",
    "비아고", "빛고을고", "상무고", "상일여고", "서강고", "서석고",
    "석산고", "설월여고", "성덕고", "송원고", "송원여고", "수완고",
    "수피아여고", "숭덕고", "숭일고", "운남고", "인성고", "장덕고",
    "전사고", "정광고", "조대부고", "조대여고", "조선대부고",
    "중앙여고", "지원고", "진흥고", "첨단고", "풍암고", "경신여고",
    "살레시오여고", "살레여고", "명진고",
}

# [지역명] 헤더 안 지역 토큰 (단독 출현 시 노이즈)
_REGION_TOKENS = {"광주", "서울", "경기", "부산", "인천", "대구", "대전", "울산", "세종"}

# [난이도 라벨] 정규식 — 본문에는 거의 안 나오는 단어 (단독 매치)
_LEVEL_LABEL_RE = re.compile(r"^(?:쉬움|보통|중간|어려움|매우 쉬움|매우 어려움)$")

# [과목 약칭] 단독 출현 시 헤더 누출 — 수상/수하 같은 흔한 한글은 단독일 때만
_SUBJECT_TOKEN_RE = re.compile(r"^(?:수상|수하|수1|수2|확통|미적분|기하|공수1|공수2)$")

# [(구) 헤더 단어 리스트] 통일 전 다양한 헤더 표현 — 단독 출현 시 노이즈
_LEGACY_HEADER_WORDS = {
    "지필평가", "수행평가", "중간고사", "기말고사",
    "출처", "학년도", "코드번호", "차수", "회차", "배점", "총점",
    "1차", "2차", "3차",
}

# [메타 부분 매치] 텍스트 어디에 들어가면 헤더 누출 (예: "출처 광주 2024")
_META_TOKEN_RE = re.compile(r"(?:출처|지필평가|학년도|코드번호|학년 학생|차 지필평가)")


def _has_school_name(text: str) -> bool:
    """명시 학교명이 텍스트 안에 포함되어 있는지."""
    return any(name in text for name in _SCHOOL_NAMES)


def _is_single_in(text: str, token_set: set[str]) -> bool:
    """텍스트가 strip 후 단독 토큰이고 해당 집합에 속하는지."""
    return text.strip() in token_set


def _is_header_noise(old: str, new: str) -> bool:
    """양식 헤더 누출 노이즈. True면 제외."""
    old_s = old.strip()
    new_s = new.strip()
    combined = old_s + " " + new_s

    # 신형 정형 헤더 패턴 (확실)
    if _HEADER_LINE_RE.search(combined):
        return True
    # 학교명 부분 매치 (대부분 헤더 누출)
    if _has_school_name(combined):
        return True
    # 지역명 단독 (광주, 서울 등) — 부분 매치는 false positive 위험 → 단독만
    if _is_single_in(old_s, _REGION_TOKENS) or _is_single_in(new_s, _REGION_TOKENS):
        return True
    # 난이도 라벨 단독
    if _LEVEL_LABEL_RE.match(new_s) or _LEVEL_LABEL_RE.match(old_s):
        return True
    # 과목 약칭 단독
    if _SUBJECT_TOKEN_RE.match(new_s) or _SUBJECT_TOKEN_RE.match(old_s):
        return True
    # (구) 헤더 단어 단독
    if _is_single_in(old_s, _LEGACY_HEADER_WORDS) or _is_single_in(new_s, _LEGACY_HEADER_WORDS):
        return True
    # 메타 부분 매치 (학년도, 출처 등)
    if _META_TOKEN_RE.search(combined):
        return True
    return False


def _is_noise_text(old: str, new: str) -> bool:
    """노이즈 교정 후보 판별. True면 제외."""
    # new가 너무 길면 구조적 차이 또는 바이너리 데이터
    if len(new) > _MAX_NEW_LEN:
        return True
    # 20자 이상 연속 영숫자가 포함되면 base64 등 노이즈
    if _NOISE_RE.search(new) or _NOISE_RE.search(old):
        return True
    # old와 new 모두 순수 숫자만이면 너무 모호 (맥락 없이 숫자 교체)
    if _DIGIT_ONLY_RE.fullmatch(old) and _DIGIT_ONLY_RE.fullmatch(new):
        return True
    # 최소 한 쪽에는 한글이 있어야 의미 있는 교정
    has_korean = bool(re.search(r"[가-힣]", old + new))
    if not has_korean:
        return True
    # 양식 헤더 누출 (학교명/난이도/메타데이터/과목 단독)
    if _is_header_noise(old, new):
        return True
    return False


# ── 데이터 클래스 ─────────────────────────────────────────────────

@dataclass
class TextUnit:
    kind: str       # "text" | "equation" | "image"
    value: str
    problem_num: int = 0   # 알 수 없으면 0
    location: str = ""     # "stem" | "choice_N" | ""


@dataclass
class Correction:
    old: str
    new: str
    context: str = ""
    location: str = ""
    type: str = "text"    # "text" | "equation"


# ── 내부 유틸 ─────────────────────────────────────────────────────

def _extract_all_tokens(hwpx_path: Path) -> tuple[list[str], list[str]]:
    """HWPX XML에서 텍스트·수식 토큰을 순서대로 추출."""
    with zipfile.ZipFile(hwpx_path, "r") as zf:
        xml = zf.read("Contents/section0.xml").decode("utf-8")

    texts: list[str] = []
    eqs:   list[str] = []
    for m in _TOKEN_RE.finditer(xml):
        txt, scr = m.group(1), m.group(2)
        if txt and txt.strip():
            texts.append(txt.strip())
        if scr:
            eqs.append(scr)
    return texts, eqs


def _word_diff(old_text: str, new_text: str) -> list[Correction]:
    """단어 수준 diff → 짧은 교정(1~3 단어) 추출."""
    old_words = _WORD_RE.findall(old_text)
    new_words = _WORD_RE.findall(new_text)

    corrections: list[Correction] = []
    matcher = difflib.SequenceMatcher(None, old_words, new_words, autojunk=False)

    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op != "replace":
            continue
        old_span = old_words[i1:i2]
        new_span = new_words[j1:j2]
        # 짧은 교정만 수집 (구조적 재배열은 제외)
        if not (1 <= len(old_span) <= 3 and 1 <= len(new_span) <= 3):
            continue
        old_str = " ".join(old_span)
        new_str = " ".join(new_span)
        if _is_noise_text(old_str, new_str):
            continue
        ctx_start = max(0, i1 - 3)
        ctx_end   = min(len(old_words), i2 + 3)
        ctx = " ".join(old_words[ctx_start:ctx_end])
        corrections.append(Correction(
            old=old_str,
            new=new_str,
            context=ctx,
            type="text",
        ))
    return corrections


# ── 공개 API ─────────────────────────────────────────────────────

def extract_text_units(hwpx_path: Path) -> list[TextUnit]:
    """HWPX에서 TextUnit 리스트 추출 (진단·검사용)."""
    texts, eqs = _extract_all_tokens(hwpx_path)
    units  = [TextUnit(kind="text",     value=t) for t in texts]
    units += [TextUnit(kind="equation", value=e) for e in eqs]
    return units


def diff_hwpx(
    production_hwpx: Path,
    gold_hwpx: Path,
) -> list[Correction]:
    """두 HWPX 비교 → 교정 후보 리스트.

    텍스트: 전체 문서 단어 수준 diff.
    수식: 라벨 제외 실수식 인덱스 기준 diff.
    """
    prod_texts, prod_eqs = _extract_all_tokens(production_hwpx)
    gold_texts, gold_eqs = _extract_all_tokens(gold_hwpx)

    corrections: list[Correction] = []

    # ── 텍스트 diff ───────────────────────────────────────────────
    prod_full = " ".join(prod_texts)
    gold_full = " ".join(gold_texts)
    corrections += _word_diff(prod_full, gold_full)

    # ── 수식 diff ─────────────────────────────────────────────────
    prod_real = [e for e in prod_eqs if not is_label_only(e)]
    gold_real = [e for e in gold_eqs if not is_label_only(e)]

    eq_matcher = difflib.SequenceMatcher(None, prod_real, gold_real, autojunk=False)
    for op, i1, i2, j1, j2 in eq_matcher.get_opcodes():
        if op == "replace" and i2 - i1 == j2 - j1:
            for k in range(i2 - i1):
                if prod_real[i1 + k] != gold_real[j1 + k]:
                    corrections.append(Correction(
                        old=prod_real[i1 + k],
                        new=gold_real[j1 + k],
                        context="",
                        type="equation",
                    ))

    return corrections


def update_dictionary(
    corrections: list[Correction],
    dict_path: Path,
    school: str = "",
    form: str = "",
) -> dict:
    """교정 후보를 사전에 누적 저장. 반환: 업데이트된 사전 dict.

    같은 (old, type) 키가 이미 있으면 frequency++, schools/forms 추가.
    없으면 새 entry 생성 (approved: false).

    form: "typer" (타이퍼) / "msbiseo" (수학비서) / "" (미상)
    """
    today = str(date.today())

    if dict_path.exists():
        data = json.loads(dict_path.read_text(encoding="utf-8"))
    else:
        data = {"version": "1.0", "updated": today, "entries": []}

    idx: dict[tuple[str, str], dict] = {
        (e["old"], e["type"]): e for e in data["entries"]
    }

    for corr in corrections:
        key = (corr.old, corr.type)
        if key in idx:
            entry = idx[key]
            entry["frequency"] += 1
            if school and school not in entry["schools"]:
                entry["schools"].append(school)
            if form:
                forms = entry.setdefault("forms", [])
                if form not in forms:
                    forms.append(form)
            if corr.context:
                examples = entry.setdefault("context_examples", [])
                snippet = corr.context[:100]
                if snippet not in examples:
                    examples.append(snippet)
        else:
            entry = {
                "old": corr.old,
                "new": corr.new,
                "type": corr.type,
                "frequency": 1,
                "schools": [school] if school else [],
                "forms": [form] if form else [],
                "first_seen": today,
                "approved": False,
                "context_examples": [corr.context[:100]] if corr.context else [],
            }
            idx[key] = entry
            data["entries"].append(entry)

    data["updated"] = today
    dict_path.parent.mkdir(parents=True, exist_ok=True)
    dict_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return data
