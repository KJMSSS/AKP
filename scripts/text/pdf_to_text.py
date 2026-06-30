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
    --format 수학비서      B4 2단 명조 학원(수학비서) 양식으로 2단 변환
    --format 타이퍼        A3 2단 메타표 타이퍼 양식으로 2단 변환 (미지정 시 1단까지만)

동작:
    파일명에 풀이본 마커(쫑/쭌/DJ/훈)가 있으면 OCR을 자동 스킵한다(정형화본+손풀이라 무의미).
    그래도 읽어야 하면 --clean-handwriting로 손풀이를 지우고 OCR한다.

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
from src.common.pdf_utils import normalize_pdf_rotation, filter_handwriting_pdf
from src.text_only.text_builder import build_from_markdown
from src.text_only.handwriting_filter import filter_handwriting
from src.text_only.ocr_fallback import apply_fallback, reinforce_placeholders
from src.text_only.problem_segmenter import parse_problems, rebuild_markdown
from src.common.image_extractor import extract_images, extract_figures_with_bbox_detection
from src.common.hwpx_image_inserter import insert_figure_placeholder
from src.common.hwpx_table_inserter import replace_condition_tables, replace_boilerplate_tables, restyle_data_tables_to_gold
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


def _split_by_problem(md: str) -> tuple[str, dict[int, str]]:
    """마크다운을 헤더 + {문제번호: 블록}으로 분할 (문제번호 줄 기준)."""
    head: list[str] = []
    blocks: dict[int, str] = {}
    cur_num: int | None = None
    cur: list[str] = []
    for line in md.split("\n"):
        m = re.match(r"^\s*(\d{1,2})[.．]\s", line)
        if m:
            if cur_num is None:
                head = cur
            else:
                blocks[cur_num] = "\n".join(cur)
            cur_num, cur = int(m.group(1)), [line]
        else:
            cur.append(line)
    if cur_num is not None:
        blocks[cur_num] = "\n".join(cur)
    else:
        head = cur
    return "\n".join(head), blocks


def _hybrid_merge_ocr(md_claude: str, md_mathpix: str) -> str:
    """Claude(구조·한글·그림) + Mathpix(수식) 문제 단위 머지 → raw.md.

    문제번호로 양쪽을 분할해 merge_all(수식=Mathpix 위치교체, 개수 큰차이 시 Haiku 보정)로
    합친다. 구조·선택지·그림 마커는 Claude 기준 유지.
    """
    from src.ocr.ocr_merger import merge_all
    head_c, vis = _split_by_problem(md_claude)
    _, mpx = _split_by_problem(md_mathpix)
    client = None
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=key)
        except ImportError:
            pass
    merged = merge_all(vis, mpx, client=client)
    body = "\n\n".join(merged[n] for n in sorted(merged))
    print(f"  하이브리드 머지: 문제 {len(merged)}개 (구조=Claude, 수식=Mathpix)")
    return (head_c.rstrip() + "\n\n" + body) if head_c.strip() else body


_HW_MARKERS = ("쫑", "쭌", "DJ", "훈")   # 파일명 손글씨(풀이) 마커: 쫑=학원장·DJ/훈/쭌=선생님


def _hw_marker_in(name: str):
    """파일명에 풀이본 마커가 있으면 그 마커를, 없으면 None.

    마커 PDF = 이미 정형화(타이핑)된 시험지를 PDF로 출력한 뒤 손풀이만 얹은 것.
    깨끗한 원본이 따로 있어 OCR 의미 없음 → OCR 입력에서 스킵 판정용.
    """
    for mk in _HW_MARKERS:
        if mk in name:
            return mk
    return None


