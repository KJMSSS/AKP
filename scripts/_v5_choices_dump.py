"""v5 HWPX에서 선택지 값 추출 — 골드와 비교."""
import sys, zipfile, re, json
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
school = sys.argv[1] if len(sys.argv) > 1 else "명진고"
v5_path = ROOT / "samples" / "11b_production" / f"2025_1_1_b_공수1_{school}_v5.hwpx"
gold_path = ROOT / "data" / "gold_manifest" / f"{school}.json"

with zipfile.ZipFile(v5_path) as zf:
    xml = zf.read("Contents/section0.xml").decode("utf-8")

gold = json.loads(gold_path.read_text(encoding="utf-8"))

# hp:script 전체 목록
scripts = re.findall(r"<hp:script>(.*?)</hp:script>", xml, re.DOTALL)
print(f"v5 hp:script 총 {len(scripts)}개, 골드 수식 {gold['total_equations']}개\n")

# 선택지 마커 위치 기반으로 마커+뒤 script 쌍 추출
# <hp:t>① </hp:t> 뒤에 <hp:equation> 안에 <hp:script> 가 나옴
choice_block_re = re.compile(
    r"<hp:t>([①②③④⑤]) </hp:t>"
    r".*?(?:<hp:script>(.*?)</hp:script>|<hp:t>([^<①②③④⑤]+)</hp:t>)",
    re.DOTALL
)

results = []
for m in choice_block_re.finditer(xml):
    marker = m.group(1)
    script_val = m.group(2)
    plain_val = m.group(3)
    if script_val is not None:
        val = f"[식:{script_val.strip()[:40]}]"
    else:
        val = (plain_val or "").strip()[:40]
    results.append((marker, val))

# 문제번호 추적 (①마다 +1)
prob = 0
print(f"{'번':>3}{'마커':1}  {'v5값':^50}  {'골드값':^50}  {'일치'}")
print("-" * 115)

gold_probs = gold["problems"]
for marker, val_v5 in results:
    if marker == "①":
        prob += 1

    # 골드 값
    g = gold_probs.get(str(prob), {})
    gold_choices = g.get("choices", {})
    val_gold = gold_choices.get(marker, "?")

    # 간단 비교 (수식 따옴표 제거)
    def normalize(s):
        return re.sub(r"\s+", "", s).replace("[식:", "").replace("]", "").replace("`", "")

    match = "✓" if normalize(val_v5) == normalize(val_gold) else "✗"
    print(f"{prob:>3}{marker}  {val_v5:<50}  {val_gold:<50}  {match}")

print()
print("완료")
