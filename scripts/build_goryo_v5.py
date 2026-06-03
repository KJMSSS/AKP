"""
Cycle 16 — 고려고 v5 빌드 (문제 단위 파이프라인).

파이프라인:
  raw.md
  → [전처리] OCR 패치 + score format 통일
  → [1] parse_problems()
  → [2] normalize_choices()
  → [3] rebuild_markdown()
  → [4] postprocess_markdown()
  → [5] apply_fallback()
  → [6] build_from_markdown()
  → [7] replace_condition_tables() / replace_boilerplate_tables()
  .hwpx
"""
import hashlib
import io
import re
import sys
import time
import traceback
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from src.text_only.problem_segmenter import parse_problems, rebuild_markdown
from src.ocr.choice_normalizer import normalize_choices
from src.text_only.ocr_fallback import apply_fallback
from src.text_only.text_builder import build_from_markdown
from src.text_only.page_extractor import compare_scripts
from src.ocr.llm_postprocess import postprocess_markdown
from src.ocr.cost_guard import CostGuard, CostCapError
from src.common.hwpx_table_inserter import (
    replace_condition_tables,
    replace_boilerplate_tables,
    TableSpec,
    replace_placeholder_with_data_table,
)

ROOT     = Path(__file__).resolve().parent.parent
SRC_DIR  = ROOT / "samples" / "11b"
PROD_DIR = ROOT / "samples" / "11b_production"
LOG_DIR  = ROOT / "log" / "cycle_16"

SOURCE  = "2025_1_1_b_공수1_고려고"
VER     = "v5"

safe     = re.sub(r"[^\w\-]+", "_", SOURCE.strip("[]")).strip("_")
cache    = SRC_DIR / f"_{safe}_raw.md"
if not cache.exists():
    cache = SRC_DIR / f"_{SOURCE}_raw.md"
template = SRC_DIR / f"[{SOURCE}].hwpx"
gold     = template
out_hwpx = PROD_DIR / f"{SOURCE}_{VER}.hwpx"
pdf      = SRC_DIR / f"[{SOURCE}].pdf"

DATA_TABLES: list[TableSpec] = []


def _split_matrix_name_commas(text: str) -> str:
    """$A, B$ → $A$, $B$ (단일 대문자 행렬명만 분리, 그 외는 그대로 유지)."""
    def split_match(m: re.Match) -> str:
        parts = [p.strip() for p in m.group(1).split(',')]
        if all(re.fullmatch(r'[A-Z]', p) for p in parts):
            return '$' + '$, $'.join(parts) + '$'
        return m.group(0)
    return re.sub(r'\$([A-Z](?:\s*,\s*[A-Z])*)\$', split_match, text)


def _fix_punct_spacing(text: str) -> str:
    """'일 때 , 이다' → '일 때, 이다' (한글·$ 뒤 구두점 앞 공백 제거)."""
    return re.sub(r'(?<=[가-힣$])\s+([,.])', r'\1', text)


def _xml_sha(hwpx_path: Path) -> str:
    with zipfile.ZipFile(hwpx_path) as zf:
        data = zf.read("Contents/section0.xml")
    return hashlib.sha256(data).hexdigest()[:12]


def _get_hp_t_texts(hwpx: Path) -> list[str]:
    with zipfile.ZipFile(hwpx) as zf:
        xml = zf.read("Contents/section0.xml").decode("utf-8")
    return re.findall(r"<hp:t[^>]*>([^<]+)</hp:t>", xml)


guard = CostGuard(cap_usd=5.0)

print(f"\n{'='*60}")
print(f"[고려고] → {VER}  (Cycle 16: 문제 단위 파이프라인)")

if out_hwpx.exists():
    print(f"  {VER} 이미 존재 ({_xml_sha(out_hwpx)}) — 재빌드하려면 수동 삭제")
    sys.exit(0)

if not cache.exists():
    print(f"  캐시 없음: {cache}")
    sys.exit(1)

if not template.exists():
    print(f"  template 없음: {template}")
    sys.exit(1)

md_raw = cache.read_text(encoding="utf-8")

