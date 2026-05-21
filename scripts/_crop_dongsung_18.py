"""동성고 18번 크롭 + OCR (page5 좌측 y=525~605)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")

import fitz
from src.common.ocr.mathpix_client import MathpixClient

DPI    = 300
SCALE  = DPI / 72
PDF    = Path("samples/11b/[2025_1_1_b_공수1_동성고].pdf")
OUT    = Path("log/cycle_16/dongsung_crops")
OCR_DIR = OUT / "ocr"
OCR_DIR.mkdir(parents=True, exist_ok=True)

THUMB_DPI   = 100
THUMB_SCALE = THUMB_DPI / 72

# 감지 결과: page5(idx=4), col=left, y=525~605
PROB_INFO = {"number": 18, "y_top": 525, "y_bottom": 605, "col": "left"}
PG_IDX    = 4  # page 5 (0-indexed)
MARGIN    = 40

doc = fitz.open(str(PDF))
page = doc[PG_IDX]
mat  = fitz.Matrix(SCALE, SCALE)
pix_full = page.get_pixmap(matrix=mat)
W, H = pix_full.width, pix_full.height
mid  = W // 2

y_top    = int(PROB_INFO["y_top"]    / THUMB_SCALE * SCALE) - MARGIN
y_bottom = int(PROB_INFO["y_bottom"] / THUMB_SCALE * SCALE) + MARGIN
y_top    = max(0, y_top)
y_bottom = min(H, y_bottom)
x0, x1   = 0, mid - 20  # left column

rect = fitz.Rect(x0/SCALE, y_top/SCALE, x1/SCALE, y_bottom/SCALE)
pix  = page.get_pixmap(matrix=mat, clip=rect)
doc.close()

crop_path = OUT / "prob_18.png"
pix.save(str(crop_path))
print(f"크롭 저장: {crop_path.name} ({pix.width}x{pix.height})")

# Mathpix OCR
mp = MathpixClient()
raw = mp.raw_ocr_image(crop_path)
text = raw.get("mmd", "") or raw.get("text", "")
out_md = OCR_DIR / "prob_18.md"
out_md.write_text(text, encoding="utf-8")
print(f"OCR 완료: {len(text)}자 → {out_md.name}")
print(f"\n--- OCR 결과 ---\n{text}\n---")
