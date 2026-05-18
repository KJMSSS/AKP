"""
레이아웃 후처리 필터 — Vision 없이 패턴 기반 노이즈 제거.

처리 유형:
  1. 결재선 테이블 제거 (교과부장/교감/교장 헤더 행 + :--- 구분자 + CDN 이미지 행)
  2. 수식 블록 내 페이지 번호 제거 (& 0 / 239 \\ 류)
  3. 알파벳 선택지 정규화 (b)→② (OCR이 ②를 (b)로 오인)
  4. 크로스블리드 탐지 마킹 (선택지 줄에 다음 문제 텍스트 침투)
  5. y=N, l=N 등 단독 변수 메타 텍스트 블록 탐지
  6. 고아 테이블 구분자 행 제거 (헤더 없는 |---|---| 행)
  7. <br> 태그 → 개행 변환
"""
import re

# ── 결재선 ────────────────────────────────────────────────────────────
_SIGNING_ROW = re.compile(
    r"^\|[^\n]*(교과부장|교\s*감|교\s*장|결\s*재)[^\n]*\|[ \t]*$",
    re.MULTILINE,
)
_MD_SEP_ROW = re.compile(
    r"^\|?[ \t]*:?-{2,}:?[ \t]*(?:\|[ \t]*:?-{2,}:?[ \t]*)*\|?[ \t]*$",
    re.MULTILINE,
)
_CDN_ROW = re.compile(
    r"^\|[^\n]*cdn\.mathpix\.com[^\n]*\|[ \t]*$",
    re.MULTILINE,
)

# ── 수식 블록 내 페이지 번호 ─────────────────────────────────────────
# aligned 환경 안의 "& N / 숫자 \\" 줄 제거
_PAGE_IN_MATH = re.compile(
    r"&[ \t]*\d*[ \t]*/[ \t]*\d{2,3}[ \t]*(?:\\\\)?[ \t]*\n",
)
# 단독 줄 "/N" — 수식 바깥에서도 발생
_PAGE_SLASH_LINE = re.compile(r"^[ \t]*/\d{1,3}[ \t]*$", re.MULTILINE)

# ── 알파벳 선택지 → 원문자 ────────────────────────────────────────────
# (b)/(B) 단독이 줄 시작이거나 공백 뒤에 오는 경우만 대상
# OCR 혼동 패턴: ②→(b)/(B), ③→(c)/(C) 등 — 대소문자 모두 처리
_ALPHA_CHOICE = re.compile(r"(?m)(?:^|(?<=\n))[ \t]*\(([a-eA-E])\)[ \t]")
_ALPHA_MAP = {"a": "①", "b": "②", "c": "③", "d": "④", "e": "⑤"}

# ── 크로스블리드 탐지 ─────────────────────────────────────────────────
# 선택지 숫자 뒤에 한글 문장이 침투한 패턴: "(5) 12 성분의 합은?"
_CHOICE_BLEED = re.compile(
    r"(\([1-5]\)[ \t]+[-\d/\\${}()eExXa-z. ]+)([가-힣]{4,}[^①-⑤\n]*)",
)

# ── 메타 변수 블록 탐지 ──────────────────────────────────────────────
# $$\begin{aligned}\n& y=N \\\n& l=N \\\n...$$ 형태
# 이런 블록은 시험지 오른쪽 여백의 학생 필기나 난수가 수식으로 흡수된 것
_META_VAR_BLOCK = re.compile(
    r"\$\$\n\\begin\{aligned\}\n"
    r"(?:&[ \t]*[a-zA-Z]=\d+[ \t]*(?:\\\\)?\n)+"
    r"\\end\{aligned\}\n\$\$",
)

# ── 손글씨 계산 침투 ─────────────────────────────────────────────────
# "i| $...$" 또는 "ii) $...$" 형태 — 손글씨 번호 + 수식 혼합
_HANDWRITING_CALC = re.compile(
    r"^[i|]+[|\)\.]\s*\$[^\n$]*\$[^\n]*$",
    re.MULTILINE,
)


def _remove_signing_area(md: str) -> tuple[str, int]:
    """결재선 관련 행 제거: 헤더 행 + :--- 행 + CDN 이미지 행."""
    removed = 0
    lines = md.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # 결재선 헤더 행 감지
        if _SIGNING_ROW.match(line):
            removed += 1
            i += 1
            # 뒤이어 나오는 :--- 행과 CDN 행도 제거
            while i < len(lines):
                nxt = lines[i]
                if _MD_SEP_ROW.match(nxt) or _CDN_ROW.match(nxt) or (
                    nxt.startswith("|") and "cdn.mathpix.com" in nxt
                ):
                    removed += 1
                    i += 1
                else:
                    break
        else:
            out.append(line)
            i += 1
    return "\n".join(out), removed


