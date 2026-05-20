"""동명고 page1 크롭 4개 Mathpix OCR."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

from src.common.ocr.mathpix_client import MathpixClient, MathpixError

client  = MathpixClient()
CROPS   = Path("log/cycle_16/dongmyeong_crops")
OCR_OUT = CROPS / "ocr"
OCR_OUT.mkdir(exist_ok=True)

for i in range(1, 5):
    img = CROPS / f"prob_{i}.png"
    if not img.exists():
        print(f"  [스킵] {img.name} 없음")
        continue

    out = OCR_OUT / f"prob_{i}.md"
    if out.exists():
        print(f"  [스킵] {out.name} 이미 존재")
        continue

    size_kb = img.stat().st_size // 1024
    print(f"  [{i}번] {img.name}  {size_kb}KB  →  OCR 중...")
    try:
        raw = client.raw_ocr_image(img)
        mmd = raw.get("mmd", "") or raw.get("text", "")
        out.write_text(mmd, encoding="utf-8")
        print(f"         저장: {out.name}  ({len(mmd)}자)")
    except MathpixError as e:
        print(f"         [실패] {e}")

print("\n완료.")
