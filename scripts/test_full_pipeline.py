"""
전체 파이프라인 통합 테스트: PDF/이미지 → OCR → HWPX 치환

사용법:
    py scripts/test_full_pipeline.py <이미지> <템플릿.hwpx> <출력.hwpx>
    py scripts/test_full_pipeline.py <이미지> <템플릿.hwpx> <출력.hwpx> --replace-all
    py scripts/test_full_pipeline.py <이미지> <템플릿.hwpx> <출력.hwpx> --dry-run

예시:
    py scripts/test_full_pipeline.py \\
        samples/test_image.png \\
        "samples/[2025_1_1_a_공수1_광주고][다항식의 연산 ~ 이차함수와 이차방정식][워드초벌].hwpx" \\
        samples/output.hwpx \\
        --replace-all
"""
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.common.ocr.mathpix_client import MathpixClient, MathpixError, OcrResult
from src.template_based.builder import fill_template, count_empty_scripts, count_all_scripts
from src.common.latex_to_hwp import convert as latex_to_hwp

SEP = "─" * 60


def main() -> None:
    args = sys.argv[1:]
    replace_all = "--replace-all" in args
    dry_run     = "--dry-run"     in args
    paths = [a for a in args if not a.startswith("--")]

    if len(paths) < 3:
        print("사용법: py scripts/test_full_pipeline.py <이미지> <템플릿.hwpx> <출력.hwpx> [--replace-all] [--dry-run]")
        sys.exit(1)

    image_path    = Path(paths[0])
    template_path = Path(paths[1])
    output_path   = Path(paths[2])

    for p, label in [(image_path, "이미지"), (template_path, "템플릿")]:
        if not p.exists():
            print(f"[오류] {label} 파일 없음: {p}")
            sys.exit(1)

    # ── 1. 템플릿 사전 분석 ──────────────────────────────────────
    print(SEP)
    print("[ 1단계 ] 템플릿 분석")
    print(SEP)
    empty_cnt = count_empty_scripts(template_path)
    all_cnt   = count_all_scripts(template_path)
    print(f"  템플릿 : {template_path.name}")
    print(f"  hp:script 전체 : {all_cnt}개")
    print(f"  빈 hp:script   : {empty_cnt}개")
    print(f"  채우기 모드     : {'전체 교체 (--replace-all)' if replace_all else '빈 슬롯만'}\n")

    # ── 2. Mathpix OCR ──────────────────────────────────────────
    print(SEP)
    print("[ 2단계 ] Mathpix OCR")
    print(SEP)

    app_id  = os.getenv("MATHPIX_APP_ID", "")
    app_key = os.getenv("MATHPIX_APP_KEY", "")
    if not app_id or not app_key:
        print("[오류] .env에 Mathpix 키가 없습니다.")
        sys.exit(1)

    try:
        client = MathpixClient()
        print(f"  파일 : {image_path}  ({image_path.stat().st_size:,} bytes)")
        print("  API 호출 중...")
        raw = client.raw_ocr_image(image_path)
        ocr_result = OcrResult.from_response(raw)
    except MathpixError as e:
        print(f"[실패] {e}")
        sys.exit(1)

    formula_blocks = [b for b in ocr_result.blocks if b.kind in ("formula_inline", "formula_display")]
    print(f"  신뢰도  : {ocr_result.confidence:.1%}")
    print(f"  블록 수 : {len(ocr_result.blocks)}개 (수식 {len(formula_blocks)}개)")

    print(f"\n  수식 블록 → HWP Script 변환 미리보기:")
    for i, block in enumerate(formula_blocks, 1):
        hwp = latex_to_hwp(block.content)
        print(f"    [{i:02d}] LaTeX : {block.content[:50]}")
        print(f"          HWP   : {hwp[:50]}")
    print()

    if dry_run:
        print("[dry-run] 파일 저장 없이 종료합니다.")
        return

    # ── 3. HWPX 치환 ────────────────────────────────────────────
    print(SEP)
    print("[ 3단계 ] HWPX 수식 치환")
    print(SEP)

    result = fill_template(
        template_path = template_path,
        ocr_result    = ocr_result,
        output_path   = output_path,
        replace_all   = replace_all,
    )

    print(f"  채워진 수식 : {result.filled}개  /  전체 슬롯 {result.total_scripts}개")
    if result.skipped:
        print(f"  ⚠ 슬롯 부족으로 미채움 : {result.skipped}개")
    print(f"  출력 파일   : {output_path}  ({output_path.stat().st_size:,} bytes)")

    # ── 4. 검증 ─────────────────────────────────────────────────
    print()
    print(SEP)
    print("[ 4단계 ] 출력 파일 검증")
    print(SEP)

    import zipfile, xml.dom.minidom
    with zipfile.ZipFile(output_path, "r") as zf:
        xml_bytes = zf.read("Contents/section0.xml")

    # 채워진 script 몇 개인지 재확인
    import re
    filled_scripts = re.findall(r'<hp:script>(.+?)</hp:script>', xml_bytes.decode("utf-8"), re.DOTALL)
    print(f"  출력 파일의 채워진 hp:script: {len(filled_scripts)}개")
    print(f"  샘플 (처음 3개):")
    for i, sc in enumerate(filled_scripts[:3], 1):
        print(f"    [{i}] {sc[:60]}")

    print()
    print("✓ 완료. 한글에서 출력 파일을 열어 수식을 확인하세요.")


if __name__ == "__main__":
    main()
