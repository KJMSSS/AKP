"""
PDF → Mathpix OCR → 수식 블록 추출 → JSON 저장

사용법:
    py scripts/ocr_pdf.py <PDF경로> [--template <hwpx경로>] [--out <json경로>]

예시:
    py scripts/ocr_pdf.py "samples/[2025_2_1_a_확통_경신여고].pdf" \
        --template "samples/[2025_2_1_a_확통_경신여고][순열 ~ 확률의 뜻과 활용][워드초벌].hwpx" \
        --out samples/ocr_확통.json
"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.common.ocr.mathpix_client import MathpixClient, MathpixError
from src.template_based.builder import count_all_scripts
from src.common.latex_to_hwp import convert as latex_to_hwp

SEP = "─" * 60


def parse_args():
    args = sys.argv[1:]
    pdf_path = None
    template_path = None
    out_path = None
    i = 0
    while i < len(args):
        if args[i] == "--template" and i + 1 < len(args):
            template_path = Path(args[i + 1]); i += 2
        elif args[i] == "--out" and i + 1 < len(args):
            out_path = Path(args[i + 1]); i += 2
        else:
            pdf_path = Path(args[i]); i += 1
    return pdf_path, template_path, out_path


def main():
    pdf_path, template_path, out_path = parse_args()

    if pdf_path is None or not pdf_path.exists():
        print("사용법: py scripts/ocr_pdf.py <PDF경로> [--template <hwpx>] [--out <json>]")
        sys.exit(1)

    # ── 1. 템플릿 슬롯 수 (있을 경우) ───────────────────────────────
    slot_count = None
    if template_path and template_path.exists():
        slot_count = count_all_scripts(template_path)
        print(SEP)
        print(f"[ 템플릿 ] {template_path.name}")
        print(f"  hp:script 슬롯: {slot_count}개  (replace_all=True 필요)")

    # ── 2. PDF OCR ──────────────────────────────────────────────────
    print(SEP)
    print(f"[ PDF OCR ] {pdf_path.name}  ({pdf_path.stat().st_size / 1024 / 1024:.1f} MB)")
    print("  Mathpix PDF API 제출 중...")

    try:
        client = MathpixClient()
        result = client.ocr_pdf_to_result(pdf_path, progress=True)
    except MathpixError as e:
        print(f"[실패] {e}")
        sys.exit(1)

    # ── 3. 결과 요약 ────────────────────────────────────────────────
    formula_blocks = [b for b in result.blocks if b.kind in ("formula_inline", "formula_display")]
    text_blocks    = [b for b in result.blocks if b.kind == "text"]

    print(SEP)
    print("[ OCR 결과 ]")
    print(f"  전체 블록   : {len(result.blocks)}개")
    print(f"  수식 블록   : {len(formula_blocks)}개")
    print(f"  텍스트 블록 : {len(text_blocks)}개")
    if slot_count:
        diff = len(formula_blocks) - slot_count
        sign = "+" if diff >= 0 else ""
        print(f"  슬롯 대비   : {sign}{diff}  (슬롯={slot_count}, 수식={len(formula_blocks)})")

    # ── 4. 수식 미리보기 ────────────────────────────────────────────
    print(f"\n  [ 처음 20개 수식 → HWP Script 변환 미리보기 ]")
    for i, blk in enumerate(formula_blocks[:20], 1):
        hwp = latex_to_hwp(blk.content)
        print(f"  [{i:03d}] {blk.kind:<16}  LaTeX: {blk.content[:45]}")
        print(f"        {'':16}  HWP  : {hwp[:45]}")

    if len(formula_blocks) > 20:
        print(f"  ... (이하 {len(formula_blocks) - 20}개 생략)")

    # ── 5. JSON 저장 ────────────────────────────────────────────────
    if out_path is None:
        out_path = pdf_path.with_suffix(".ocr.json")

    payload = {
        "source_pdf": str(pdf_path),
        "total_blocks": len(result.blocks),
        "formula_count": len(formula_blocks),
        "slot_count": slot_count,
        "formulas": [
            {
                "index": i,
                "kind": b.kind,
                "latex": b.content,
                "hwp": latex_to_hwp(b.content),
            }
            for i, b in enumerate(formula_blocks, 1)
        ],
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(SEP)
    print(f"[ 저장 완료 ] {out_path}")

    # ── 6. 매칭 전략 안내 ───────────────────────────────────────────
    print(SEP)
    print("[ 매칭 전략 선택 ] — 아래 중 하나를 선택하세요")
    print()
    print("  [A] 단순 순서 매칭 (추천)")
    print("      OCR 수식 #1 → 슬롯 #1, #2 → #2, ...")
    if slot_count:
        over = len(formula_blocks) - slot_count
        if over > 0:
            print(f"      ⚠ 수식({len(formula_blocks)}) > 슬롯({slot_count}): 마지막 {over}개 수식은 버림")
        elif over < 0:
            print(f"      ⚠ 수식({len(formula_blocks)}) < 슬롯({slot_count}): 마지막 {-over}개 슬롯 원본 유지")
    print()
    print("  [B] 문항 번호 인식 매칭")
    print("      OCR 텍스트에서 '1.', '2.' 패턴을 찾아 문항 단위로 그룹핑 후 매핑")
    print("      → 정확도↑ but 구현 복잡")
    print()
    print("  [C] AI 검증 추가 (Claude API)")
    print("      각 수식 블록을 LLM이 검증하여 오인식 필터링 후 매칭")
    print()
    print("  결정 후 다음 명령으로 실행하세요:")
    if template_path:
        out_hwpx = template_path.parent / template_path.name.replace("[워드초벌]", "[완성]")
        print(f"  py scripts/fill_from_ocr.py {out_path} \"{template_path}\" \"{out_hwpx}\"")


if __name__ == "__main__":
    main()
