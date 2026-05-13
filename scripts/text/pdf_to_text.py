"""
PDF → 빈 HWPX 직접 타이핑 변환 (템플릿 불필요)

사용법:
    py scripts/text/pdf_to_text.py [PDF경로]

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


def convert(pdf_path: Path) -> Path:
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
    print(" 완료")
    print("─" * 62)
    print(f"  출력: {out_hwpx}")

    return out_hwpx


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: py scripts/text/pdf_to_text.py [PDF경로]")
        sys.exit(1)

    pdf = Path(sys.argv[1])
    if not pdf.exists():
        # samples/ 폴더 자동 탐색
        cand = SAMPLES_DIR / pdf.name
        if cand.exists():
            pdf = cand
        else:
            print(f"파일 없음: {pdf}")
            sys.exit(1)

    convert(pdf)