# ── 고려고 OCR 수정 패치 ────────────────────────────────────────────────────

# A. score format: (N점) → [N점] (CRITICAL — 없으면 전 문제 score_idx=-1)
md_raw = re.sub(r'\((\d+(?:\.\d+)?)점\)', r'[\1점]', md_raw)

# A2. 서술형 점수 형식 （N점） → [N점] (전각 괄호 — parse_problems가 인식하도록)
md_raw = re.sub(r'（(\d+(?:\.\d+)?)점）', r'[\1점]', md_raw)

# B. <br> 태그 → 줄바꿈 (14번 전용)
md_raw = re.sub(r'\s*<br\s*/?>\s*', '\n', md_raw)

# B2. \left/\right 전역 제거 (HWP 수식 미지원 → 'LEFT' 텍스트로 표시됨)
md_raw = re.sub(r'\\left\s*\(', '(', md_raw)
md_raw = re.sub(r'\\right\s*\)', ')', md_raw)
md_raw = re.sub(r'\\left\s*\[', '[', md_raw)
md_raw = re.sub(r'\\right\s*\]', ']', md_raw)
md_raw = re.sub(r'\\left\s*\\{', r'\\{', md_raw)
md_raw = re.sub(r'\\right\s*\\}', r'\\}', md_raw)
md_raw = re.sub(r'\\left\s*\|', '|', md_raw)
md_raw = re.sub(r'\\right\s*\|', '|', md_raw)
md_raw = re.sub(r'\\left\s*\\?\.', '', md_raw)
md_raw = re.sub(r'\\right\s*\\?\.', '', md_raw)

# B3. 8번 연립부등식 복원 (B2 패치가 \left\{ 구조를 파괴함)
md_raw = md_raw.replace(
    '\\{\\begin{array}{l}\nx+4>7 \\\\\n4 x<k+2\n\\end{array}',
    '\\left\\{\\begin{array}{l}\nx+4>7 \\\\\n4 x<k+2\n\\end{array}\\right.',
)

# C. 14번: ## 4. → 14. + '름이 아' → '음이 아닌'
md_raw = md_raw.replace('## 4. 름이 아 정수', '14. 음이 아닌 정수')

# C'. 14번: OCR에서 누락된 분수식 복원 80!/(50!)(30!) = 2^n(2k+1)
md_raw = md_raw.replace(
    '14. 음이 아닌 정수 $n, k$ 를 사용하여\n$(50!)(30!)$\n$2^{n}(2 k+1)$ 와 같이 나타낼 때 $n$ 의 값은?',
    '14. $\\dfrac{80!}{(50!)(30!)}$ 을 음이 아닌 정수 $n, k$ 를 사용하여 $2^{n}(2k+1)$ 와 같이 나타낼 때, $n$ 의 값은?',
)
md_raw = md_raw.replace('\n8071\n', '\n')  # 14번 뒤 stray 숫자 제거

# D. 서술형 1 첫 글자 수정
md_raw = md_raw.replace('처술형 1', '서술형 1')

# E. 서술형 1 '때하여' → '대하여'
md_raw = md_raw.replace('때하여 $A=B$', '대하여 $A=B$')

# E2. 서술형 1 학생풀이 제거
md_raw = md_raw.replace('$a_{11}, a_{12}, d_{21}, a_{22} z$ LIELUIC', '')
md_raw = md_raw.replace('01 차정 사와잉s 21', '')
md_raw = md_raw.replace("구로 $a_{12} 41 B_{12}$ 가 $\\frac{x}{6} 2$", '')
md_raw = re.sub(
    r'\$\$\s*\\begin\{aligned\}\s*& 2\+c=2 \\\\.*?\\end\{aligned\}\s*\$\$',
    '',
    md_raw,
    flags=re.DOTALL,
)
md_raw = re.sub(r'\n\$C=0\$\n\$b=4\$\n', '\n', md_raw)
# 서술형 1 학생 필기 이미지 제거 (y=607, 771, 835, 953 좌표대 — student work)
md_raw = re.sub(
    r'!\[\]\(https://cdn\.mathpix\.com/cropped/7a7bbe86-52a3-4d0b-a079-b22c94bacc5e-6\.jpg'
    r'\?height=\d+&width=\d+&top_left_y=(?:607|771|835|953)[^\)]*\)\n?',
    '',
    md_raw,
)
# 서술형 2 학생 필기 이미지 제거 (y=1775)
md_raw = re.sub(
    r'!\[\]\(https://cdn\.mathpix\.com/cropped/7a7bbe86-52a3-4d0b-a079-b22c94bacc5e-6\.jpg'
    r'\?height=181&width=412[^\)]*\)\n?',
    '',
    md_raw,
)

