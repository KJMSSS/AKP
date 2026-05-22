"""v5 vs 골드 빠른 비교."""
import sys, zipfile, re, json
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
school = sys.argv[1] if len(sys.argv) > 1 else "명진고"

v5_path = ROOT / "samples" / "11b_production" / f"2025_1_1_b_공수1_{school}_v5.hwpx"
gold_path = ROOT / "data" / "gold_manifest" / f"{school}.json"

# v5 파싱
with zipfile.ZipFile(v5_path) as zf:
    xml = zf.read("Contents/section0.xml").decode("utf-8")

scripts = re.findall(r"<hp:script>(.*?)</hp:script>", xml, re.DOTALL)
choices_v5 = re.findall(r"[①②③④⑤]", xml)
print(f"v5 수식: {len(scripts)}개  /  골드 수식: ", end="")

gold = json.loads(gold_path.read_text(encoding="utf-8"))
print(f"{gold['total_equations']}개  →  차이: {len(scripts) - gold['total_equations']:+d}")
print(f"v5 선택지 마커: {len(choices_v5)}개  /  골드: {gold['choice_marker_total']}개")
print()

# 선택지별 상세 비교
# v5 XML에서 선택지 마커 + 바로 뒤 텍스트 추출
# 마커 → 텍스트 (script 또는 plain 텍스트)
chunk_re = re.compile(
    r"([①②③④⑤])</hp:t>.*?(?=<hp:t[^r]|<hp:script|</hp:p)",
    re.DOTALL
)
# 더 단순하게: 마커 뒤 100자 내 script 또는 텍스트
seg_re = re.compile(
    r"([①②③④⑤])(?:.*?<hp:script>(.*?)</hp:script>|.*?<hp:t[^>]*>([^<]+)</hp:t>)",
    re.DOTALL
)

# 문제 번호별로 분할 (메타표 기반은 복잡하니 선택지 마커만)
# 간단히: 전체 XML에서 선택지 순서만 뽑기
plain_re = re.compile(r"<hp:t[^>]*>([^<]+)</hp:t>")
script_re = re.compile(r"<hp:script>(.*?)</hp:script>", re.DOTALL)

# 선택지 마커가 나오는 위치 기반으로 파싱
marker_re = re.compile(r"[①②③④⑤]")
markers_pos = [(m.group(), m.start()) for m in re.finditer(r"[①②③④⑤]", xml)]

print(f"{'번호':>4}  {'골드':^60}  {'v5':^60}")
print("-" * 130)

prob_num = 0
gold_probs = gold["problems"]

for i, (marker, pos) in enumerate(markers_pos):
    if marker == "①":
        prob_num += 1

    # 마커 뒤 200자에서 값 추출
    snippet = xml[pos:pos+200]
    # script가 있으면 script 값
    sm = script_re.search(snippet)
    # plain 텍스트
    pm = plain_re.search(snippet[1:])  # 마커 자체 제외

    if sm:
        val_v5 = f"[식:{sm.group(1).strip()[:30]}]"
    elif pm:
        val_v5 = pm.group(1).strip()[:30]
    else:
        val_v5 = "?"

    # 골드 값
    prob_key = str(prob_num)
    gold_prob = gold_probs.get(prob_key, {})
    gold_choices = gold_prob.get("choices", {})
    val_gold = gold_choices.get(marker, "?")[:60]

    match = "✓" if val_v5.replace(" ", "") == val_gold.replace(" ", "") else "✗"
    print(f"{prob_num:>3}{marker}  {val_gold:<60}  {match}  {val_v5:<60}")

print()
print("완료")
