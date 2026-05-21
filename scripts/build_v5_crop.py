"""
크롭 OCR 파이프라인 — 전 학교 v5 빌드.

사용법:
  python scripts/build_v5_crop.py               # 전 학교
  python scripts/build_v5_crop.py 동성고         # 특정 학교만
  python scripts/build_v5_crop.py 동성고 대성여고  # 여러 학교
  python scripts/build_v5_crop.py --no-cache     # 크롭/OCR 캐시 무시
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")

from src.pipeline.crop_ocr_builder import build_one_crop

ROOT     = Path(__file__).resolve().parent.parent
SRC_DIR  = ROOT / "samples" / "11b"
PROD_DIR = ROOT / "samples" / "11b_production"
CROP_ROOT = ROOT / "log" / "cycle_16" / "crops"

PROD_DIR.mkdir(exist_ok=True)

# 전 학교 목록 (PDF 파일 기준 자동 탐색)
ALL_SCHOOLS = sorted(
    p.stem.removeprefix("[").removesuffix("]").replace("2025_1_1_b_공수1_", "")
    for p in SRC_DIR.glob("[[]2025_1_1_b_공수1_*].pdf")
    if "광주고" not in p.stem  # 광주고는 사진 PDF — 별도 트랙
)


def main():
    args = sys.argv[1:]
    no_cache = "--no-cache" in args
    args = [a for a in args if not a.startswith("--")]

    targets = args if args else ALL_SCHOOLS

    print(f"처리 대상: {targets}")
    print(f"캐시 사용: {'아니오' if no_cache else '예'}\n")

    ok, fail = [], []

    for school in targets:
        source = f"2025_1_1_b_공수1_{school}"
        crop_dir = CROP_ROOT / school

        # --no-cache: 크롭/OCR 캐시 삭제
        if no_cache and crop_dir.exists():
            import shutil
            shutil.rmtree(crop_dir)

        try:
            _, verify = build_one_crop(
                source=source,
                src_dir=SRC_DIR,
                prod_dir=PROD_DIR,
                crop_dir=crop_dir,
            )
            if verify["pass"]:
                ok.append(school)
            else:
                fail.append(f"{school}(검증실패)")
        except Exception as e:
            import traceback
            print(f"\n[ERROR] {school}: {e}")
            traceback.print_exc()
            fail.append(school)

    print(f"\n\n{'='*55}")
    print(f"완료: {ok}")
    if fail:
        print(f"실패: {fail}")


if __name__ == "__main__":
    main()
