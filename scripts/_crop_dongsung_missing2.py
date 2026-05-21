"""동성고 4·7·18번 크롭 OCR — 재시도 (모듈 경로 수정 + 7/18번 재감지)."""
import sys, base64, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")

import fitz
import anthropic

DPI    = 300
SCALE  = DPI / 72
PDF    = Path("samples/11b/[2025_1_1_b_공수1_동성고].pdf")
OUT    = Path("log/cycle_16/dongsung_crops")
OUT.mkdir(parents=True, exist_ok=True)

CLIENT = anthropic.Anthropic()

# ──────────────────────────────────────────────
# Step 1: 썸네일 확인 (이미 있으면 재사용)
# ──────────────────────────────────────────────
doc = fitz.open(str(PDF))

def page_thumb(page_idx, dpi=100):
    page = doc[page_idx]
    mat  = fitz.Matrix(dpi/72, dpi/72)
    pix  = page.get_pixmap(matrix=mat)
    return pix

for pg, name in [(0, "pg1_thumb.png"), (1, "pg2_thumb.png"), (2, "pg3_thumb.png")]:
    p = OUT / name
    if not p.exists():
        pix = page_thumb(pg, dpi=100)
        pix.save(str(p))
        print(f"  썸네일 생성: {name}")
    else:
        print(f"  썸네일 재사용: {name}")

doc.close()

# ──────────────────────────────────────────────
# Step 2: bbox 감지 — 페이지 여러 곳 시도
# ──────────────────────────────────────────────
DETECT_PROMPT = """\
이 이미지는 2컬럼 레이아웃의 수학 시험지 페이지입니다.
규칙:
1. 인쇄된 아라비아 숫자로 시작하는 문제(예: "1.", "2.", "3." 또는 "1．")만 찾으세요.
2. 학생 손글씨, 빨간 표시, 헤더/푸터는 무시하세요.
3. 보기, 조건, 선택지까지 포함한 문제 전체 범위로 bbox를 지정하세요.

찾을 문제: {target_probs}
이미지 크기: 너비 {img_w}px, 높이 {img_h}px

JSON만 출력 (설명 없이):
{{
  "problems": [
    {{"number": N, "y_top": 픽셀, "y_bottom": 픽셀, "col": "left" 또는 "right"}}
  ]
}}

문제가 보이지 않으면 problems를 빈 배열로 반환."""


def detect_bbox(pg_idx, target_probs):
    img_path = OUT / f"pg{pg_idx+1}_thumb.png"
    b64 = base64.standard_b64encode(img_path.read_bytes()).decode()
    doc2 = fitz.open(str(PDF))
    pix_tmp = doc2[pg_idx].get_pixmap(matrix=fitz.Matrix(100/72, 100/72))
    img_w, img_h = pix_tmp.width, pix_tmp.height
    doc2.close()

    msg = CLIENT.messages.create(
        model="claude-opus-4-7", max_tokens=512,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            {"type": "text", "text": DETECT_PROMPT.format(
                target_probs=target_probs,
                img_w=img_w, img_h=img_h
            )},
        ]}],
    )
    raw = msg.content[0].text.strip()
    print(f"  [raw] {raw[:300]}")
    try:
        start = raw.find('{')
        end   = raw.rfind('}') + 1
        return json.loads(raw[start:end])
    except Exception as e:
        print(f"  [parse error] {e}")
        return {"problems": []}


print("\n=== bbox 감지 ===")
# 4번·7번: page 1 시도
bbox_p1 = detect_bbox(0, "4번, 7번")
print(f"page1 감지: {bbox_p1}")

# 7번이 page1에서 안 나오면 page2 시도
probs_found_p1 = {p["number"] for p in bbox_p1.get("problems", [])}
if 7 not in probs_found_p1:
    print("\n  [7번 page1 미감지 → page2 시도]")
    bbox_p2 = detect_bbox(1, "7번")
    probs_found_p2 = {p["number"] for p in bbox_p2.get("problems", [])}
    print(f"page2 감지: {bbox_p2}")
    # page2 결과를 page1에 합침 (pg_map에서 처리)
