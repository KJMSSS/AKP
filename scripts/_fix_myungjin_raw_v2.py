"""명진고 raw.md 2차 교정 — 학생 풀이(수식/계산) 제거."""
from pathlib import Path

raw_path = Path("d:/f1/AKP/log/cycle_16/crops/명진고/raw.md")
text = raw_path.read_text(encoding="utf-8")

# ── 1번: 선택지 사이 학생 풀이 제거 ──────────────────────────────────
# (3) 3개 다음에 끼어든 \( -1 \leq 2 x-3 \leq 1 \)
text = text.replace(
    "(3) 3 개\n\\( -1 \\leq 2 x-3 \\leq 1 \\)\n(4) 4 개",
    "(3) 3 개\n(4) 4 개"
)
# (5) 5개 이후 학생 풀이 블록
text = text.replace(
    "(5) 5 개\n\\( 2 \\leq 2 x \\leq 4 \\)\n\\[\n\\leq x \\leq 2\n\\]",
    "(5) 5 개"
)

# ── 4번: 선택지 사이 학생 풀이 제거 ──────────────────────────────────
# (2) 4 다음에 끼어든 \( -1+i+4+a \)
text = text.replace(
    "(2) 4\n\\( -1+i+4+a \\)\n(3) 9",
    "(2) 4\n(3) 9"
)
# (5) 25 이후 학생 풀이 블록
text = text.replace(
    "(5) 25\n\\( -4 \\)\n\\[\n(x+1)\\left(x^{2}-4\\right)\n\\]",
    "(5) 25"
)

# ── 5번: 선택지 이후 학생 계산 메모 제거 ─────────────────────────────
text = text.replace("\n13579\n", "\n")

# ── 7번: 선택지 (4)와 (5) 사이 학생 풀이 제거 ───────────────────────
text = text.replace(
    "(4) -4\n\\( (x+1)\\left(x^{2}-x+1\\right) \\)\n(5) -5",
    "(4) -4\n(5) -5"
)

# ── 8번: 문제 본문 split 합치기 + 학생 풀이 제거 ─────────────────────
text = text.replace(
    "8. 연립부등식 \\( 5-2 x \\leq 2 x-4<-2 x+8 \\) 을 만족시키는 정\n\n수 \\( x \\) 의 개수는?",
    "8. 연립부등식 \\( 5-2 x \\leq 2 x-4<-2 x+8 \\) 을 만족시키는 정수 \\( x \\) 의 개수는?"
)
# (3) 2개와 (4) 3개 사이 학생 풀이
text = text.replace(
    "(3) 2 개\n\\( 4 x \\geq 9 \\)\n(4) 3 개",
    "(3) 2 개\n(4) 3 개"
)
# (4) 3개와 (5) 4개 사이 학생 풀이 (빈 줄 포함)
text = text.replace(
    "(4) 3 개\n\n\\( 4 x<12 \\)\n(5) 4 개",
    "(4) 3 개\n(5) 4 개"
)
# (5) 4개 이후 학생 풀이 블록
text = text.replace(
    "(5) 4 개\n\\[\n\\frac{9}{4} \\leq x<3\n\\]",
    "(5) 4 개"
)

# ── 9번: OCR 오타 + 학생 풀이 제거 ──────────────────────────────────
# 이챠 → 이차, 학은 → 합은
text = text.replace("이챠부등식", "이차부등식")
text = text.replace("의 값의 학은?", "의 값의 합은?")
# (3) 3과 (4) 4 사이 학생 풀이 블록
text = text.replace(
    "(3) 3\n\\[\n(n+3)^{2}-(n+5)=0\n\\]\n(4) 4",
    "(3) 3\n(4) 4"
)
# (5) 5 이후 학생 풀이 블록
text = text.replace(
    "(5) 5\n\\[\n\\begin{array}{l}\nn^{2}+6 n+9-n-5=0 \\\\\nn^{2}+5 n+4=0 \\\\\n(n+4)(n+1)=0\n\\end{array}\n\\]",
    "(5) 5"
)

