"""
동명고(page 1,4 이미지 재OCR) + 동아여고(corrected PDF 전체 재제출)
→ raw.md 파일 생성/갱신
"""
import sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

import fitz
from src.common.ocr.mathpix_client import MathpixClient, MathpixError

ROOT    = Path(__file__).resolve().parent.parent
SAMPLE  = ROOT / "samples" / "11b"
PREP    = SAMPLE / "preprocessed"
OUT_DIR = SAMPLE

client = MathpixClient()

# ─────────────────────────────────────────────────────────────────────────────
# 1. 동아여고: corrected PDF 전체 → raw.md
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("[동아여고] corrected PDF 전체 Mathpix 재제출")
print("=" * 60)

dongah_pdf = PREP / "동아여고_corrected.pdf"
dongah_out = OUT_DIR / "_2025_1_1_b_공수1_동아여고_reocr_raw.md"

try:
    pdf_id = client.submit_pdf(dongah_pdf)
    print(f"  제출 완료 (pdf_id={pdf_id})")
    client.poll_pdf(pdf_id, progress=True)
    md = client.fetch_pdf_markdown(pdf_id)
    dongah_out.write_text(md, encoding="utf-8")
    print(f"  저장: {dongah_out.name}  ({len(md)}자)")
except MathpixError as e:
    print(f"  [실패] {e}")

# ─────────────────────────────────────────────────────────────────────────────
# 2. 동명고: page 1, 4 이미지 OCR
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("[동명고] page 1, 4 이미지 Mathpix OCR")
print("=" * 60)

dongmyeong_pdf = SAMPLE / "[2025_1_1_b_공수1_동명고].pdf"
doc = fitz.open(str(dongmyeong_pdf))

pages_mmd = {}
for page_idx in [0, 3]:  # page 1 and page 4 (0-indexed)
    page = doc[page_idx]
    mat  = fitz.Matrix(300 / 72, 300 / 72)  # 300 dpi
    pix  = page.get_pixmap(matrix=mat)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp = Path(f.name)
    pix.save(str(tmp))
    size_kb = tmp.stat().st_size // 1024
    print(f"  page {page_idx+1}: PNG {pix.width}×{pix.height}  {size_kb}KB")

    try:
        raw = client.raw_ocr_image(tmp)
        mmd = raw.get("mmd", "")
        txt = raw.get("text", "")
        print(f"    mmd: {len(mmd)}자  text: {len(txt)}자")
        pages_mmd[page_idx + 1] = mmd if mmd else txt
    except MathpixError as e:
        print(f"    [실패] {e}")
        pages_mmd[page_idx + 1] = f"<!-- OCR 실패: {e} -->"
    finally:
        tmp.unlink(missing_ok=True)

doc.close()

# 결과 저장
for pnum, mmd in sorted(pages_mmd.items()):
    out = OUT_DIR / f"_2025_1_1_b_공수1_동명고_page{pnum}_reocr.md"
    out.write_text(mmd, encoding="utf-8")
    print(f"  저장: {out.name}")

print("\n완료.")