else:
    bbox_p2 = {"problems": []}

# 18번: page3 시도
bbox_p3 = detect_bbox(2, "18번")
print(f"page3 감지: {bbox_p3}")

# 18번 page3에서 안 나오면 page2 재시도
probs_found_p3 = {p["number"] for p in bbox_p3.get("problems", [])}
if 18 not in probs_found_p3:
    print("\n  [18번 page3 미감지 → page2 시도]")
    bbox_p2_18 = detect_bbox(1, "18번")
    print(f"page2(18번) 감지: {bbox_p2_18}")
else:
    bbox_p2_18 = {"problems": []}

# ──────────────────────────────────────────────
# Step 3: 크롭 (이미 있으면 재사용)
# ──────────────────────────────────────────────
THUMB_DPI   = 100
THUMB_SCALE = THUMB_DPI / 72


def crop_problem(pg_idx, prob_info, margin=40):
    doc2 = fitz.open(str(PDF))
    page = doc2[pg_idx]
    mat  = fitz.Matrix(SCALE, SCALE)
    pix_full = page.get_pixmap(matrix=mat)
    W, H = pix_full.width, pix_full.height
    mid  = W // 2

    col      = prob_info.get("col", "left")
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


# 감지 결과 수집 (번호 → (페이지, prob_info))
all_probs = {}

# page1 결과
for p in bbox_p1.get("problems", []):
    all_probs[p["number"]] = (0, p)

# page2 결과 (7번 fallback)
for p in bbox_p2.get("problems", []):
    if p["number"] not in all_probs:
        all_probs[p["number"]] = (1, p)

# page2 결과 (18번 fallback)
for p in bbox_p2_18.get("problems", []):
    if p["number"] not in all_probs:
        all_probs[p["number"]] = (1, p)

# page3 결과
for p in bbox_p3.get("problems", []):
    if p["number"] not in all_probs:
        all_probs[p["number"]] = (2, p)

# 4번은 이미 크롭됨 → 재크롭 스킵
crops = {}

NEEDED = {4, 7, 18}
print(f"\n=== 크롭 ===\n감지된 번호: {set(all_probs.keys())}")

for n in NEEDED:
    out_path = OUT / f"prob_{n}.png"
    if n not in all_probs:
        print(f"  {n}번: bbox 미감지 → 스킵")
        continue
    if out_path.exists() and n == 4:
        print(f"  {n}번: 기존 크롭 재사용 ({out_path.name})")
        crops[n] = out_path
        continue
    pg, prob_info = all_probs[n]
    try:
        pix = crop_problem(pg, prob_info)
        pix.save(str(out_path))
        crops[n] = out_path
        print(f"  {n}번: 저장 완료 → {out_path.name} ({pix.width}×{pix.height})")
    except Exception as e:
        print(f"  {n}번: 크롭 실패 — {e}")

# ──────────────────────────────────────────────
# Step 4: Mathpix OCR
# ──────────────────────────────────────────────
from src.common.ocr.mathpix_client import MathpixClient

mp = MathpixClient()
OCR_DIR = OUT / "ocr"
OCR_DIR.mkdir(exist_ok=True)

print("\n=== Mathpix OCR ===")
for n in sorted(crops):
    img_path = crops[n]
    try:
        raw = mp.raw_ocr_image(img_path)
        text = raw.get("mmd", "") or raw.get("text", "")
        out_md = OCR_DIR / f"prob_{n}.md"
        out_md.write_text(text, encoding="utf-8")
        print(f"  {n}번: {len(text)}자 → {out_md.name}")
        print(f"    미리보기: {text[:200]}")
        print()
    except Exception as e:
        print(f"  {n}번: OCR 실패 — {e}")

print("\n=== 완료 ===")
print(f"크롭: {sorted(crops.keys())}")
print(f"OCR 결과 위치: {OCR_DIR}")