# F. 10번: OCR 오자
md_raw = md_raw.replace('두양슥', '두 양수')
md_raw = md_raw.replace('만족시키눈 정수', '만족시키는 정수')
md_raw = md_raw.replace('를 아족시키는 자연수', '를 만족시키는 자연수')
md_raw = md_raw.replace('(4) 1.1\n(5) 13', '(4) 11\n(5) 13')  # 1.1 → 11
md_raw = md_raw.replace('(4) 1.1\n$A(14,13)=15$', '(4) 11\n$A(14,13)=15$')  # 중복 제거

# G. 2번: 선택지 ⑤ '$10^{\prime}$' → '10'
md_raw = md_raw.replace("(5) $10^{\\prime}$", '(5) 10')
md_raw = md_raw.replace("(5) $10'$", '(5) 10')

# H. 1번 뒤 스크래치 제거
md_raw = md_raw.replace('(5) 5\n$3 \\quad 5$\n2.', '(5) 5\n2.')

# I. 3번: 누락 선택지 ②③④ (사용자 확인: ①20 ⑤40 기준 25/30/35 추정)
md_raw = md_raw.replace(
    '3. ${ }_{5} \\mathrm{P}_{2}$ 의 값은? [3점]\n(1) 20\n(5) 40',
    '3. ${ }_{5} \\mathrm{P}_{2}$ 의 값은? [3점]\n(1) 20\n(2) 25\n(3) 30\n(4) 35\n(5) 40',
)

# J. 5번: 학생 풀이 제거 (score 아래 스크래치)
md_raw = re.sub(
    r'\[3점\]\n+\$\\\$partial\^21.*?\n+\$\\begin\{array\}\{ll\}\n0 & 2 \\\\\n0 & 3\n\\\\end\{array\}\$\n+',
    '[3점]\n',
    md_raw,
    flags=re.DOTALL,
)

# K. 6번: (4) 누락 추가 + 변수 q → a (OCR 오인식)
md_raw = md_raw.replace('(3) 42\n45\n(5) 50', '(3) 42\n(4) 45\n(5) 50')
md_raw = md_raw.replace('모든 실수 $q$ 의 값의 곱은?', '모든 실수 $a$ 의 값의 곱은?')

# L. 7번: © → (3) + 또든 → 또는 + 학생풀이 제거 + ① <-arrow 방지 + ⑤ 주의 처리
md_raw = md_raw.replace('© $x<-4$ 또든 $x>5$', '(3) $x<-4$ 또는 $x>5$')
# 7번 학생 계산 줄 제거 (선택지 사이에 끼어 파서가 unlabeled choice로 수집)
md_raw = md_raw.replace('\n$-x-1-x+2>9$\n', '\n')
md_raw = md_raw.replace('\n$-2 x>8<x<-4$\n', '\n')
md_raw = md_raw.replace('\n$x+1-x+2>0$\n', '\n')
md_raw = md_raw.replace('\n$x+1+x-2>9$\n', '\n')
md_raw = md_raw.replace('\n$2 x>10$\n', '\n')
# ① x<-1 → x < -1 (HWP 수식에서 <-는 왼쪽화살표로 해석됨)
md_raw = md_raw.replace('(1) $x<-1$ 또는 $x>2$', '(1) $x < -1$ 또는 $x>2$')
md_raw = md_raw.replace('(3) $x<-4$ 또는 $x>5$', '(3) $x < -4$ 또는 $x>5$')

