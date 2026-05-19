import sys, io, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.text_only.problem_segmenter import parse_problems

md = Path("samples/11b/_2025_1_1_b_공수1_광주제일고_raw.md").read_text(encoding="utf-8")
md = re.sub(r"(?m)^[（(]\s*[bB]\s*[）)]", "（1）", md)
header, segs = parse_problems(md)

SCORE_RE = re.compile(r"[\[［【]([\d.．]+)점[\]］】]")

total = 0.0
missing = []
print(f"{'문제':6} {'배점':8} {'선택지':8}")
print("-" * 30)
for s in segs:
    num = s.number if s.number < 100 else s.number - 100
    label = str(num) + ("서" if s.is_subjective else "")
    m = SCORE_RE.search(s.problem_text)
    score_str = m.group(1).replace("．", ".") if m else None
    score = float(score_str) if score_str else 0.0
    total += score
    ch_cnt = len(s.choices)
    ch_ok = "5개OK" if ch_cnt == 5 else f"{ch_cnt}개NG"
    flag = " ← 배점없음" if not score_str else ""
    print(f"{label:6} {(score_str or '없음'):8} {ch_ok}{flag}")
    if not score_str:
        missing.append(label)

print("-" * 30)
print(f"{'합계':6} {total:.1f}점")
if abs(total - 100.0) < 0.05:
    print("OK 100점 일치")
else:
    print(f"부족: {100.0 - total:.1f}점")
print(f"배점 누락: {missing or ['없음']}")
