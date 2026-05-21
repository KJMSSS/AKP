"""대동고·대성여고·동성고 점수 형식 전처리 후 v5 재빌드."""
import io, re, sys
from pathlib import Path
from contextlib import redirect_stdout

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")

from src.text_only.problem_segmenter import parse_problems, rebuild_markdown
from src.ocr.choice_normalizer import normalize_choices
from src.text_only.ocr_fallback import apply_fallback
from src.text_only.text_builder import build_from_markdown
from src.ocr.llm_postprocess import postprocess_markdown
from src.common.hwpx_table_inserter import replace_condition_tables, replace_boilerplate_tables

ROOT     = Path(__file__).resolve().parent.parent
SRC_DIR  = ROOT / "samples" / "11b"
PROD_DIR = ROOT / "samples" / "11b_production"

# 학교별 설정: (source명, 전처리 함수)
def preprocess_daedong(md):
    # 【3.8점】 겹꺾쇠 → [3.8점]
    md = re.sub(r'【([\d.．]+)점】', r'[\1점]', md)
    return md

def preprocess_daesung(md):
    # (3점) (3.2점) 소괄호 → [3점] [3.2점]
    md = re.sub(r'\(([\d.]+)점\)', r'[\1점]', md)
    return md

def preprocess_dongsung(md):
    # (3점) 소괄호 → [3점]  (기존 [4점] 형식은 유지)
    md = re.sub(r'\(([\d.]+)점\)', r'[\1점]', md)
    return md

TARGETS = [
    ("2025_1_1_b_공수1_대동고",  preprocess_daedong),
    ("2025_1_1_b_공수1_대성여고", preprocess_daesung),
    ("2025_1_1_b_공수1_동성고",  preprocess_dongsung),
]


def build_one(source, preprocess_fn):
    cache    = SRC_DIR / f"_{source}_raw.md"
    template = SRC_DIR / f"[{source}].hwpx"
    out_hwpx = PROD_DIR / f"{source}_v5.hwpx"
    pdf      = SRC_DIR / f"[{source}].pdf"

    print(f"\n{'='*55}")
    print(f"[{source}]")
    print('='*55)

    md_raw = cache.read_text(encoding="utf-8")
    md_raw = preprocess_fn(md_raw)

    print("=== 파싱 ===")
    header, segments = parse_problems(md_raw)
    obj_cnt  = sum(1 for s in segments if not s.is_subjective)
    subj_cnt = sum(1 for s in segments if s.is_subjective)
    print(f"객관식 {obj_cnt}개, 서술형 {subj_cnt}개")
    for s in segments:
        tag = "서술형" if s.is_subjective else "객관식"
        print(f"  {tag} {s.number}번: 선택지={len(s.choices)}개")

    print("\n=== 선택지 정규화 ===")
    stem = source.replace("2025_1_1_b_공수1_", "")
    segments = normalize_choices(segments, log_stem=f"{stem}_v5fix")

    print("\n=== rebuild ===")
    md_rebuilt = rebuild_markdown(header, segments)

    print("\n=== LLM 후처리 ===")
    md_llm, llm_meta = postprocess_markdown(md_rebuilt, log_stem=f"{stem}_v5fix")
    if llm_meta.get("skipped"):
        print(f"  스킵: {llm_meta.get('reason')}")
    else:
        print(f"  완료 (${llm_meta.get('cost_usd', 0):.4f})")

    print("\n=== fallback + HWPX 빌드 ===")
    buf = io.StringIO()
    with redirect_stdout(buf):
        md_proc = apply_fallback(md_llm, pdf)
        result  = build_from_markdown(md_proc, out_hwpx, template)
    print(buf.getvalue()[-300:] if buf.getvalue() else "  (출력 없음)")

    print("\n=== 표 삽입 ===")
    n_cond = replace_condition_tables(out_hwpx)
    n_bogi = replace_boilerplate_tables(out_hwpx)
    print(f"  조건표: {n_cond}개, 보기표: {n_bogi}개")

    kb = out_hwpx.stat().st_size // 1024
    print(f"\n완료: {out_hwpx.name}  ({kb}KB)")


for source, fn in TARGETS:
    try:
        build_one(source, fn)
    except Exception as e:
        print(f"\n[ERROR] {source}: {e}")
        import traceback; traceback.print_exc()

print("\n\n=== 전체 완료 ===")