# M. 8번: 선택지 ⑤ 뒤 스크래치 제거
md_raw = re.sub(
    r'\(5\)\. 49 \$\\begin\{array\}.*?\$',
    '(5) 49',
    md_raw,
    flags=re.DOTALL,
)

# N. 9번: 도형 표기 수식처리 (사각형·선분·꼭짓점)
md_raw = md_raw.replace(
    '정사각형 ABCD 와 한 변의 길이가 $b$ 인 정사각형 EFGH',
    '정사각형 $ABCD$ 와 한 변의 길이가 $b$ 인 정사각형 $EFGH$',
)
md_raw = md_raw.replace('선분 CD 와 선분 HE 의 교점을 I 라', '선분 $CD$ 와 선분 $HE$ 의 교점을 $I$ 라')
md_raw = md_raw.replace('직사각형 EBCI 의 넓이가 정사각형 EFGH', '직사각형 $EBCI$ 의 넓이가 정사각형 $EFGH$')

# N2. 9번: 선택지 사이 학생풀이 제거 + ⑤ 오인식 수정
md_raw = md_raw.replace('\n$2 d=\\frac{1}{5} b^{2}$\n', '\n')  # (3)/(4) 사이 학생 계산
md_raw = md_raw.replace(
    '(5) $-5+\\sqrt{105} 10 a=6^{2}$',
    '(5) $-5+\\sqrt{105}$',
)

# O. 11번: jamo 분리 수정 (수식 안에 포함됨 → $ 경계 포함 교체)
md_raw = md_raw.replace('\\leq 10 ㅇ ㅣ ㄴ$ 자연수이다', '\\leq 10$ 인 자연수이다')
md_raw = md_raw.replace('사열하는', '나열하는')
# 11번 (가)(나) 공란 → 박스 수식 처리
md_raw = md_raw.replace(
    '는 (가) $\\times{ }_{8}',
    '는 $box{~~(가)~~}$ $\\times{ }_{8}',
)
md_raw = md_raw.replace(
    '$k(k-1) \\times$ (나) 이다',
    '$k(k-1) \\times$ $box{~~(나)~~}$ 이다',
)
# 11번 OCR 손상 수식 복원 (분수 오인식된 (가)/(나) 포함 equation 교체)
md_raw = md_raw.replace(
    '& { }_{10} \\mathrm{P}_{k}={ }_{8} \\mathrm{P}_{k}'
    '+\\frac{\\text { (가) }}{\\text { 아수 } \\mathrm{P}_{k}}'
    ' \\times{ }_{8} \\mathrm{P}_{k-1}+k(k-1) \\times \\text { (나) } \\\\\n'
    '& \\text { 이다. }',
    '{ }_{10}\\mathrm{P}_{k}={ }_{8}\\mathrm{P}_{k}'
    '+box{~~(가)~~}\\times{ }_{8}\\mathrm{P}_{k-1}+k(k-1)\\times box{~~(나)~~}',
)

# P. 12번: 선택지 정리 (분수 형식 오염)
md_raw = md_raw.replace(
    '(4) $\\frac{91}{4}$\n'
    '$\\frac{(2) \\frac{93}{4} \\quad(3) \\frac{95}{4}}{f(t) \\leq O}$\n'
    '(4) $\\frac{97}{4}$\n'
    '(5) $\\frac{99}{4}$\n'
    '(4) $\\frac{91}{4} \\quad \\frac{\\text { (2) } \\frac{93}{4}}{f(3) \\leq 0}$',
    '(1) $\\frac{91}{4}$\n(2) $\\frac{93}{4}$\n(3) $\\frac{95}{4}$\n(4) $\\frac{97}{4}$\n(5) $\\frac{99}{4}$',
)
# 12번: '해간' → '해는'
md_raw = md_raw.replace('의 해간 $-7', '의 해는 $-7')

# Q. 13번: 문제 번호 누락 복원 + 줄바꿈 연결
md_raw = md_raw.replace(
    '$(x)=x^{2}-4 x+3$ 에 대하여 부등식',
    '13. $f(x)=x^{2}-4 x+3$ 에 대하여 부등식',
)
md_raw = md_raw.replace('\n514\n', '\n(5) 14\n')
# 13번: 문제 본문 줄바꿈 제거 ('양수 m 의\n값의 범위를' → 연결)
md_raw = md_raw.replace(
    '하는 양수 $m$ 의\n값의 범위를',
    '하는 양수 $m$ 의 값의 범위를',
)

