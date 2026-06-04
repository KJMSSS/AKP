"""
조대부고 2026 — 2·4·6페이지 180° 회전 보정 + 재OCR.

문제: PDF 페이지 2,4,6 이미지가 물리적으로 180° 뒤집혀 있어
      크롭 이미지도 거꾸로 됨 → Mathpix OCR 실패 또는 엉뚱한 내용.

수정:
  1. 해당 페이지 문제의 크롭 PNG를 180° 회전
  2. OCR 캐시 삭제
  3. 재OCR
  4. raw.md 재조합 (캐시 삭제 → 재생성)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv(".env")

from PIL import Image
from src.common.ocr.mathpix_client import MathpixClient

ROOT     = Path(__file__).resolve().parent.parent
CROP_DIR = ROOT / "log" / "cycle_16" / "crops" / "조대부고_2026"
OCR_DIR  = CROP_DIR / "ocr"

# 페이지별 문제 번호 (build 로그 기반)
PAGE2_PROBS = [7, 8, 9, 10, 11, 12, 13]   # 2페이지 (180° 회전)
PAGE4_PROBS = [14, 15, 16, 101, 102]       # 4페이지 (180° 회전)
PAGE6_PROBS = []                            # 6페이지 (감지 안 됨, 추후 확인)

ROTATED_PROBS = PAGE2_PROBS + PAGE4_PROBS + PAGE6_PROBS


def rotate_180(png_path: Path) -> None:
    """PNG를 180° 회전하여 덮어씀."""
    img = Image.open(png_path)
    img_r = img.rotate(180)
    img_r.save(png_path)
    print(f"  회전: {png_path.name}")


def main():
    print(f"{'='*55}")
    print("조대부고 2026 — 회전 보정 + 재OCR")
    print(f"{'='*55}\n")

    mp = MathpixClient()

    print("[1] 크롭 PNG 180° 회전")
    for num in ROTATED_PROBS:
        p = CROP_DIR / f"prob_{num}.png"
        if p.exists():
            rotate_180(p)
        else:
            print(f"  {num}번: PNG 없음 — 스킵")

    print("\n[2] OCR 캐시 삭제")
    for num in ROTATED_PROBS:
        ocr = OCR_DIR / f"prob_{num}.md"
        if ocr.exists():
            ocr.unlink()
            print(f"  삭제: {ocr.name}")

    print("\n[3] raw.md 캐시 삭제 (재생성 유도)")
    raw = CROP_DIR / "raw.md"
    if raw.exists():
        raw.unlink()
        print(f"  삭제: raw.md")

    print("\n[4] 재OCR")
    results: dict[int, str] = {}
    for num in sorted(ROTATED_PROBS):
        p = CROP_DIR / f"prob_{num}.png"
        if not p.exists():
            print(f"  {num}번: PNG 없음 — 스킵")
            results[num] = ""
            continue
        raw_res = mp.raw_ocr_image(p)
        text = raw_res.get("mmd", "") or raw_res.get("text", "")
        label = f"서술형{num-100}" if num >= 100 else f"{num}번"
        print(f"  {label}: {len(text)}자")
        if text:
            print(f"    → {text[:80].strip()}")
        results[num] = text

        ocr_file = OCR_DIR / f"prob_{num}.md"
        OCR_DIR.mkdir(exist_ok=True)
        ocr_file.write_text(text, encoding="utf-8")

    print("\n[5] 결과 요약")
    ok = [n for n, t in results.items() if t.strip()]
    fail = [n for n, t in results.items() if not t.strip()]
    print(f"  OCR 성공: {ok}")
    print(f"  OCR 실패(0자): {fail}")

    if fail:
        print("\n  [경고] 0자 문제는 이미지 내용 확인 후 raw.md 수동 입력 필요")

    print("\n완료. 다음 단계: python scripts/_build_조대부고_2026.py 실행")


if __name__ == "__main__":
    main()
