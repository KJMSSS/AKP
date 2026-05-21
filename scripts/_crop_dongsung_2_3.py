"""동성고 2번·3번 크롭 OCR."""
import sys, base64, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")

import fitz
import anthropic
from src.common.ocr.mathpix_client import MathpixClient

DPI    = 300
SCALE  = DPI / 72
PDF    = Path("samples/11b/[2025_1_1_b_공수1_동성고].pdf")
OUT    = Path("log/cycle_16/dongsung_crops")
OCR_DIR = OUT / "ocr"
OCR_DIR.mkdir(parents=True, exist_ok=True)

CLIENT = anthropic.Anthropic()

DETECT_PROMPT = (
    "이 이미지는 2컬럼 수학 시험지 page 1 입니다.\n"
    "찾을 문제: 2번, 3번\n"
    "이미지 크기: {img_w}x{img_h}px\n\n"
    "규칙:\n"
    "1. 인쇄된 숫자 \"2.\" 또는 \"2．\"로 시작하는 문제 → 2번\n"
    "2. 인쇄된 숫자 \"3.\" 또는 \"3．\"로 시작하는 문제 → 3번\n"
    "3. 헤더/푸터/학생 필기 무시\n"
    "4. 보기·선택지까지 포함한 전체 범위\n\n"
    "JSON만 출력:\n"
    "{{\"problems\": ["
    "{{\"number\": 2, \"y_top\": 픽셀, \"y_bottom\": 픽셀, \"col\": \"left\" 또는 \"right\"}},"
    "{{\"number\": 3, \"y_top\": 픽셀, \"y_bottom\": 픽셀, \"col\": \"left\" 또는 \"right\"}}"
    "]}}\n"
    "없는 번호는 생략."
)

# ── bbox 감지 ───────────────────────────────────
img_path = OUT / "pg1_thumb.png"
doc_tmp = fitz.open(str(PDF))
pix_tmp = doc_tmp[0].get_pixmap(matrix=fitz.Matrix(100/72, 100/72))
img_w, img_h = pix_tmp.width, pix_tmp.height
doc_tmp.close()

b64 = base64.standard_b64encode(img_path.read_bytes()).decode()
msg = CLIENT.messages.create(
    model="claude-opus-4-7", max_tokens=512,
    messages=[{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
        {"type": "text", "text": DETECT_PROMPT.format(img_w=img_w, img_h=img_h)},
    ]}],
)
raw = msg.content[0].text.strip()
print(f"[bbox raw]\n{raw}\n")

try:
    start = raw.find('{'); end = raw.rfind('}') + 1
    result = json.loads(raw[start:end])
except Exception as e:
    print(f"[parse error] {e}")
    result = {"problems": []}

print(f"감지: {result}")

# ── 크롭 ────────────────────────────────────────
THUMB_SCALE = 100 / 72
MARGIN = 40

def crop_problem(pg_idx, prob_info):
    doc2 = fitz.open(str(PDF))
    page = doc2[pg_idx]
    mat  = fitz.Matrix(SCALE, SCALE)
    pix_full = page.get_pixmap(matrix=mat)
    W, H = pix_full.width, pix_full.height
    mid  = W // 2
    col  = prob_info.get("col", "left")
    y_top    = max(0, int(prob_info["y_top"]    / THUMB_SCALE * SCALE) - MARGIN)
    y_bottom = min(H, int(prob_info["y_bottom"] / THUMB_SCALE * SCALE) + MARGIN)
    x0, x1 = (0, mid - 20) if col == "left" else (mid + 20, W)
    rect = fitz.Rect(x0/SCALE, y_top/SCALE, x1/SCALE, y_bottom/SCALE)
    pix  = page.get_pixmap(matrix=mat, clip=rect)
    doc2.close()
    return pix

crops = {}
for prob in result.get("problems", []):
    n = prob["number"]
    path = OUT / f"prob_{n}.png"
    try:
        pix = crop_problem(0, prob)
        pix.save(str(path))
        crops[n] = path
        print(f"  {n}번 크롭: {path.name} ({pix.width}x{pix.height})")
    except Exception as e:
        print(f"  {n}번 크롭 실패: {e}")

# ── Mathpix OCR ──────────────────────────────────
mp = MathpixClient()
print("\n=== Mathpix OCR ===")
for n in sorted(crops):
    img_path = crops[n]
    try:
        raw_ocr = mp.raw_ocr_image(img_path)
        text = raw_ocr.get("mmd", "") or raw_ocr.get("text", "")
        out_md = OCR_DIR / f"prob_{n}.md"
        out_md.write_text(text, encoding="utf-8")
        print(f"  {n}번: {len(text)}자\n    {text[:250]}\n")
    except Exception as e:
        print(f"  {n}번 OCR 실패: {e}")
