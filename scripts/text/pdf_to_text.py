"""
PDF → 빈 HWPX 직접 타이핑 변환 (템플릿 불필요)

사용법:
    py scripts/text/pdf_to_text.py [PDF경로]
    py scripts/text/pdf_to_text.py [PDF경로] --filter-handwriting

출력:
    samples/output_text_{파일명}.hwpx
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from src.common.ocr.mathpix_client import MathpixClient
from src.text_only.text_builder import build_from_markdown
from src.text_only.handwriting_filter import filter_handwriting
from src.text_only.ocr_fallback import apply_fallback, reinforce_placeholders
from src.common.hwpx_namespace_fixer import fix_hwpx_namespaces
from src.common.hwpx_validator import validate_hwpx, HWPXValidationError

# ── 설정 ──────────────────────────────────────────────────────────────
ROOT        = Path(__file__).resolve().parent.parent.parent
SAMPLES_DIR = ROOT / "samples"

# header.xml 참조용 기준 템플릿 (가장 단순한 것)
_BASE_TEMPLATES = list(SAMPLES_DIR.glob("*.hwpx"))
_BASE_TEMPLATE  = next(
    (f for f in _BASE_TEMPLATES if "워드초벌" in f.name and "]1." not in f.name),
    None,
)


def _pick_template() -> Path:
    if _BASE_TEMPLATE and _BASE_TEMPLATE.exists():
        return _BASE_TEMPLATE
    # fallback: 첫 번째 hwpx 파일
    for f in SAMPLES_DIR.glob("*.hwpx"):
        return f
    raise FileNotFoundError("samples/ 폴더에 .hwpx 파일이 없습니다.")


def convert(pdf_path: Path, filter_hw: bool = False) -> Path:
    stem    = pdf_path.stem
    out_md  = ROOT / "output_text_temp.md"          # 임시 마크다운 저장
    out_hwpx = SAMPLES_DIR / f"output_text_{stem}.hwpx"

    print("─" * 62)
    print("[ 1단계 ] PDF OCR")
    print("─" * 62)
    print(f"  PDF: {pdf_path.name}")

    client = MathpixClient()
    t0 = time.time()

    pdf_id = client.submit_pdf(pdf_path)
    print(f"  제출 완료 (pdf_id={pdf_id})")

    client.poll_pdf(pdf_id, progress=True)
    md = client.fetch_pdf_markdown(pdf_id)

    ocr_time = time.time() - t0
    print(f"  마크다운: {len(md):,}자  ({ocr_time:.1f}s)")

    # 마크다운 저장 (디버그용)
    out_md.write_text(md, encoding='utf-8')
    print(f"  마크다운 저장: {out_md.name}")

    raw_md_for_reinforce = md  # 보강 시 손상 카운트의 기준
    md = apply_fallback(md, pdf_path)

    if filter_hw:
        print()
        print("─" * 62)
        print("[ 1.5단계 ] 손글씨 풀이 제거 (Claude AI)")
        print("─" * 62)
        t_filter = time.time()
        md_filtered = filter_handwriting(md)
        filter_time = time.time() - t_filter
        removed = len(md) - len(md_filtered)
        print(f"  원본: {len(md):,}자  →  필터 후: {len(md_filtered):,}자  (제거: {removed:+,}자, {filter_time:.1f}s)")

        # 필터가 ★ 플레이스홀더를 일부 제거했을 수 있어 문항 단위로 강제 재삽입
        md_reinforced, added = reinforce_placeholders(md_filtered, raw_md_for_reinforce)
        if added:
            print(f"  [reinforce] 필터가 누락한 ★ 마커 {added}건 재삽입")
        md_filtered = md_reinforced

        out_md_filtered = ROOT / "output_text_temp_filtered.md"
        out_md_filtered.write_text(md_filtered, encoding='utf-8')
        print(f"  필터 마크다운 저장: {out_md_filtered.name}")
        md = md_filtered

    print()
    print("─" * 62)
    print("[ 2단계 ] HWPX 생성")
    print("─" * 62)

    base = _pick_template()
    print(f"  헤더 참조: {base.name}")

    t1 = time.time()
    result = build_from_markdown(md, out_hwpx, base)
    build_time = time.time() - t1

    print(f"  문단: {result['paragraphs']}개  수식: {result['equations']}개")
    print(f"  생성 시간: {build_time:.1f}s")
    print(f"  파일 크기: {out_hwpx.stat().st_size:,} bytes")

    print()
    print("─" * 62)
    print("[ 2.5단계 ] HWPX 구조 검증")
    print("─" * 62)
    fix_hwpx_namespaces(str(out_hwpx))
    struct_errs = validate_hwpx(str(out_hwpx))
    if struct_errs:
        print(f"  ✗ FAIL ({len(struct_errs)}건):")
        for e in struct_errs:
            print(f"    - {e}")
        raise HWPXValidationError(
            f"HWPX 구조 검증 실패 ({len(struct_errs)}건): {out_hwpx.name}\n"
            "학원장 보고 필요 — src/common/hwpx_validator.py fix_hwpx() 참조"
        )
    print("  ✓ PASS")

    print()
    print("─" * 62)
    print(" 완료")
    print("─" * 62)
    print(f"  출력: {out_hwpx}")

    return out_hwpx


if __name__ == "__main__":
    args = sys.argv[1:]
    filter_hw = "--filter-handwriting" in args
    positional = [a for a in args if not a.startswith("--")]

    if not positional:
        print("사용법: py scripts/text/pdf_to_text.py [PDF경로] [--filter-handwriting]")
        sys.exit(1)

    pdf = Path(positional[0])
    if not pdf.exists():
        # samples/ 폴더 자동 탐색
        cand = SAMPLES_DIR / pdf.name
        if cand.exists():
            pdf = cand
        else:
            print(f"파일 없음: {pdf}")
            sys.exit(1)

    convert(pdf, filter_hw=filter_hw)