def convert(pdf_path: Path, filter_hw: bool = False, ocr_engine: str = "mathpix", full_content: bool = False, force_ocr: bool = False, clean_handwriting: bool = False, output_format: str = "") -> Path | None:
    # 0단계: 풀이본(쫑/쭌/DJ/훈) 스킵 — 정형화본+손풀이라 OCR 무의미(과금·출력 없음).
    #         단, --clean-handwriting 명시 시엔 의도적 처리로 보고 스킵하지 않는다(손풀이 지우고 읽기).
    _mk = _hw_marker_in(pdf_path.name)
    if _mk and not clean_handwriting:
        print("─" * 62)
        print(f"[ 스킵 ] 풀이본 PDF (마커 '{_mk}') — OCR 제외 · 과금/출력 없음")
        print(f"  PDF: {pdf_path.name}")
        print("─" * 62)
        return None
    # 회전 정상화 (회전된 페이지가 있으면 보정 PDF로 교체)
    original_pdf = pdf_path                          # 캐시 키는 원본 기준 (rotfix 바이트 변동 무시)
    pdf_path = normalize_pdf_rotation(pdf_path)
    cache_key = original_pdf                         # Mathpix 캐시 키 (회전 무시)
    # 손풀이(학생 손글씨) 제거 — OCR 입력 정화 (opt-in). 회전보정 직후·OCR 전.
    if clean_handwriting:
        pdf_path = filter_handwriting_pdf(pdf_path)
        cache_key = pdf_path                         # 정화본은 내용이 달라 별도 캐시

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
    elif ocr_engine == "hybrid":
        # Claude(구조·한글·그림) + Mathpix(수식) 문제 단위 머지
        md_claude = read_pdf_as_markdown(pdf_path, full_content=full_content)
        client = MathpixClient()
        pdf_id = client.submit_pdf(pdf_path, force=force_ocr, cache_key_path=cache_key)
        if client.last_pdf_cached:
            print(f"  Mathpix 캐시 재사용 (pdf_id={pdf_id})")
        client.poll_pdf(pdf_id, progress=True)
        md = _hybrid_merge_ocr(md_claude, client.fetch_pdf_markdown(pdf_id))
    else:
        client = MathpixClient()
        pdf_id = client.submit_pdf(pdf_path, force=force_ocr, cache_key_path=cache_key)
        if client.last_pdf_cached:
            print(f"  캐시된 pdf_id 재사용 → 재과금 없음 (pdf_id={pdf_id})")
        else:
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

    # Vision 폴백: BBoxDetector로 문제별 크롭 후 개별 Vision 판정 (정밀 추출)
    # ★ Claude 마킹 문제만 처리 — false-positive 없음
    unresolved = figure_items_from_claude - set(figure_map)
    if unresolved:
        print(f"  그림 Vision 폴백 ({len(unresolved)}건): {sorted(unresolved)}")
        try:
            vision_map = extract_figures_with_bbox_detection(
                pdf_path, unresolved, fig_dir,
                api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            )
            figure_map.update(vision_map)
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

    # 양식별 header 소스: 수학비서=서울세종고(명조), 그 외=워드초벌/template glob
    if output_format == "수학비서":
        _suh = SAMPLES_DIR / "suhbiseo_template.hwpx"
        base = _suh if _suh.exists() else _pick_template()
    else:
        base = _pick_template()
    print(f"  헤더 참조: {base.name}")

    t1 = time.time()
    result = build_from_markdown(md, out_hwpx, base)
    out_hwpx = result['output']  # 잠금으로 인해 대체 경로에 저장된 경우 반영
    restyle_data_tables_to_gold(out_hwpx)   # 데이터표 골드 양식(헤더 이중선) — 박스 치환 전
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

    # 양식 변환 (2단): 수학비서(B4 명조·메타표없음) / 타이퍼(A3 메타표)
    if output_format:
        from src.text_only.typer_builder import build_by_format
        print()
        print("─" * 62)
        print(f"[ 3단계 ] {output_format} 양식 2단 변환")
        print("─" * 62)
        two_path = SAMPLES_DIR / f"output_text_{stem}_{output_format}.hwpx"
        out_hwpx = build_by_format(out_hwpx, stem, two_path, output_format)
        fix_hwpx_namespaces(str(out_hwpx))

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
    force_ocr    = "--force-ocr" in args
    clean_hw     = "--clean-handwriting" in args

    # --ocr-engine 파싱
    ocr_engine = "mathpix"
    for i, a in enumerate(args):
        if a == "--ocr-engine" and i + 1 < len(args):
            ocr_engine = args[i + 1]
        elif a.startswith("--ocr-engine="):
            ocr_engine = a.split("=", 1)[1]
    if ocr_engine not in ("mathpix", "claude", "hybrid"):
        print(f"알 수 없는 OCR 엔진: {ocr_engine}  (mathpix|claude)")
        sys.exit(1)

    # --format 파싱 (출력 양식: 수학비서 | 타이퍼). 미지정이면 1단까지만.
    output_format = ""
    for i, a in enumerate(args):
        if a == "--format" and i + 1 < len(args):
            output_format = args[i + 1]
        elif a.startswith("--format="):
            output_format = a.split("=", 1)[1]
    if output_format and output_format not in ("타이퍼", "수학비서"):
        print(f"알 수 없는 양식: {output_format}  (타이퍼|수학비서)")
        sys.exit(1)

    # --ocr-engine/--format 값이 위치인자로 새지 않게 제외
    _flag_vals = {"mathpix", "claude", "hybrid", "타이퍼", "수학비서"}
    positional = [a for a in args if not a.startswith("--") and a not in _flag_vals]

    if not positional:
        print("사용법: py scripts/text/pdf_to_text.py [PDF경로] [--ocr-engine mathpix|claude|hybrid] [--format 수학비서|타이퍼] [--full-content] [--force-ocr] [--clean-handwriting]")
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

    convert(pdf, filter_hw=filter_hw, ocr_engine=ocr_engine, full_content=full_content, force_ocr=force_ocr, clean_handwriting=clean_hw, output_format=output_format)
