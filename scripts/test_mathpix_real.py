"""
Mathpix API 실제 호출 테스트

사용법:
    py scripts/test_mathpix_real.py samples/test_image.png
"""
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (패키지 임포트용)
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import os
from src.ocr.mathpix_client import MathpixClient, MathpixError


def main() -> None:
    # ── 인자 확인 ────────────────────────────────────────────────
    if len(sys.argv) < 2:
        print("사용법: py scripts/test_mathpix_real.py <이미지 경로>")
        print("예시:   py scripts/test_mathpix_real.py samples/test_image.png")
        sys.exit(1)

    image_path = Path(sys.argv[1])
    if not image_path.exists():
        print(f"[오류] 파일을 찾을 수 없습니다: {image_path}")
        sys.exit(1)

    # ── 키 로드 확인 ─────────────────────────────────────────────
    app_id  = os.getenv("MATHPIX_APP_ID", "")
    app_key = os.getenv("MATHPIX_APP_KEY", "")

    if not app_id or not app_key:
        print("[오류] .env 파일에 MATHPIX_APP_ID / MATHPIX_APP_KEY가 없습니다.")
        print("       .env.example을 참고해 .env 파일을 만드세요.")
        sys.exit(1)

    print(f"[정보] APP_ID  : {app_id[:8]}{'*' * (len(app_id) - 8)}")
    print(f"[정보] APP_KEY : {app_key[:6]}{'*' * 10}  (일부만 표시)")
    print(f"[정보] 파일    : {image_path}  ({image_path.stat().st_size:,} bytes)\n")

    # ── API 호출 ─────────────────────────────────────────────────
    try:
        client = MathpixClient()
        print("Mathpix API 호출 중...")
        result = client.ocr_image(image_path)
    except MathpixError as e:
        print(f"[실패] {e}")
        sys.exit(1)

    # ── 결과 출력 ────────────────────────────────────────────────
    print(f"\n총 블록 수: {len(result.blocks)}\n")
    print("─" * 60)

    for i, block in enumerate(result.blocks, 1):
        label = {
            "text":            "텍스트",
            "formula_display": "수식(display)",
        }.get(block.kind, block.kind)

        print(f"[블록 {i}] {label}")
        print(block.content)
        print("─" * 60)

    # raw 응답 중 latex_styled 도 표시 (수식 전체 확인용)
    if latex := result.raw.get("latex_styled"):
        print("\n[raw] latex_styled:")
        print(latex)


if __name__ == "__main__":
    main()