# R. 17번: 2컬럼 역전 복원 + ②⑤ 확정값 추가 (사용자 확인: ②56 ⑤72)
md_raw = md_raw.replace(
    '17. 한 자리 자연수 $n$ 에 대하여 세 변의 길이가 $n, 2 n+3,3 n$ 인 삼각형의 개수는 $a$ 이다.'
    ' 이 $a$ 개의 삼각형 중에서 이등변삼각형의 개수는 $b$, 둔각삼각형의 개수는 일 때,\n(3) 60\n(4) 63',
    '17. 한 자리 자연수 $n$ 에 대하여 세 변의 길이가 $n, 2n+3, 3n$ 인 삼각형의 개수는 $a$ 이다.'
    ' 이 $a$ 개의 삼각형 중에서 이등변삼각형의 개수는 $b$, 둔각삼각형의 개수는 $c$ 일 때, $abc$의 값은? [5.8점]'
    '\n(1) 48\n(2) 56\n(3) 60\n(4) 63\n(5) 72',
)

# S. 19번: (다) 조건 2줄 → 1줄 합치기 (parse_problems가 단일 조건으로 수집하도록)
md_raw = md_raw.replace(
    '(다) 방정식\n$(x^{3}+a x^{2}+b x+c)(-c x^{3}+b x^{2}-a x+1)=0$',
    '(다) 방정식 $(x^{3}+a x^{2}+b x+c)(-c x^{3}+b x^{2}-a x+1)=0$',
)

# T. 노이즈 제거: '1.2953' 등 학생 풀이 숫자 줄 (1. 패턴 오감지 방지)
md_raw = md_raw.replace('\n1.2953\n', '\n')
md_raw = md_raw.replace('\n2,36\n', '\n')
md_raw = md_raw.replace('\n71721\n', '\n')
md_raw = md_raw.replace('\n61920\n', '\n')
md_raw = md_raw.replace('\n92129\n', '\n')
md_raw = md_raw.replace('\n441\n', '\n')   # 14번 앞 stray 숫자

# U. 한자 노이즈 제거
md_raw = md_raw.replace('\n考합\n', '\n')
md_raw = md_raw.replace('\n考合\n', '\n')

# ── 문제 본문 숫자 수식처리 ─────────────────────────────────────────────────

# 1번
md_raw = md_raw.replace(
    '1. 3 종류의 빵과 2 종류의 우유 중에서 1 개를 선택하는 경우의 수 는?',
    '1. $3$ 종류의 빵과 $2$ 종류의 우유 중에서 $1$ 개를 선택하는 경우의 수는?',
)
# 2번
md_raw = md_raw.replace(
    '2. 3 종류의 김밥과 2 종류의 떡볶이 중에서 김밥과 떡볶이를 각각 1개씩 선택하는 경우의 수는?',
    '2. $3$ 종류의 김밥과 $2$ 종류의 떡볶이 중에서 김밥과 떡볶이를 각각 $1$개씩 선택하는 경우의 수는?',
)
# 6번
md_raw = md_raw.replace('실근의 개수가 2가 되도록', '실근의 개수가 $2$가 되도록')
# 8번
md_raw = md_raw.replace('값의 합이 15 가 되도록', '값의 합이 $15$ 가 되도록')
# 11번
md_raw = md_raw.replace('11. $10$ 이하의 자연수에서', '11. $10$ 이하의 자연수에서')  # 중복 방지
md_raw = md_raw.replace('11. 10 이하의 자연수에서', '11. $10$ 이하의 자연수에서')
md_raw = md_raw.replace(
    '(i) 1 과 2 를 모두 포함하지 않고 나머지 8 개의',
    '(i) $1$ 과 $2$ 를 모두 포함하지 않고 나머지 $8$ 개의',
)
md_raw = md_raw.replace(
    '(ii) $1$ 과 $2$ 중 한 개만 포함하고 나머지 $8$ 개의',
    '(ii) $1$ 과 $2$ 중 한 개만 포함하고 나머지 $8$ 개의',
)  # 중복 방지
md_raw = md_raw.replace(
    '(ii) 1 과 2 중 한 개만 포함하고 나머지 8 개의',
    '(ii) $1$ 과 $2$ 중 한 개만 포함하고 나머지 $8$ 개의',
)
md_raw = md_raw.replace(
    '(iii) $1$ 과 $2$ 를 모두 포함하고 나머지 $8$ 개의',
    '(iii) $1$ 과 $2$ 를 모두 포함하고 나머지 $8$ 개의',
)  # 중복 방지
md_raw = md_raw.replace(
    '(iii) 1 과 2 를 모두 포함하고 나머지 8 개의',
    '(iii) $1$ 과 $2$ 를 모두 포함하고 나머지 $8$ 개의',
)

