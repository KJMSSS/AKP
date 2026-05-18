"""
자모 분리 감지 및 표시 — OCR이 분리한 자모 문자(ㄱ-ㅣ)를 찾아 마킹.

Unicode 한글 영역:
  완성형 음절: U+AC00~U+D7A3  ([가-힣])
  자모 (낱자): U+3131~U+3163  (ㄱ~ㅣ)

OCR에서 발생하는 두 가지 패턴:
  1. 음절 내부 분리: '핟ㄷ톨' (원본: '하도록'의 일부)
  2. 단독 자모 삽입: '존재한ㄷ다'

이 모듈은 감지 + 마킹만 담당. 실제 조합은 LLM에 위임.
"""
import re

# 독립 자모 문자 범위 (완성형 음절 아닌 낱자 ㄱ-ㅣ)
_JAMO_RE = re.compile(r"[ㄱ-ㅣ]")

# 자모 문자가 2개 이상 연속되거나 한글 음절 사이에 끼어 있는 패턴
_JAMO_BETWEEN_RE = re.compile(r"[가-힣]([ㄱ-ㅣ]+)[가-힣]")


def has_jamo(text: str) -> bool:
    """독립 자모 문자(ㄱ~ㅣ)가 포함된 텍스트인지 확인."""
    return bool(_JAMO_RE.search(text))


def jamo_positions(text: str) -> list[int]:
    """독립 자모 문자의 위치(인덱스) 목록 반환."""
    return [m.start() for m in _JAMO_RE.finditer(text)]


def mark_jamo_for_llm(text: str) -> str:
    """
    자모 분리 부분에 LLM 힌트 주석 삽입.

    예: '존제핟ㄷ톨 히' → '존제핟[자모:ㄷ]톨 히'
    LLM이 컨텍스트를 보고 복원하도록 유도.
    """
    def repl(m: re.Match) -> str:
        return f"[자모:{m.group(0)}]"
    return _JAMO_RE.sub(repl, text)


def strip_jamo_markers(text: str) -> str:
    """mark_jamo_for_llm이 삽입한 [자모:X] 마커 제거."""
    return re.sub(r"\[자모:[^\]]+\]", "", text)
