"""각 학교 raw.md 점수 형식·문제 수 스캔."""
import re, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
SRC  = ROOT / "samples" / "11b"

schools = [
    "경신여고","고려고","광덕고","광주고","광주여고","광주제일고",
    "국제고","금호고","금호중앙여고","대광여고","대동고","대성여고",
    "동명고","동성고","동신여고","동아여고","명진고","문성고",
]

print(f"{'학교':<12} {'문제':>4} {'이미지':>5} {'[N점]':>5} {'(N점)':>5} {'【N점】':>6}")
print("-" * 48)
for s in schools:
    f = SRC / f"_2025_1_1_b_공수1_{s}_raw.md"
    if not f.exists():
        print(f"{s:<12} 파일없음")
        continue
    txt = f.read_text(encoding="utf-8")
    probs  = len(re.findall(r"^\d{1,2}[.．]", txt, re.MULTILINE))
    imgs   = len(re.findall(r"mathpix", txt))
    sc_sq  = len(re.findall(r"[\[［][\d.．]+점[\]］]", txt))
    sc_rd  = len(re.findall(r"\([\d.]+점\)", txt))
    sc_dbl = len(re.findall(r"【[\d.．]+점】", txt))
    flag = ""
    if probs < 15: flag += " ⚠️문제少"
    if sc_sq == 0 and sc_rd == 0 and sc_dbl == 0: flag += " ⚠️점수없음"
    if sc_dbl > 0: flag += " ⚠️【】형식"
    if imgs > 20: flag += " ⚠️이미지多"
    if imgs < 3: flag += " ⚠️이미지少"
    print(f"{s:<12} {probs:>4} {imgs:>5} {sc_sq:>5} {sc_rd:>5} {sc_dbl:>6}  {flag}")