# O'. 11번 보기 박스 마커 수동 삽입 (범위: sub-intro ~ 수식블록 + 이다.)
# 박스 구조: 자연수이다.) → [BOX] $1,2,3,...이다. / (i)(ii)(iii) / 수식 / 이다. [/BOX] → 위의(가)(나)...
md_raw = md_raw.replace(
    '\n$1,2,3, \\cdots, 10$ 에서',
    '\n【★ 보기시작:11번】\n$1,2,3, \\cdots, 10$ 에서',
)
md_raw = md_raw.replace(
    '\\end{aligned}\n$$\n\n위의 (가)',
    '\\end{aligned}\n$$\n이다.\n【★ 보기끝:11번】\n위의 (가)',
)

# 13번
md_raw = md_raw.replace('개수가 10 이 되도록', '개수가 $10$ 이 되도록')
# 15번
md_raw = md_raw.replace('어느 2 개의 합이 나머지 한 개의 2 배이고', '어느 $2$ 개의 합이 나머지 한 개의 $2$ 배이고')
md_raw = md_raw.replace('숫자 0, 3, 7 을 포함하지 않는', '숫자 $0, 3, 7$ 을 포함하지 않는')
# 16번
md_raw = md_raw.replace(
    '16. 1 부터 7 까지의 자연수가 하나씩 적혀 있는 7 장의 카드가 있다.'
    ' 이 7장의 카드 중 6장의 카드를 임의로 동시에-선택하여 세 자리의 자연수 두 개를 만들 때,'
    ' 가능한 모든 경우의 수를 $a$, 이 중에서 두 자연수의 합이 500 미만인 경우의 수를 $b$ 라 하자.',
    '16. $1$ 부터 $7$ 까지의 자연수가 하나씩 적혀 있는 $7$ 장의 카드가 있다.'
    ' 이 $7$장의 카드 중 $6$장의 카드를 임의로 동시에 선택하여 세 자리의 자연수 두 개를 만들 때,'
    ' 가능한 모든 경우의 수를 $a$, 이 중에서 두 자연수의 합이 $500$ 미만인 경우의 수를 $b$ 라 하자.',
)
# 18번
md_raw = md_raw.replace('정수해의 개수가 6 이 되도록', '정수해의 개수가 $6$ 이 되도록')
# 19번 조건 숫자 수식처리
md_raw = md_raw.replace('의 한 근은 1 이고', '의 한 근은 $1$ 이고')
md_raw = md_raw.replace('의 한 근은 2이다', '의 한 근은 $2$이다')
# 서술형 2
md_raw = md_raw.replace('선생님 3명과 학생 6 명이 있다', '선생님 $3$명과 학생 $6$ 명이 있다')
md_raw = md_raw.replace('9 명의 자리를 배치하는', '$9$ 명의 자리를 배치하는')
md_raw = md_raw.replace('（가）앞줄에 4 명，뒷줄에 5 명을 배치한다', '（가）앞줄에 $4$ 명，뒷줄에 $5$ 명을 배치한다')
# 서술형 3 조건문
md_raw = md_raw.replace('최고차항의 계수가 1이다', '최고차항의 계수가 $1$이다')
md_raw = md_raw.replace('서로 다른 실근의 개수는 2이다', '서로 다른 실근의 개수는 $2$이다')
md_raw = md_raw.replace('이 세 실근의 합은 -5 이다', '이 세 실근의 합은 $-5$ 이다')

