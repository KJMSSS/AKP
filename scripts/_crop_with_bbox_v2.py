"""bbox_v2.json 좌표로 동아여고 page1 재크롭."""
import sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

import fitz

DPI   = 300
SCALE = DPI / 72

PDF   = Path("samples/11b/preprocessed/동아여고_corrected.pdf")
CROPS = Path("log/cycle_16/dongah_crops")
BBOX  = json.loads((CROPS / "bbox_v2.json").read_text(encoding="utf-8"))

doc  = fitz.open(str(PDF))
page = doc[0]  # page 1 (0-indexed)

# 전체 페이지 크기 확인
mat = fitz.Matrix(SCALE, SCALE)
pix = page.get_pixmap(matrix=mat)
W, H = pix.width, pix.height
print(f"페이지 크기: {W}×{H}px")

mid   = W // 2
col_x = {"left": (0, mid - 20), "right": (mid + 20, W)}

for p in BBOX:
    num = p["num"]
    col = p.get("column", "left")
    y0  = max(0, int(p["y_start"]) - 20)
    y1  = min(H, int(p["y_end"])   + 20)
    x0, x1 = col_x.get(col, (0, W))

    rect = fitz.Rect(x0/SCALE, y0/SCALE, x1/SCALE, y1/SCALE)
    pix2 = page.get_pixmap(matrix=mat, clip=rect)

    out = CROPS / f"v2_prob_{num.replace(' ', '_')}.png"
    pix2.save(str(out))
    print(f"  {num}번: col={col} y={y0}~{y1} → {out.name} ({pix2.width}×{pix2.height})")

doc.close()
print("\n완료.")
