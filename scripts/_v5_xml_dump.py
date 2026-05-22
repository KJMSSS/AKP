"""v5 HWPX XML에서 선택지 마커 주변 구조 확인."""
import sys, zipfile, re
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
school = sys.argv[1] if len(sys.argv) > 1 else "명진고"
v5_path = ROOT / "samples" / "11b_production" / f"2025_1_1_b_공수1_{school}_v5.hwpx"

with zipfile.ZipFile(v5_path) as zf:
    xml = zf.read("Contents/section0.xml").decode("utf-8")

# 선택지 마커 위치 찾기
marker_re = re.compile(r"[①②③④⑤]")
positions = [(m.group(), m.start()) for m in marker_re.finditer(xml)]

print(f"총 선택지 마커 {len(positions)}개 발견\n")
print("처음 15개 마커 주변 100자:")
print("-" * 80)
for marker, pos in positions[:15]:
    snippet = xml[max(0, pos-20):pos+100]
    # 개행 정리
    snippet = snippet.replace("\n", " ").replace("\r", "")
    print(f"  {marker}: ...{snippet}...")
    print()