# ── [1] 문제 분리 + 정렬 + 스크래치 제거 ────────────────────────────────
print("\n[1/7] 문제 파싱 (parse_problems)")
header, segments = parse_problems(md_raw)
obj_cnt  = sum(1 for s in segments if not s.is_subjective)
subj_cnt = sum(1 for s in segments if s.is_subjective)
cond_cnt = sum(1 for s in segments if s.conditions)
bogi_cnt = sum(1 for s in segments if s.boilerplate)
print(f"  발견: 객관식 {obj_cnt}개 / 서술형 {subj_cnt}개")
print(f"  조건 {cond_cnt}개 / 보기 {bogi_cnt}개 문제")
nums = sorted(s.number for s in segments)
print(f"  번호: {nums}")

# 선택지 현황 보고
print("  선택지 현황:")
for s in segments:
    if not s.is_subjective:
        n = s.number
        c = len(s.choices)
        mark = "✅" if c == 5 else f"⚠️ {c}개"
        print(f"    {n}번: {mark}")

# ── [2] 선택지 정규화 (LLM) ─────────────────────────────────────────────
print("\n[2/7] 선택지 정규화 (normalize_choices)")
try:
    guard.check_or_raise("choices")
    segments = normalize_choices(segments, log_stem=f"고려고_{VER}")
    choices_ok = sum(1 for s in segments if not s.is_subjective and len(s.choices) == 5)
    print(f"  선택지 5개 완비: {choices_ok}/{obj_cnt}개 문제")
    guard.record("choices", 0.01)
except CostCapError as e:
    print(f"  [비용 cap] {e}")

# ── [3] rebuild_markdown ─────────────────────────────────────────────────
print("\n[3/7] rebuild_markdown")
data_table_set = {t.item for t in DATA_TABLES}
md_rebuilt = rebuild_markdown("", segments, data_table_items=data_table_set)
md_rebuilt = _split_matrix_name_commas(md_rebuilt)
md_rebuilt = _fix_punct_spacing(md_rebuilt)
line_cnt   = md_rebuilt.count("\n")
marker_cnt = md_rebuilt.count("【★")
print(f"  줄 수: {line_cnt} / 마커: {marker_cnt}개")

# ── [4] LLM 후처리 ────────────────────────────────────────────────────────
print("\n[4/7] LLM 후처리 (postprocess_markdown)")
md_llm  = md_rebuilt
llm_meta: dict = {}
try:
    guard.check_or_raise("llm")
    md_llm, llm_meta = postprocess_markdown(md_rebuilt, log_stem=f"고려고_{VER}")
    if llm_meta.get("skipped"):
        print(f"  SKIP: {llm_meta.get('reason')}")
    else:
        cost        = llm_meta["cost_usd"]
        corrections = llm_meta.get("corrections", 0)
        rejected    = llm_meta.get("rejected", 0)
        print(f"  완료: ${cost:.4f}  교정 {corrections}건 / 거부 {rejected}건")
        guard.record("llm", cost)
except CostCapError as e:
    print(f"  [비용 cap] {e}")

# ── [5] OCR fallback + [6] HWPX 빌드 ─────────────────────────────────────
print("\n[5-6/7] apply_fallback + HWPX 빌드")
try:
    buf = io.StringIO()
    t0  = time.time()
    with redirect_stdout(buf):
        md_proc = apply_fallback(md_llm, pdf)
        r       = build_from_markdown(md_proc, out_hwpx, template)
    elapsed = time.time() - t0

    xml_sha     = _xml_sha(out_hwpx)
    size_kb     = out_hwpx.stat().st_size // 1024
    pipeline_out = buf.getvalue()

    print(f"  완료: p={r['paragraphs']} eq={r['equations']}  {size_kb}KB  {elapsed:.1f}s")
    print(f"  xml_sha: {xml_sha}")
    if pipeline_out.strip():
        print(f"  pipeline:\n{pipeline_out.strip()}")