# ── 10번: 학생 풀이 제거 + (5) 레이블 복원 ───────────────────────────
text = text.replace(
    "(4) 4개\n\\[\ny=4 a-x\n\\]\n\n3개\n\\[\n\\begin{array}{l}\nx^{2}+x^{2}-8 a x+16 a^{2} \\\\\n2 x^{2}-8 a x+4 a^{2}+2 a+12 \\\\\nx^{2}-4 a x+2 a^{2}+a+6 \\\\\n4 a^{2}-2 a^{2}-a-6-60 \\\\\n2 a^{2}-a-6<0 \\\\\n2 a+3 \\\\\na-2 \\\\\n-\\frac{3}{2}<a<2 \\\\\n-1<1\n\\end{array}\n\\]",
    "(4) 4개\n(5) 3 개"
)

# ── 11번: 선택지 사이 학생 계산 제거 ────────────────────────────────
text = text.replace(
    "(3) 54\n\\[\n46\n\\]\n(4) 56\n\\[\n357\n\\]\n(5) 58\n\\[\n\\begin{array}{lll}\n3 & 24 & 24 \\\\\n& 42 & 24\n\\end{array}\n\\]",
    "(3) 54\n(4) 56\n(5) 58"
)

# ── 13번: (4)와 (5) 사이 행렬 계산 + (5) 이후 학생 풀이 제거 ──────
text = text.replace(
    "(4) -29\n\\[\n\\left(\\begin{array}{cc}\n-4 & -3 \\\\\na b & -7\n\\end{array}\\right)=\\left(\\begin{array}{ll}\n2 a & 1 \\\\\n-7 & 1\n\\end{array}\\right)+\\left(\\begin{array}{cc}\n2 b & -4 \\\\\n4 & -8\n\\end{array}\\right)\n\\]\n(5) -30\n\\[\n\\begin{array}{l}\na+b=-2 \\\\\na b=-3\n\\end{array}\n\\]\n\n\\( -8 \\quad-18 \\)\n",
    "(4) -29\n(5) -30\n"
)

# ── 14번 이후 학생 풀이(15번 내용 혼입) 제거 ─────────────────────────
text = text.replace(
    "(5) \\( B A \\)\n\n\\( -3<x-k<3 \\quad-34 k<x<3+ \\)\n\n15.",
    "(5) \\( B A \\)\n\n15."
)

# ── 16번 이후 학생 계산 제거 ────────────────────────────────────────
text = text.replace(
    "(5) 43102\n\\[\n\\begin{array}{l}\n4321096 \\\\\n43=0195 \\\\\n4312094 \\\\\n4310293 \\\\\n4231092\n\\end{array}\n\\]",
    "(5) 43102"
)

# ── 18번 이후 학생 계산 제거 ────────────────────────────────────────
text = text.replace(
    "(5) 28\n\n1359\n2968\n\\[\n\\left.\\frac{1}{2}+\\frac{1}{2}+2 K S\\right)\n\\]",
    "(5) 28"
)

# ── 서술형 1: 학생 풀이 제거 ────────────────────────────────────────
text = text.replace(
    "[5점]\n\\[\n(3 a-6)^{2}-4(a-2) \\times 4<0\n\\]\n\\[\n\\begin{array}{l}\n(3 a-6)(3 a-6) \\\\\n9 a^{2}-36 a+36-16 a+32<0 \\\\\n9 a^{2}-52 a+68<0\n\\end{array}\n\\]\n\n서술형 2.",
    "[5점]\n\n서술형 2."
)

# ── 서술형 2: 학생 풀이 제거 ────────────────────────────────────────
text = text.replace(
    "[5점]\n\\[\n\\begin{array}{l}\n\\left(\\begin{array}{ll}\n2 & 2 \\\\\n3 & 5\n\\end{array}\\right)\\left(\\begin{array}{ll}\n2 & 2 \\\\\n3 & 5\n\\end{array}\\right) \\\\\n\\left(\\begin{array}{ll}\n10 & 14 \\\\\n21 & 31\n\\end{array}\\right)\n\\end{array}\n\\]\n",
    "[5점]\n"
)

# ── 저장 ─────────────────────────────────────────────────────────────
raw_path.write_text(text, encoding="utf-8")
print("저장 완료!")

# 검증: 문제 번호 줄 목록
lines = text.split("\n")
for l in lines:
    stripped = l.strip()
    for n in list(range(1, 21)) + ["서술형 1", "서술형 2"]:
        prefix = f"{n}." if isinstance(n, int) else f"{n}."
        if stripped.startswith(prefix):
            print(f"  {stripped[:90]}")
            break
