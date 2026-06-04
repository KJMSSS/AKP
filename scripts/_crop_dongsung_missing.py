"""동성고 4번·7번·18번 크롭 OCR — 누락 문제 복원."""
import sys, base64, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")

import fitz
import anthropic

DPI   = 300
SCALE = DPI / 72
PDF   = Path("samples/11b/[2025_1_1_b_공수1_동성고].pdf")
OUT   = Path("log/cycle_16/dongsung_crops")
OUT.mkdir(parents=True, exist_ok=True)

CLIENT = anthropic.Anthropic()

# ──────────────────────────────────────────────
# Step 1: 각 페이지 썸네일 생성 (bbox 감지용)
# ──────────────────────────────────────────────
doc = fitz.open(str(PDF))

def page_thumb(page_idx, dpi=72):
    page = doc[page_idx]
    mat  = fitz.Matrix(dpi/72, dpi/72)
    pix  = page.get_pixmap(matrix=mat)
    return pix

# page 1, 2, 3 썸네일 저장
for pg in [0, 1, 2]:
    pix = page_thumb(pg, dpi=100)
    pix.save(str(OUT / f"pg{pg+1}_thumb.png"))
    print(f"page {pg+1} 썸네일: {pix.width}×{pix.height}")

doc.close()

# ──────────────────────────────────────────────
# Step 2: Vision으로 bbox 감지
# ──────────────────────────────────────────────
DETECT_PROMPT = """\
이 이미지는 2컬럼 레이아웃의 수학 시험지 페이지입니다.
중요 규칙:
1. 상단 헤더(시험 제목, 학교명, 지시사항, 저작권 문구, 이름/반 기입란)는 완전히 무시하세요.
2. 학생 손글씨 풀이, 빨간 동그라미, 필기는 완전히 무시하세요.
3. 인쇄된 아라비아 숫자로 시작하는 문제(예: "1.", "2.", "3.")만 찾으세요.

이미지에서 찾을 문제들: {target_probs}
이미지 높이: {img_h}px

각 문제의 bounding box를 JSON으로 출력하세요:
{{
  "problems": [
    {{"number": N, "y_top": 픽셀, "y_bottom": 픽셀, "col": "left" 또는 "right"}}
  ]
}}

문제가 보이지 않으면 해당 항목을 생략하세요.
픽셀 좌표만 출력, 다른 설명 없이."""


def detect_bbox(pg_idx, target_probs):
    img_path = OUT / f"pg{pg_idx+1}_thumb.png"
    b64 = base64.standard_b64encode(img_path.read_bytes()).decode()
    img_h = fitz.open(str(PDF))[pg_idx].get_pixmap(matrix=fitz.Matrix(100/72, 100/72)).height

    msg = CLIENT.messages.create(
        model="claude-opus-4-7", max_tokens=512,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            {"type": "text", "text": DETECT_PROMPT.format(
                target_probs=target_probs,
                img_h=img_h
            )},
        ]}],
    )
    raw = msg.content[0].text.strip()
    print(f"  [bbox raw] {raw[:200]}")
    try:
        start = raw.find('{')
        end   = raw.rfind('}') + 1
        return json.loads(raw[start:end])
    except Exception as e:
        print(f"  [bbox parse error] {e}")
        return {"problems": []}


print("\n=== bbox 감지 ===")
# 4번·7번: page 1, 18번: page 3
bbox_p1 = detect_bbox(0, "4번, 7번")
bbox_p3 = detect_bbox(2, "18번")

print(f"\npage1 감지: {bbox_p1}")
print(f"page3 감지: {bbox_p3}")

# ──────────────────────────────────────────────
# Step 3: bbox 기반 크롭
# ──────────────────────────────────────────────
THUMB_DPI  = 100
THUMB_SCALE = THUMB_DPI / 72

def crop_problem(pg_idx, prob_info, margin=30):
    """thumb 좌표 → 실제 PDF 좌표로 변환 후 크롭."""
    doc2 = fitz.open(str(PDF))
    page = doc2[pg_idx]
    mat  = fitz.Matrix(SCALE, SCALE)
    pix_full = page.get_pixmap(matrix=mat)
    W, H = pix_full.width, pix_full.height
    mid  = W // 2

    col = prob_info.get("col", "left")
    y_top    = int(prob_info["y_top"]    / THUMB_SCALE * SCALE) - margin
    y_bottom = int(prob_info["y_bottom"] / THUMB_SCALE * SCALE) + margin
    y_top    = max(0, y_top)
    y_bottom = min(H, y_bottom)

    if col == "left":
        x0, x1 = 0, mid - 20
    else:
        x0, x1 = mid + 20, W

    rect = fitz.Rect(x0/SCALE, y_top/SCALE, x1/SCALE, y_bottom/SCALE)
    pix  = page.get_pixmap(matrix=mat, clip=rect)
    doc2.close()
    return pix


# 크롭 저장
crops = {}  # {문제번호: 이미지경로}

all_problems = bbox_p1.get("problems", []) + bbox_p3.get("problems", [])
pg_map = {4: 0, 7: 0, 18: 2}

for prob in all_problems:
    n  = prob["number"]
    pg = pg_map.get(n, 0)
    try:
        pix = crop_problem(pg, prob)
        path = OUT / f"prob_{n}.png"
        pix.save(str(path))
        crops[n] = path
        print(f"  크롭 저장: {n}번 → {path.name}  ({pix.width}×{pix.height})")
    except Exception as e:
        print(f"  크롭 실패 {n}번: {e}")

# ──────────────────────────────────────────────
# Step 4: Mathpix OCR
# ──────────────────────────────────────────────
import sys
sys.path.insert(0, ".")
try:
    from src.ocr.mathpix_client import MathpixClient
    mp = MathpixClient()

    OCR_DIR = OUT / "ocr"
    OCR_DIR.mkdir(exist_ok=True)

    print("\n=== Mathpix OCR ===")
    for n, img_path in sorted(crops.items()):
        img = img_path.read_bytes()
        raw = mp.raw_ocr_image(img)
        text = raw.get("mmd", "") or raw.get("text", "")
        out_md = OCR_DIR / f"prob_{n}.md"
        out_md.write_text(text, encoding="utf-8")
        print(f"  {n}번: {len(text)}자 → {out_md.name}")
        print(f"    {text[:120]}")
except Exception as e:
    print(f"\nMathpix OCR 실패: {e}")
    print("→ Vision fallback 필요")