except Exception:
    print(f"  오류:\n{traceback.format_exc()}")
    sys.exit(1)

# ── [7] 표 삽입 ──────────────────────────────────────────────────────────
print("\n[7/7] 표/박스 삽입")
try:
    n_cond = replace_condition_tables(out_hwpx)
    n_bogi = replace_boilerplate_tables(out_hwpx)

    for spec in DATA_TABLES:
        replace_placeholder_with_data_table(out_hwpx, spec)

    xml_sha_after = _xml_sha(out_hwpx)
    print(f"  xml_sha (표 삽입 후): {xml_sha_after}")

except Exception:
    print(f"  [경고] 표 삽입 오류:\n{traceback.format_exc()}")

# ── gold 비교 ──────────────────────────────────────────────────────────────
print(f"\n  ── gold 비교 ({gold.name}) ──")
try:
    xml_sha_final = _xml_sha(out_hwpx)

    t_gold = _get_hp_t_texts(gold)
    t_v5   = _get_hp_t_texts(out_hwpx)
    ht_diffs      = [(i, a, b) for i, (a, b) in enumerate(zip(t_gold, t_v5)) if a != b]
    count_mismatch = abs(len(t_gold) - len(t_v5))

    print(f"  hp:t 개수: gold={len(t_gold)} / {VER}={len(t_v5)}"
          + (f"  ⚠️ {count_mismatch}개 차이" if count_mismatch else "  ✅ 동일"))
    print(f"  hp:t 내용 차이: {len(ht_diffs)}건")
    for i, a, b in ht_diffs[:20]:
        print(f"    #{i}: {repr(a[:50])} → {repr(b[:50])}")
    if len(ht_diffs) > 20:
        print(f"    ... (총 {len(ht_diffs)}건)")

    diffs = compare_scripts(gold, out_hwpx, start_idx=0)
    print(f"\n  script 차이: {len(diffs)}건")
    for d in diffs[:10]:
        idx = d["idx"]
        if idx == -1:
            print(f"    총계: gold={d['a']} / {VER}={d['b']}")
        else:
            print(f"    #{idx}: {d['a'][:60]} → {d['b'][:60]}")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    report_path = LOG_DIR / f"고려고_{VER}_baseline_diff.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# 고려고 {VER} baseline 비교\n\n")
        f.write(f"생성: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"## SHA\n- gold: `{_xml_sha(gold)}`\n- {VER}: `{xml_sha_final}`\n\n")
        f.write(f"## hp:t\n- gold: {len(t_gold)}개 / {VER}: {len(t_v5)}개 / 차이: {len(ht_diffs)}건\n\n")
        if ht_diffs:
            f.write("### 내용 차이\n")
            for i, a, b in ht_diffs:
                f.write(f"- `#{i}`: `{a[:80]}` → `{b[:80]}`\n")
        f.write(f"\n## script\n- 차이: {len(diffs)}건\n\n")
        if diffs:
            f.write("### 차이 목록\n")
            for d in diffs:
                idx = d["idx"]
                if idx == -1:
                    f.write(f"- 총계: gold={d['a']} / {VER}={d['b']}\n")
                else:
                    f.write(f"- `#{idx}`: `{d['a'][:80]}` → `{d['b'][:80]}`\n")
        f.write(f"\n## LLM\n- 교정: {llm_meta.get('corrections', 'N/A')}건\n")
        f.write(f"- 거부: {llm_meta.get('rejected', 'N/A')}건\n")
        f.write(f"- 비용: ${llm_meta.get('cost_usd', 0):.4f}\n")
    print(f"\n  보고서: {report_path}")

except Exception:
    print(f"  gold 비교 오류:\n{traceback.format_exc()}")

# ── 비용 요약 ──────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print("비용 요약")
for svc, cost in guard.summary().items():
    print(f"  {svc}: ${cost:.4f}")
print(f"  오늘 합계: ${guard.total_today():.4f} / $5.00")
print("=" * 60)