def _remove_page_in_math(md: str) -> tuple[str, int]:
    """수식 블록 내 페이지 번호 제거."""
    result, n = _PAGE_IN_MATH.subn("", md)
    result2, n2 = _PAGE_SLASH_LINE.subn("", result)
    return result2, n + n2


def _fix_alpha_choices(md: str) -> tuple[str, int]:
    """(a)~(e) 알파벳 선택지를 ①~⑤ 원문자로 교체."""
    counter = [0]

    def repl(m: re.Match) -> str:
        letter = m.group(1).lower()
        circle = _ALPHA_MAP.get(letter, f"({letter})")
        counter[0] += 1
        # m.group(0) = "[spaces](letter) " — leading spaces만 보존, '(' 제거
        leading = re.match(r"^[ \t]*", m.group(0)).group(0)
        return leading + circle + " "

    result = _ALPHA_CHOICE.sub(repl, md)
    return result, counter[0]


def _mark_bleed(md: str) -> tuple[str, int]:
    """선택지에 침투한 다음 문제 텍스트를 분리 마킹."""
    counter = [0]

    def repl(m: re.Match) -> str:
        counter[0] += 1
        choice_part = m.group(1).rstrip()
        bleed_part  = m.group(2).strip()
        return f"{choice_part}\n【★ 크로스블리드 — {bleed_part}】"

    result = _CHOICE_BLEED.sub(repl, md)
    return result, counter[0]


def _mark_meta_var_block(md: str) -> tuple[str, int]:
    """y=N / l=N 등 메타 변수 블록을 플레이스홀더로 교체."""
    counter = [0]

    def repl(m: re.Match) -> str:
        counter[0] += 1
        return "【★ 레이아웃 메타 블록 — 원본 PDF 참조】\n"

    result = _META_VAR_BLOCK.sub(repl, md)
    return result, counter[0]


def _remove_stray_table_sep(md: str) -> tuple[str, int]:
    """헤더 행 없는 고아 테이블 구분자 행 제거 (|:?-+:?| 형태, 앞 줄이 |로 시작하지 않는 경우)."""
    lines = md.split("\n")
    out: list[str] = []
    removed = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if _MD_SEP_ROW.match(stripped):
            prev = lines[i - 1].strip() if i > 0 else ""
            if not prev.startswith("|"):
                removed += 1
                continue
        out.append(line)
    return "\n".join(out), removed


def _convert_br_to_newline(md: str) -> tuple[str, int]:
    """<br> / <br/> 태그를 개행으로 교체."""
    result, n = re.subn(r"<br\s*/?>", "\n", md, flags=re.IGNORECASE)
    return result, n


def _mark_handwriting_calc(md: str) -> tuple[str, int]:
    """손글씨 계산 침투 줄을 플레이스홀더로 교체."""
    counter = [0]

    def repl(m: re.Match) -> str:
        counter[0] += 1
        return "【★ 손글씨 계산 침투 — 원본 PDF 참조】"

    result = _HANDWRITING_CALC.sub(repl, md)
    return result, counter[0]


def apply_layout_filter(md: str) -> tuple[str, list[dict]]:
    """
    레이아웃 필터 전체 실행.

    반환: (필터링된 마크다운, 로그 리스트)
    각 로그: {filter, count, note?}
    """
    log: list[dict] = []

    md, n = _remove_signing_area(md)
    if n:
        log.append({"filter": "signing_area", "count": n})

    md, n = _remove_page_in_math(md)
    if n:
        log.append({"filter": "page_in_math", "count": n})

    md, n = _fix_alpha_choices(md)
    if n:
        log.append({"filter": "alpha_choices", "count": n,
                    "note": "(a)~(e) → ①~⑤ 교체"})

    md, n = _mark_bleed(md)
    if n:
        log.append({"filter": "cross_bleed", "count": n,
                    "note": "선택지 내 다음 문제 텍스트 침투 — 수동 확인 필요"})

    md, n = _mark_meta_var_block(md)
    if n:
        log.append({"filter": "meta_var_block", "count": n,
                    "note": "y=N / l=N 메타 블록 → 플레이스홀더"})

    md, n = _mark_handwriting_calc(md)
    if n:
        log.append({"filter": "handwriting_calc", "count": n,
                    "note": "손글씨 계산 침투 → 플레이스홀더"})

    md, n = _remove_stray_table_sep(md)
    if n:
        log.append({"filter": "stray_table_sep", "count": n,
                    "note": "헤더 없는 구분자 행 제거"})

    md, n = _convert_br_to_newline(md)
    if n:
        log.append({"filter": "br_to_newline", "count": n,
                    "note": "<br> → 개행"})

    return md, log
