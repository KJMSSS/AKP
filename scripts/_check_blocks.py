import sys, io, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.text_only.problem_segmenter import parse_problems

md = Path("samples/11b/_2025_1_1_b_공수1_광주제일고_raw.md").read_text(encoding="utf-8")
md = re.sub(r"(?m)^[（(]\s*[bB]\s*[）)]", "（1）", md)
header, segs = parse_problems(md)

for num in [3, 12]:
    s = next(x for x in segs if x.number == num)
    print(f"=== {num}번 raw_block ===")
    for i, l in enumerate(s.raw_block.split("\n")):
        print(f"  {i:2}: {l}")
    print(f"  choices: {s.choices}")
    print(f"  boilerplate: {s.boilerplate}")
    print()
