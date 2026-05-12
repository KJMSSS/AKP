"""
PDF 마크다운 → 문항별 토큰 그룹화

파싱 대상:
  - 선택형 (1-15번): N. 패턴으로 분리
  - 서술형 (16-22번): 서술형N. 패턴으로 분리 (template 기준 16-22번 매핑)

토큰 종류:
  - formula   : $...$ 인라인 수식 (본문)
  - answer    : (N) 값 형식 답지
  - quantity  : N가지/N개 등 수량 표현에서 추출한 숫자
"""
import re
from dataclasses import dataclass, field
from typing import Any

from src.hwpx.latex_to_hwp import convert as latex_to_hwp

# ── 정규식 패턴 ──────────────────────────────────────────────────

# 선택형 문항 분리: "1. " 또는 전각문자 "1．세" (마침표 뒤 공백 없는 경우 포함)
_PROB_RE = re.compile(r'(?:^|\n)(\d{1,2})[.．]\s*', re.MULTILINE)

# 서술형 분리: "## 서술형3.", "서술형7," 등 다양한 형태
# - ## 헤더 접두사 가능
# - 마침표(.) 또는 쉼표(,) 또는 공백으로 끝날 수 있음
# - 전각문자 포함
_ESSAY_RE = re.compile(
    r'(?:^|\n)(?:##\s*)?서술[형헝]\s*(\d+)\s*[.,，．\s]', re.MULTILINE
)

# 답지: "(1) 42" 또는 전각문자 "（1）42" 형식
_ANSWER_RE = re.compile(
    r'[（(]([1-5])[）)]\s*([^\n（(]+)', re.MULTILINE
)

# 인라인 수식: $...$ ($$...$$는 제외)
_INLINE_RE = re.compile(r'(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)', re.DOTALL)

# 디스플레이 수식: $$...$$
_DISPLAY_RE = re.compile(r'\$\$(.*?)\$\$', re.DOTALL)

# 학생 필기 필터 (array, aligned, gathered 환경)
_STUDENT_WORK_RE = re.compile(r'\\begin\{(array|aligned|gathered|pmatrix)\}')

# 수량 단위 패턴: N가지, N개, N명, N등분, N자루 등
_QUANTITY_RE = re.compile(
    r'(\d+)\s*(?:가지|개|명|등분|자루|장|색|영역|개씩|이하|이상|종류|배|번째|'
    r'초과|미만|자리|팀|쌍|조|번|라운드|층|열|칸|칸씩|면|개짜리)'
)

# 이미지 라인 제거
_IMAGE_RE = re.compile(r'!\[.*?\]\(.*?\)', re.DOTALL)

# 해설/정답 마커 (선택형 답지 이후 내용 제거용)
_HEADER_ANSWER_PAREN_RE = re.compile(
    r'\(([1-9]|[1-9]\d)\)\s*(?:학년|차|면|번|반|학기)', re.MULTILINE
)


@dataclass
class PdfToken:
    kind: str        # 'formula' | 'answer' | 'quantity' | 'display'
    content: str     # HWP script 변환 후 내용
    latex: str       # 원본 LaTeX (formula/display만)
    answer_num: int  # 답지 번호 (answer만, 나머지는 0)


@dataclass
class ProblemTokens:
    number: int          # 1-22 (template 기준 번호)
    is_essay: bool       # 서술형 여부
    tokens: list[PdfToken] = field(default_factory=list)

    @property
    def formulas(self) -> list[PdfToken]:
        return [t for t in self.tokens if t.kind in ('formula', 'display')]

    @property
    def answers(self) -> list[PdfToken]:
        return [t for t in self.tokens if t.kind == 'answer']

    @property
    def quantities(self) -> list[PdfToken]:
        return [t for t in self.tokens if t.kind == 'quantity']

    @property
    def non_answer_tokens(self) -> list[PdfToken]:
        return [t for t in self.tokens if t.kind != 'answer']


# ── 내부 헬퍼 ────────────────────────────────────────────────────

def _is_student_work(latex: str) -> bool:
    return bool(_STUDENT_WORK_RE.search(latex))


def _clean_answer_value(val: str) -> str:
    """답지 값에서 불필요한 내용 제거."""
    val = val.strip()
    # 라텍스 수식이 포함된 경우 그대로 반환
    if '$' in val:
        return val
    # 순수 숫자나 단순 표현
    val = re.split(r'[^0-9\-,\.\s]', val)[0].strip()
    return val or val


def _make_formula_token(latex: str) -> PdfToken:
    return PdfToken(kind='formula', content=latex_to_hwp(latex), latex=latex, answer_num=0)


def _make_quantity_token(num_str: str) -> PdfToken:
    return PdfToken(kind='quantity', content=num_str, latex='', answer_num=0)


