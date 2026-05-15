"""
승인된 교정 항목을 마크다운 텍스트(또는 HWP 수식 스크립트)에 적용.

안전 정책:
  - 긴 패턴 (공백 포함 또는 3자+): 단순 str.replace()
  - 짧은 단일 토큰 (1~2자, 공백 없음): context_examples[0] 기반 앵커 매칭
  - equation 타입: domain="hwp_script" 일 때만 적용 (본문 미적용)
"""
import json
import re
from pathlib import Path

_WORD_RE = re.compile(r"[가-힣a-zA-Z0-9]+")


def _is_safe_pattern(old: str) -> bool:
    """공백 포함이거나 2자 이상이면 단순 replace 안전.
    단일 글자(을, 를, 울 등 입자)만 컨텍스트 앵커 방식 사용."""
    return " " in old or len(old) >= 2


def _context_anchor_replace(
    text: str, old: str, new: str, context: str
) -> tuple[str, int]:
    """
    컨텍스트 앵커 단어를 좌우에 두고 제한적으로 교체.
    old가 context word list에 없으면 0 반환.
    """
    words = _WORD_RE.findall(context)
    try:
        idx = words.index(old)
    except ValueError:
        return text, 0

    before_word = words[idx - 1] if idx > 0 else None
    after_word  = words[idx + 1] if idx + 1 < len(words) else None

    if not (before_word or after_word):
        return text, 0

    counter = [0]

    if before_word and after_word:
        pat = re.compile(
            rf"({re.escape(before_word)}\s+)"
            rf"({re.escape(old)})"
            rf"(\s+{re.escape(after_word)})"
        )
        def repl(m: re.Match) -> str:
            counter[0] += 1
            return m.group(1) + new + m.group(3)
        text = pat.sub(repl, text)

    elif before_word:
        pat = re.compile(
            rf"({re.escape(before_word)}\s+)"
            rf"({re.escape(old)})"
        )
        def repl(m: re.Match) -> str:
            counter[0] += 1
            return m.group(1) + new
        text = pat.sub(repl, text)

    else:  # after_word only
        pat = re.compile(
            rf"({re.escape(old)})"
            rf"(\s+{re.escape(after_word)})"
        )
        def repl(m: re.Match) -> str:
            counter[0] += 1
            return new + m.group(2)
        text = pat.sub(repl, text)

    return text, counter[0]


def apply_corrections(
    text: str,
    dict_path: Path,
    domain: str = "markdown",
) -> tuple[str, list[dict]]:
    """
    approved=True 항목을 text에 적용.

    domain="markdown"   : type="text" 항목만 (파이프라인 기본값)
    domain="hwp_script" : type="equation" 항목만
                          (수식은 컨텍스트 없는 경우 대부분 → 미구현 안내)

    반환: (수정된 text, 적용 로그 리스트)
    각 로그 항목: {old, new, count, method, note?}
    """
    data = json.loads(dict_path.read_text(encoding="utf-8"))

    if domain == "markdown":
        targets = [e for e in data["entries"] if e.get("approved") and e["type"] == "text"]
    elif domain == "hwp_script":
        targets = [e for e in data["entries"] if e.get("approved") and e["type"] == "equation"]
    else:
        targets = [e for e in data["entries"] if e.get("approved")]

    log: list[dict] = []
    result = text

    for entry in targets:
        old = entry["old"]
        new = entry["new"]
        ctx_list = entry.get("context_examples", [])
        ctx = ctx_list[0] if ctx_list else ""

        if domain == "hwp_script":
            # 수식 교정은 컨텍스트 없음 → 위험한 단순 replace 금지
            # 향후 HWPX XML 레벨 통합으로 처리 예정
            log.append({
                "old": old, "new": new, "count": 0,
                "method": "hwp_script", "note": "HWPX 레벨 통합 예정 (미적용)",
            })
            continue

        if _is_safe_pattern(old):
            n = result.count(old)
            result = result.replace(old, new)
            entry_log: dict = {"old": old, "new": new, "count": n, "method": "replace"}
            if n == 0:
                entry_log["note"] = "미발견"
            log.append(entry_log)

        else:
            if not ctx:
                log.append({
                    "old": old, "new": new, "count": 0,
                    "method": "context", "note": "컨텍스트 없음 — 스킵",
                })
                continue
            result, n = _context_anchor_replace(result, old, new, ctx)
            entry_log = {"old": old, "new": new, "count": n, "method": "context"}
            if n == 0:
                entry_log["note"] = "앵커 미매칭"
            log.append(entry_log)

    return result, log


def summarize_log(log: list[dict]) -> str:
    """로그 리스트 → 사람이 읽을 수 있는 요약 문자열."""
    applied = [e for e in log if e.get("count", 0) > 0]
    skipped = [e for e in log if e.get("count", 0) == 0]
    lines = [f"교정 적용: {len(applied)}건 / 미적용: {len(skipped)}건"]
    for e in applied:
        lines.append(f"  [{e['old']}]→[{e['new']}] {e['count']}회 ({e['method']})")
    if skipped:
        lines.append("  미적용 항목:")
        for e in skipped:
            note = e.get("note", "")
            lines.append(f"    [{e['old']}]→[{e['new']}] — {note}")
    return "\n".join(lines)
