"""
동신여고 raw.md 재구성.
각 prob_N.md에서 문제 본문만 추출 + 골드 선택지 삽입.
"""
import json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ocr_dir = ROOT / "log" / "cycle_16" / "crops" / "동신여고" / "ocr"
raw_path = ROOT / "log" / "cycle_16" / "crops" / "동신여고" / "raw.md"
gold = json.loads((ROOT / "data" / "gold_manifest" / "동신여고.json").read_text(encoding="utf-8"))

def gold_choices_md(prob_key: str) -> str:
    """골드 선택지를 (1)~(5) 형식으로 변환."""
    prob = gold["problems"].get(prob_key, {})
    choices = prob.get("choices", {})
    if not choices:
        return ""
    num_map = {"①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5}
    lines = []
    for marker, val in choices.items():
        # [식:N] → \( N \)
        v = re.sub(r'\[식:([^\]]+)\]', lambda m: f"\\( {m.group(1)} \\)", val)
        lines.append(f"({num_map[marker]}) {v}")
    return "\n".join(lines)

def extract_prob_text(ocr_text: str, num: int) -> str:
    """
    OCR 텍스트에서 문제 N.으로 시작하는 줄부터 선택지 직전까지 추출.
    중복 번호가 있으면 마지막(가장 완전한) 버전 선택.
    """
    lines = ocr_text.strip().split("\n")
    prefix = f"{num}."

    # 문제 번호 시작 위치들 찾기
    starts = [i for i, l in enumerate(lines) if l.strip().startswith(prefix)]
    if not starts:
        return ""

    # 마지막 시작 위치 (가장 완전한 버전)
    start = starts[-1]

    # 끝 위치: 다음 문제 번호 또는 선택지 또는 파일 끝
    end = len(lines)
    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()
        # 다른 문제 번호 만나면 종료
        m = re.match(r'^(\d+)\.\s', stripped)
        if m and int(m.group(1)) != num:
            end = i
            break
        # 선택지 줄 만나면 종료
        if re.match(r'^\([1-5]\)\s+', stripped):
            end = i
            break
        # 서술형 시작
        if stripped.startswith("서술형"):
            end = i
            break

    block = lines[start:end]
    # 끝에서 빈 줄 제거
    while block and not block[-1].strip():
        block.pop()

    return "\n".join(block)

# ── 수동 수정이 필요한 문제 (OCR이 너무 손상된 경우) ──────────────

# 6번: 보기 박스 내용이 OCR에서 심하게 손상됨 → 골드 기반 재작성
PROB6_TEXT = """\
6. 아래의 보기에 제시된 등식이 참이 되도록 하는 \\( \\alpha, \\beta, \\gamma \\) 에 대하여 \\( \\frac{\\alpha}{\\beta}-\\gamma \\) 의 값은? [3.6점]

\\begin{tabular}{l}
ㄱ. \\( {}_{4}P_{3} \\div 0! = \\alpha \\) \\\\
ㄴ. \\( 3! \\times 2! = \\beta \\) \\\\
ㄷ. \\( {}_{3}C_{2} \\times {}_{5}P_{0} = \\gamma \\)
\\end{tabular}\
"""

# ── 메인 빌드 ──────────────────────────────────────────────────────
sections = []

# 객관식 1~19
for n in range(1, 20):
    prob_key = str(n)
    score = gold["score_list"].get(prob_key)
    score_str = f"[{score}점]" if score else ""
    choices = gold_choices_md(prob_key)

    if n == 6:
        sections.append(PROB6_TEXT + "\n" + choices)
        continue

    ocr_file = ocr_dir / f"prob_{n}.md"
    if not ocr_file.exists():
        sections.append(f"{n}. [OCR 파일 없음]\n{score_str}\n{choices}")
        continue

    ocr_text = ocr_file.read_text(encoding="utf-8")
    prob_text = extract_prob_text(ocr_text, n)

    if not prob_text:
        # OCR에서 문제 번호를 찾지 못한 경우 — raw.md 전체에서 찾기
        # raw.md를 사용하지 않으므로, 간단히 "OCR에서 추출 실패" 처리
        sections.append(f"{n}. [추출 실패]\n{score_str}\n{choices}")
        continue

    # 배점이 이미 문제 본문에 있는지 확인
    if score_str and score_str not in prob_text:
        prob_text = prob_text + "\n" + score_str

    sections.append(prob_text + "\n" + choices)

# 서술형 101~104 (골드 번호 서술형20~23)
subj_map = [(101, "서술형20", "서술형 1"), (102, "서술형21", "서술형 2"),
            (103, "서술형22", "서술형 3"), (104, "서술형23", "서술형 4")]

for ocr_num, gold_key, label in subj_map:
    ocr_file = ocr_dir / f"prob_{ocr_num}.md"
    score = gold["score_list"].get(gold_key)
    score_str = f"[{score}점]" if score else ""

    if ocr_file.exists():
        ocr_text = ocr_file.read_text(encoding="utf-8").strip()
        # 서술형은 그대로 사용 (학생 풀이 포함돼도 LLM이 처리)
        # 단, 다른 문제 내용 제거
        # 서술형 N. 시작 줄 찾기
        lines = ocr_text.split("\n")
        start = 0
        for i, l in enumerate(lines):
            if re.match(r'^서술형\s*\d*\.?\s*\<?' , l.strip()) or \
               re.match(r'^\d+\.\s*\d', l.strip()):
                start = i
        content = "\n".join(lines[start:]).strip()
    else:
        content = f"{label}. [OCR 파일 없음]"

    sections.append(f"{content}\n{score_str}")

# ── 저장 ──────────────────────────────────────────────────────────
raw_md = "\n\n".join(sections)
raw_path.write_text(raw_md, encoding="utf-8")
print(f"raw.md 저장: {len(raw_md)}자")

# 검증
lines_all = raw_md.split("\n")
choice_lines = [l for l in lines_all if re.match(r'\([1-5]\)\s+', l.strip())]
print(f"선택지 줄 수: {len(choice_lines)}개 (기대 95개)")
for n in range(1, 20):
    cnt = sum(1 for l in choice_lines if l.strip().startswith(("(1) ", "(2) ", "(3) ", "(4) ", "(5) ")))
print("완료!")
