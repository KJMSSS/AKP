"""
PDF → 빈 HWPX 직접 타이핑 변환 (템플릿 불필요)

사용법:
    py scripts/text/pdf_to_text.py [PDF경로]
    py scripts/text/pdf_to_text.py [PDF경로] --filter-handwriting
    py scripts/text/pdf_to_text.py [PDF경로] --ocr-engine claude
    py scripts/text/pdf_to_text.py [PDF경로] --ocr-engine claude --full-content

옵션:
    --ocr-engine mathpix  (기본값) Mathpix API 사용
    --ocr-engine claude   Claude API 직접 사용 (Mathpix 구독 불필요)
    --full-content        정답·해설 포함 전체 내용 전사 (--ocr-engine claude 전용)

출력:
    samples/output_text_{파일명}.hwpx
"""
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from src.common.ocr.mathpix_client import MathpixClient
from src.ocr.claude_pdf_reader import read_pdf_as_markdown
from src.text_only.text_builder import build_from_markdown
from src.text_only.handwriting_filter import filter_handwriting
from src.text_only.ocr_fallback import apply_fallback, reinforce_placeholders
from src.text_only.problem_segmenter import parse_problems, rebuild_markdown
from src.common.image_extractor import extract_images, extract_figures_by_vision
from src.common.hwpx_image_inserter import insert_figure_placeholder
from src.common.hwpx_table_inserter import replace_condition_tables, replace_boilerplate_tables
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


def convert(pdf_path: Path, filter_hw: bool = False, ocr_engine: str = "mathpix", full_content: bool = False) -> Path:
    stem    = pdf_path.stem
    out_md  = ROOT / "output_text_temp.md"          # 임시 마크다운 저장
    out_hwpx = SAMPLES_DIR / f"output_text_{stem}.hwpx"

    print("─" * 62)
    print(f"[ 1단계 ] PDF OCR  (엔진: {ocr_engine})")
    print("─" * 62)
    print(f"  PDF: {pdf_path.name}")

    t0 = time.time()

    if ocr_engine == "claude":
        md = read_pdf_as_markdown(pdf_path, full_content=full_content)
    else:
        client = MathpixClient()
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

    # 문제 파싱 + 그림 감지 + 마커 삽입
    header, segments = parse_problems(md)
    fig_dir = ROOT / "log" / "figures_tmp"
    fig_dir.mkdir(parents=True, exist_ok=True)

    # Claude OCR이 출력한 【★ 그림:N번】 마커 감지
    figure_items_from_claude: set[str] = set()
    if ocr_engine == "claude":
        for seg in segments:
            m = re.search(r'【★ 그림:(\d+)번】', seg.problem_text)
            if m:
                figure_items_from_claude.add(m.group(1))

    # PyMuPDF 그림 추출
    figure_map: dict[str, Path] = {}
    try:
        figures = extract_images(pdf_path, fig_dir, dpi=150)
        for f in figures:
            if f.item_no:
                figure_map[f.item_no] = f.image_path
        if figure_map:
            print(f"  그림 감지(PyMuPDF): {len(figure_map)}건 ({', '.join(sorted(figure_map))}번)")
    except Exception as e:
        print(f"  그림 감지 실패 (무시): {e}")

    # Vision 폴백: Claude 마커 있는데 PyMuPDF가 못 찾은 경우
    # ★ Vision은 '추출'만 담당 — Claude가 마킹하지 않은 문제는 추가하지 않음
    unresolved = figure_items_from_claude - set(figure_map)
    if unresolved:
        print(f"  그림 Vision 폴백 ({len(unresolved)}건): {sorted(unresolved)}")
        import fitz as _fitz
        render_dir = fig_dir / "pages"
        render_dir.mkdir(exist_ok=True)
        doc = _fitz.open(str(pdf_path))
        page_pngs: list[Path] = []
        for i, page in enumerate(doc):
            p = render_dir / f"page{i}.png"
            if not p.exists():
                page.get_pixmap(dpi=150).save(str(p))
            page_pngs.append(p)
        doc.close()
        try:
            vision_map = extract_figures_by_vision(
                page_pngs, fig_dir, api_key=os.environ.get("ANTHROPIC_API_KEY", "")
            )
            # Claude 마커 있는 문제만 figure_map에 추가 (false-positive 차단)
            for no, path in vision_map.items():
                if no in figure_items_from_claude:
                    figure_map[no] = path
        except Exception as e:
            print(f"  Vision 그림 실패: {e}")

    # rebuild: Claude 마커는 problem_text에 이미 있으므로 figure_items 추가 없음
    md = rebuild_markdown(header, segments)

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
    out_hwpx = result['output']  # 잠금으로 인해 대체 경로에 저장된 경우 반영
    replace_condition_tables(out_hwpx)
    replace_boilerplate_tables(out_hwpx)
    build_time = time.time() - t1

    print(f"  문단: {result['paragraphs']}개  수식: {result['equations']}개")
    print(f"  생성 시간: {build_time:.1f}s")
    print(f"  파일 크기: {out_hwpx.stat().st_size:,} bytes")

    # 그림 삽입: Claude 마커 기준만 (Vision 감지 추가분 배제)
    if figure_items_from_claude:
        print()
        print("─" * 62)
        print("[ 2.3단계 ] 그림 삽입")
        print("─" * 62)
        for item_no in sorted(figure_items_from_claude, key=lambda x: int(x)):
            if item_no not in figure_map:
                print(f"  {item_no}번 PNG 없음 — 플레이스홀더 유지")
                continue
            try:
                insert_figure_placeholder(out_hwpx, item_no, figure_map[item_no])
                print(f"  {item_no}번 그림 삽입 완료")
            except Exception as e:
                print(f"  {item_no}번 그림 삽입 실패: {e}")

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
    filter_hw    = "--filter-handwriting" in args
    full_content = "--full-content" in args

    # --ocr-engine 파싱
    ocr_engine = "mathpix"
    for i, a in enumerate(args):
        if a == "--ocr-engine" and i + 1 < len(args):
            ocr_engine = args[i + 1]
        elif a.startswith("--ocr-engine="):
            ocr_engine = a.split("=", 1)[1]
    if ocr_engine not in ("mathpix", "claude"):
        print(f"알 수 없는 OCR 엔진: {ocr_engine}  (mathpix|claude)")
        sys.exit(1)

    positional = [a for a in args if not a.startswith("--") and a not in ("mathpix", "claude")]

    if not positional:
        print("사용법: py scripts/text/pdf_to_text.py [PDF경로] [--filter-handwriting] [--ocr-engine mathpix|claude] [--full-content]")
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

    convert(pdf, filter_hw=filter_hw, ocr_engine=ocr_engine, full_content=full_content)
