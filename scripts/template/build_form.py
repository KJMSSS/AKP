"""
양식(Form) HWPX 빌더 실행 스크립트

사용법:
    py scripts/template/build_form.py [PDF경로] [--md MARKDOWN경로]

  PDF경로  : samples/ 폴더 자동 탐색
  --md     : 기존 OCR 마크다운 재사용 (생략 시 Mathpix OCR 실행)

출력:
    samples/form_{년}_{학년}_{학기}_{시험}_{과목}_{학교}.hwpx
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from src.template_based.form_builder import build_form, parse_filename, extract_scores
from src.common.ocr.mathpix_client import MathpixClient

ROOT        = Path(__file__).resolve().parent.parent.parent
SAMPLES_DIR = ROOT / "samples"


def _ocr_pdf(pdf_path: Path) -> str:
    """Mathpix OCR 실행 → 마크다운 반환 + samples/ 에 저장."""
    client = MathpixClient()
    print("  OCR 제출 중...")
    t0     = time.time()
    pdf_id = client.submit_pdf(pdf_path)
    print(f"  pdf_id = {pdf_id}")
    client.poll_pdf(pdf_id, progress=True)
    md = client.fetch_pdf_markdown(pdf_id)
    elapsed = time.time() - t0

    save_path = SAMPLES_DIR / f"{pdf_path.stem}.ocr.md"
    save_path.write_text(md, encoding='utf-8')
    print(f"  OCR 완료 ({elapsed:.1f}s)  마크다운 저장: {save_path.name}")
    return md


def run(pdf_path: Path, md_path: Path | None = None) -> None:
    # ── 파일명 미리 파싱 (오류 조기 발견) ──────────────────────────
    info = parse_filename(pdf_path)
    out_name = (f"form_{info['year']}_{info['grade']}_{info['semester']}_"
                f"{info['type']}_{info['subject_key']}_{info['school_short']}.hwpx")
    output_path = SAMPLES_DIR / out_name

    print("─" * 62)
    print("[ 1단계 ] OCR 마크다운 확보")
    print("─" * 62)

    # OCR 마크다운 가져오기
    if md_path:
        if not md_path.exists():
            print(f"  오류: 파일 없음 — {md_path}")
            sys.exit(1)
        md_text = md_path.read_text(encoding='utf-8')
        print(f"  기존 마크다운 사용: {md_path.name}  ({len(md_text):,}자)")
    else:
        md_text = _ocr_pdf(pdf_path)

    scores = extract_scores(md_text)
    print(f"  추출된 배점: {len(scores)}개  {scores[:5]}{'...' if len(scores)>5 else ''}")

    print()
    print("─" * 62)
    print("[ 2단계 ] 양식 HWPX 생성")
    print("─" * 62)

    t1     = time.time()
    result = build_form(pdf_path, SAMPLES_DIR, output_path, md_text=md_text)
    elapsed = time.time() - t1

    # ── 결과 보고 ──────────────────────────────────────────────────
    inf  = result['info']
    tmpl = result['template']
    print()
    print("=" * 62)
    print("✓ 양식 생성 완료")
    print("=" * 62)
    print(f"  학교   : {inf['school_full']} ({inf['school_short']})")
    print(f"  학년   : {inf['grade']}학년 {inf['semester']}학기 {inf['type_display']}")
    print(f"  과목   : {inf['subject_display']}")
    print(f"  연도   : {inf['year']}년")
    print(f"  사용 헤더: {tmpl.name}")
    print(f"  총 문항  : {result['n_questions']}문항")
    print(f"  생성 시간: {elapsed:.1f}s")
    print()
    print(f"  자동 채움  : 학교, 학년, 학기, 시험종류, 과목, 연도 + 배점")
    print(f"  비워둠     : 범위, 지역, 문항 번호, 선택지, 본문")
    print()
    print(f"  출력 파일  : {output_path}")
    print()
    print("─" * 62)
    print("다음 단계")
    print("─" * 62)
    print(f"  한글에서  {out_name}  열고:")
    print(f"  1. 헤더 '범위' 자리에 시험 범위 입력")
    print(f"  2. 헤더 '지역' 자리에 지역 입력")
    print(f"  3. 각 문항 자리에 output_text_xxx.hwpx 내용 복붙")


if __name__ == '__main__':
    args = sys.argv[1:]

    pdf_arg: str | None = None
    md_arg:  str | None = None
    i = 0
    while i < len(args):
        if args[i] == '--md' and i + 1 < len(args):
            md_arg = args[i + 1]
            i += 2
        else:
            pdf_arg = args[i]
            i += 1

    if not pdf_arg:
        print("사용법: py scripts/template/build_form.py [PDF경로] [--md MARKDOWN경로]")
        sys.exit(1)

    pdf_path = Path(pdf_arg)
    if not pdf_path.exists():
        cand = SAMPLES_DIR / pdf_path.name
        if cand.exists():
            pdf_path = cand
        else:
            print(f"파일 없음: {pdf_arg}")
            sys.exit(1)

    md_path = Path(md_arg) if md_arg else None

    run(pdf_path, md_path)
