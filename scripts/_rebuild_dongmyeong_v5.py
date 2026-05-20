"""동명고 v5 재빌드 (1-4번 OCR 크롭 반영)."""
import io, sys
from pathlib import Path
from contextlib import redirect_stdout

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from src.text_only.problem_segmenter import parse_problems, rebuild_markdown
from src.ocr.choice_normalizer import normalize_choices
from src.text_only.ocr_fallback import apply_fallback
from src.text_only.text_builder import build_from_markdown
from src.ocr.llm_postprocess import postprocess_markdown
from src.common.hwpx_table_inserter import replace_condition_tables, replace_boilerplate_tables

ROOT     = Path(__file__).resolve().parent.parent
SRC_DIR  = ROOT / "samples" / "11b"
PROD_DIR = ROOT / "samples" / "11b_production"

SOURCE   = "2025_1_1_b_공수1_동명고"
cache    = SRC_DIR / f"_{SOURCE}_raw.md"
template = SRC_DIR / f"[{SOURCE}].hwpx"
out_hwpx = PROD_DIR / f"{SOURCE}_v5.hwpx"
pdf      = SRC_DIR / f"[{SOURCE}].pdf"

import re
md_raw = cache.read_text(encoding="utf-8")

# 점수 형식 정규화: (N점) → [N점]
md_raw = re.sub(r'\((\d+(?:\.\d+)?)점\)', r'[\1점]', md_raw)

print("=== 파싱 ===")
header, segments = parse_problems(md_raw)
obj_cnt  = sum(1 for s in segments if not s.is_subjective)
subj_cnt = sum(1 for s in segments if s.is_subjective)
print(f"객관식 {obj_cnt}개, 서술형 {subj_cnt}개")
for s in segments:
    tag = "서술형" if s.is_subjective else "객관식"
    nc  = len(s.choices)
    print(f"  {tag} {s.number}번: 선택지={nc}개")

print("\n=== 선택지 정규화 ===")
segments = normalize_choices(segments, log_stem="동명고_v5")

print("\n=== rebuild ===")
md_rebuilt = rebuild_markdown(header, segments)

print("\n=== LLM 후처리 ===")
md_llm, llm_meta = postprocess_markdown(md_rebuilt, log_stem="동명고_v5")
if llm_meta.get("skipped"):
    print(f"  스킵: {llm_meta.get('reason')}")
else:
    print(f"  완료 (${llm_meta.get('cost_usd', 0):.4f})")

print("\n=== fallback + HWPX 빌드 ===")
buf = io.StringIO()
with redirect_stdout(buf):
    md_proc = apply_fallback(md_llm, pdf)
    result  = build_from_markdown(md_proc, out_hwpx, template)
print(buf.getvalue()[-400:] if buf.getvalue() else "  (출력 없음)")

print("\n=== 표 삽입 ===")
n_cond = replace_condition_tables(out_hwpx)
n_bogi = replace_boilerplate_tables(out_hwpx)
print(f"  조건표: {n_cond}개, 보기표: {n_bogi}개")

kb = out_hwpx.stat().st_size // 1024
print(f"\n완료: {out_hwpx.name}  ({kb}KB)")
