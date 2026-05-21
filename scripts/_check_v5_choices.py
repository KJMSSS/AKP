"""v5 hwpx에서 선택지 마커 수 검증."""
import sys, zipfile, re
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

PROD = Path(__file__).resolve().parent.parent / "samples" / "11b_production"
CHOICE_RE = re.compile(r'[①②③④⑤]')

schools = sorted(PROD.glob("*_v5.hwpx"), key=lambda p: p.name)
print(f"{'학교':<20} {'선택지수':>6} {'경고'}")
print("-" * 45)
for hwpx in schools:
    try:
        with zipfile.ZipFile(hwpx) as zf:
            xml = zf.read("Contents/section0.xml").decode("utf-8", errors="ignore")
        cnt = len(CHOICE_RE.findall(xml))
        flag = " ⚠️ 선택지 없음!!" if cnt < 10 else (" ⚠️ 적음" if cnt < 50 else "")
        name = hwpx.stem.replace("2025_1_1_b_공수1_","").replace("_v5","")
        print(f"{name:<20} {cnt:>6}  {flag}")
    except Exception as e:
        print(f"{hwpx.name}: 오류 {e}")
