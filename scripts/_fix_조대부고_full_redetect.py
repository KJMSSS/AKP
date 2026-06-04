"""
조대부고 2026 — 회전 페이지 전체 재처리.

문제: 2·4·6페이지가 180° 뒤집혀 bbox가 완전히 잘못됨.
해결: 해당 페이지를 PyMuPDF로 180° 회전 렌더링 → bbox 재감지 → 크롭 재생성 → 재OCR.
"""
from __future__ import annotations

import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv(".env")

import fitz
from PIL import Image

ROOT     = Path(__file__).resolve().parent.parent
PDF_PATH = ROOT / "samples" / "2026" / "[2026_1_1_a_공수1_조대부고].pdf"
CROP_DIR = ROOT / "log" / "cycle_16" / "crops" / "조대부고_2026"
OCR_DIR  = CROP_DIR / "ocr"
THUMB_DIR = CROP_DIR / "thumbs"

# 회전 필요 페이지 (0-based): 페이지1=idx0, 페이지2=idx1 ...
ROTATED_PAGE_IDX = [1, 3, 5]  # 2, 4, 6번째 페이지

THUMB_DPI = 72
CROP_DPI  = 300


def render_page_rotated(pdf_path: Path, page_idx: int, dpi: int, rotation: int = 180) -> Image.Image:
    """PyMuPDF로 페이지를 회전 적용해서 PIL Image로 반환."""
    doc = fitz.open(str(pdf_path))
    page = doc[page_idx]
    mat = fitz.Matrix(dpi / 72, dpi / 72).prerotate(rotation)
    pix = page.get_pixmap(matrix=mat)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    doc.close()
    return img


def run_bbox_vision(thumb_img: Image.Image, page_idx: int) -> list[dict]:
    """Claude Vision으로 회전된 썸네일에서 bbox 감지."""
    import base64, os
    import anthropic

    buf = io.BytesIO()
    thumb_img.save(buf, format="PNG")
    data = base64.standard_b64encode(buf.getvalue()).decode()

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    W = thumb_img.width
    H = thumb_img.height
    prompt = (
        f"이 이미지는 수학 시험지 한 페이지입니다. "
        f"각 문제(번호 있는 문제)의 bounding box를 찾아 JSON 배열로 반환하세요. "
        f"이미지 크기는 {W}x{H} 픽셀입니다.\n"
        "형식: [{\"num\": 번호, \"x0\": 좌, \"y0\": 위, \"x1\": 우, \"y1\": 아래}, ...]\n"
        "서술형도 포함 (서술형1=101, 서술형2=102 등). "
        "헤더/푸터/저작권 텍스트는 제외. "
        "JSON만 반환하세요."
    )

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}},
            {"type": "text", "text": prompt},
        ]}]
    )
    text = resp.content[0].text.strip()

    import json, re
    m = re.search(r'\[.*\]', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    print(f"  [Vision] 파싱 실패: {text[:200]}")
    return []


def crop_from_rotated_page(pdf_path: Path, page_idx: int, bbox_norm: dict) -> bytes:
    """회전된 페이지 렌더링 이미지에서 bbox 영역 크롭."""
    img = render_page_rotated(pdf_path, page_idx, dpi=CROP_DPI, rotation=180)
    W, H = img.size

    x0 = max(0, int(bbox_norm["x0"] * W))
    y0 = max(0, int(bbox_norm["y0"] * H))
    x1 = min(W, int(bbox_norm["x1"] * W))
    y1 = min(H, int(bbox_norm["y1"] * H))

    cropped = img.crop((x0, y0, x1, y1))
    buf = io.BytesIO()
    cropped.save(buf, format="PNG")
    return buf.getvalue()


def main():
    from src.common.ocr.mathpix_client import MathpixClient

    print(f"{'='*60}")
    print("조대부고 2026 — 회전 페이지 재처리")
    print(f"{'='*60}\n")

    mp = MathpixClient()
    CROP_DIR.mkdir(exist_ok=True)
    OCR_DIR.mkdir(exist_ok=True)
    THUMB_DIR.mkdir(exist_ok=True)

    for page_idx in ROTATED_PAGE_IDX:
        page_num = page_idx + 1
        print(f"\n{'─'*50}")
        print(f"페이지 {page_num} (idx={page_idx}) — 180° 회전 재처리")
        print(f"{'─'*50}")

        # 1. 회전된 썸네일 생성
        print(f"  [1] 회전 썸네일 생성")
        thumb = render_page_rotated(PDF_PATH, page_idx, dpi=THUMB_DPI, rotation=180)
        thumb_path = THUMB_DIR / f"pg{page_num}_rotated_thumb.png"
        thumb.save(thumb_path)
        print(f"      저장: {thumb_path.name} ({thumb.width}×{thumb.height})")

        # 2. Vision bbox 감지
        print(f"  [2] Vision bbox 감지...")
        bboxes_raw = run_bbox_vision(thumb, page_idx)
        if not bboxes_raw:
            print(f"      감지 실패 — 스킵")
            continue
        print(f"      감지: {[b.get('num') for b in bboxes_raw]}번")

        # bbox를 0~1 정규화 (썸네일 크기 기준)
        W_t, H_t = thumb.width, thumb.height
        bboxes_norm = []
        for b in bboxes_raw:
            bboxes_norm.append({
                "num":  b["num"],
                "x0":   b.get("x0", 0) / W_t,
                "y0":   b.get("y0", 0) / H_t,
                "x1":   b.get("x1", W_t) / W_t,
                "y1":   b.get("y1", H_t) / H_t,
            })

        # 3. 크롭 생성
        print(f"  [3] 크롭 생성 + OCR")
        for b in bboxes_norm:
            num = b["num"]
            crop_bytes = crop_from_rotated_page(PDF_PATH, page_idx, b)

            crop_path = CROP_DIR / f"prob_{num}.png"
            crop_path.write_bytes(crop_bytes)
            print(f"      prob_{num}.png 저장 ({len(crop_bytes)//1024}KB)")

            # OCR 캐시 삭제 후 재OCR
            ocr_path = OCR_DIR / f"prob_{num}.md"
            if ocr_path.exists():
                ocr_path.unlink()

            raw = mp.raw_ocr_image(crop_path)
            text = raw.get("mmd", "") or raw.get("text", "")
            ocr_path.write_text(text, encoding="utf-8")
            label = f"서술형{num-100}" if num >= 100 else f"{num}번"
            print(f"      {label}: {len(text)}자 — {text[:60].strip()}")

            time.sleep(0.2)

    # raw.md 삭제 (재생성 유도)
    raw_path = CROP_DIR / "raw.md"
    if raw_path.exists():
        raw_path.unlink()
        print(f"\nraw.md 삭제 (빌드 시 재생성)")

    print(f"\n{'='*60}")
    print("재처리 완료. 다음: python scripts/_build_조대부고_2026.py")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