def _make_answer_token(num: int, val: str) -> PdfToken:
    """답지 토큰 생성. 값에 수식이 있으면 변환."""
    if '$' in val:
        # $...$ 추출해 변환
        m = _INLINE_RE.search(val)
        if m:
            return PdfToken(
                kind='answer', content=latex_to_hwp(m.group(1).strip()),
                latex=m.group(1).strip(), answer_num=num,
            )
    return PdfToken(kind='answer', content=val.strip(), latex='', answer_num=num)


def _extract_tokens_from_section(text: str, is_essay: bool) -> list[PdfToken]:
    """
    하나의 문항 텍스트에서 토큰 추출.
    선택형: 본문(질문) + 답지 분리
    서술형: 본문 수식만 추출
    """
    # 이미지 라인 제거
    text = _IMAGE_RE.sub('', text)

    tokens: list[PdfToken] = []

    if not is_essay:
        # ── 선택형: 답지 경계 찾기 ─────────────────────────────────
        # "(1) 값" 패턴의 첫 번째 등장 위치를 답지 시작으로 판단
        first_answer = None
        for m in _ANSWER_RE.finditer(text):
            # 헤더 오답 필터 (학년, 차, 면 등 포함된 경우 무시)
            val = m.group(2).strip()
            if re.search(r'학년|학기|차\s*지필|면\s*광주|번\s*지필', val):
                continue
            if first_answer is None:
                first_answer = m.start()
            # 답지 토큰 추가
            clean = _clean_answer_value(val)
            if clean:
                tokens.append(_make_answer_token(int(m.group(1)), clean))

        # 본문 (답지 이전)
        question_text = text[:first_answer] if first_answer else text

        # 1) 인라인 수식 추출
        for m in _INLINE_RE.finditer(question_text):
            latex = m.group(1).strip()
            if latex and not _is_student_work(latex):
                tokens.append(_make_formula_token(latex))

        # 2) 수량 표현 추출
        for m in _QUANTITY_RE.finditer(question_text):
            tokens.append(_make_quantity_token(m.group(1)))

    else:
        # ── 서술형: 본문 수식만 추출 ──────────────────────────────
        # 인라인 수식
        for m in _INLINE_RE.finditer(text):
            latex = m.group(1).strip()
            if latex and not _is_student_work(latex):
                tokens.append(_make_formula_token(latex))
        # 수량 표현
        for m in _QUANTITY_RE.finditer(text):
            tokens.append(_make_quantity_token(m.group(1)))
        # 단독 디스플레이 수식 (학생 필기 제외)
        for m in _DISPLAY_RE.finditer(text):
            latex = m.group(1).strip()
            if latex and not _is_student_work(latex):
                tokens.append(PdfToken(
                    kind='display', content=latex_to_hwp(latex),
                    latex=latex, answer_num=0,
                ))

    # 최종: answer 토큰을 답지 번호 순으로 정렬, 나머지는 추출 순서 유지
    non_answers = [t for t in tokens if t.kind != 'answer']
    answers = sorted([t for t in tokens if t.kind == 'answer'], key=lambda t: t.answer_num)
    return non_answers + answers


# ── 공개 API ─────────────────────────────────────────────────────

def parse_pdf_markdown(md: str) -> list[ProblemTokens]:
    """
    Mathpix PDF 마크다운을 문항별로 파싱한다.

    선택형 (1-15): 문항 번호 'N.' 으로 분리
    서술형 (16-22): '서술형N.' 으로 분리 후 template 번호(+15) 할당

    Returns:
        문항별 ProblemTokens 리스트 (번호 오름차순)
    """
    # 선택형 분리
    problems: list[ProblemTokens] = []
    found_nums: set[int] = set()

    mc_matches = list(_PROB_RE.finditer(md))
    for i, m in enumerate(mc_matches):
        num = int(m.group(1))
        if num > 15 or num in found_nums:
            continue
        found_nums.add(num)
        # 이 문항의 텍스트 범위
        start = m.end()
        end = mc_matches[i + 1].start() if i + 1 < len(mc_matches) else len(md)
        section = md[start:end]

        # 서술형 헤더 이후는 서술형 영역이므로 중단
        essay_header = re.search(r'## 서\s*술\s*형', section)
        if essay_header:
            section = section[:essay_header.start()]

        tokens = _extract_tokens_from_section(section, is_essay=False)
        problems.append(ProblemTokens(number=num, is_essay=False, tokens=tokens))

    # 서술형 분리 (서술형N. 패턴)
    essay_matches = list(_ESSAY_RE.finditer(md))
    for i, m in enumerate(essay_matches):
        essay_num = int(m.group(1))
        template_num = essay_num + 15  # template에서 16-22번
        if template_num in found_nums:
            continue
        found_nums.add(template_num)

        start = m.end()
        end = essay_matches[i + 1].start() if i + 1 < len(essay_matches) else len(md)
        section = md[start:end]

        tokens = _extract_tokens_from_section(section, is_essay=True)
        problems.append(ProblemTokens(number=template_num, is_essay=True, tokens=tokens))

    return sorted(problems, key=lambda p: p.number)
