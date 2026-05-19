"""
2025 공수1 전체 학교 v5 일괄 빌드.

raw.md + template HWPX 있는 학교 중 v5 미완료 학교를 순서대로 빌드.
"""
import hashlib
import io
import re
import sys
import time
import traceback
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from src.text_only.problem_segmenter import parse_problems, rebuild_markdown
from src.ocr.choice_normalizer import normalize_choices
from src.text_only.ocr_fallback import apply_fallback
from src.text_only.text_builder import build_from_markdown
from src.ocr.llm_postprocess import postprocess_markdown
from src.ocr.cost_guard import CostGuard, CostCapError
from src.common.hwpx_table_inserter import replace_condition_tables, replace_boilerplate_tables

ROOT     = Path(__file__).resolve().parent.parent
SRC_DIR  = ROOT / "samples" / "11b"
PROD_DIR = ROOT / "samples" / "11b_production"
LOG_DIR  = ROOT / "log" / "cycle_16"
VER      = "v5"

# v5 아직 없는 학교만 빌드
SCHOOLS = [
    "2025_1_1_b_공수1_고려고",
    "2025_1_1_b_공수1_광주고",
    "2025_1_1_b_공수1_국제고",
    "2025_1_1_b_공수1_금호중앙여고",
    "2025_1_1_b_공수1_대광여고",
    "2025_1_1_b_공수1_대동고",
    "2025_1_1_b_공수1_동명고",
    "2025_1_1_b_공수1_동성고",
    "2025_1_1_b_공수1_동아여고",
    "2025_1_1_b_공수1_명진고",
    "2025_1_1_b_공수1_문성고",
]


def _xml_sha(p: Path) -> str:
    with zipfile.ZipFile(p) as zf:
        return hashlib.sha256(zf.read("Contents/section0.xml")).hexdigest()[:12]


def build_one(source: str, guard: CostGuard) -> dict:
    safe     = re.sub(r"[^\w\-]+", "_", source).strip("_")
    cache    = SRC_DIR / f"_{safe}_raw.md"
    if not cache.exists():
        cache = SRC_DIR / f"_{source}_raw.md"
    template = SRC_DIR / f"[{source}].hwpx"
    out_hwpx = PROD_DIR / f"{source}_{VER}.hwpx"

    result = {"source": source, "ok": False, "note": ""}

    if out_hwpx.exists():
        result["note"] = f"이미 존재 ({_xml_sha(out_hwpx)})"
        result["ok"] = True
        return result

    if not cache.exists():
        result["note"] = f"raw.md 없음"
        return result
    if not template.exists():
        result["note"] = f"template 없음"
        return result

    md_raw = cache.read_text(encoding="utf-8")

    # [1] 문제 파싱
    header, segments = parse_problems(md_raw)
    obj_cnt  = sum(1 for s in segments if not s.is_subjective)
    subj_cnt = sum(1 for s in segments if s.is_subjective)

    # [2] 선택지 정규화
    school_short = source.split("_")[-1]
    try:
        guard.check_or_raise("choices")
        segments = normalize_choices(segments, log_stem=f"{school_short}_{VER}")
        guard.record("choices", 0.01)
    except CostCapError as e:
        result["note"] = f"비용 cap (choices): {e}"
        return result

    # [3] rebuild
    md_rebuilt = rebuild_markdown(header, segments)

    # [4] LLM 후처리
    llm_meta: dict = {}
    try:
        guard.check_or_raise("llm")
        md_llm, llm_meta = postprocess_markdown(md_rebuilt, log_stem=f"{school_short}_{VER}")
        if not llm_meta.get("skipped"):
            guard.record("llm", llm_meta.get("cost_usd", 0))
    except CostCapError as e:
        md_llm = md_rebuilt
        llm_meta = {"skipped": True, "reason": str(e)}

    # [5-6] fallback + HWPX 빌드
    try:
        pdf = SRC_DIR / f"[{source}].pdf"
        buf = io.StringIO()
        with redirect_stdout(buf):
            md_proc = apply_fallback(md_llm, pdf)
            r = build_from_markdown(md_proc, out_hwpx, template)
    except Exception as e:
        result["note"] = f"빌드 오류: {traceback.format_exc()[-200:]}"
        return result

    # [7] 표 삽입
    try:
        n_cond = replace_condition_tables(out_hwpx)
        n_bogi = replace_boilerplate_tables(out_hwpx)
    except Exception:
        pass

    sha = _xml_sha(out_hwpx)
    kb  = out_hwpx.stat().st_size // 1024
    choices_ok = sum(1 for s in segments if not s.is_subjective and len(s.choices) == 5)
    result["ok"]   = True
    result["note"] = (f"객관식 {obj_cnt} 서술형 {subj_cnt} | "
                      f"선택지 {choices_ok}/{obj_cnt} | "
                      f"p={r['paragraphs']} eq={r['equations']} | "
                      f"{kb}KB | sha={sha} | "
                      f"llm={llm_meta.get('corrections','skip')}건")
    return result


guard = CostGuard(cap_usd=5.0)
results = []

print(f"\n{'='*65}")
print(f"2025 공수1 일괄 빌드 ({len(SCHOOLS)}개 학교)")
print(f"{'='*65}")

for source in SCHOOLS:
    school = source.split("_")[-1]
    print(f"\n[{school}] 빌드 중...")
    t0 = time.time()
    res = build_one(source, guard)
    elapsed = time.time() - t0
    status = "✅" if res["ok"] else "❌"
    print(f"  {status} {res['note']}  ({elapsed:.1f}s)")
    results.append(res)

print(f"\n{'='*65}")
print("결과 요약")
for r in results:
    school = r["source"].split("_")[-1]
    status = "✅" if r["ok"] else "❌"
    print(f"  {status} {school:12s}  {r['note'][:80]}")

print(f"\n비용")
for svc, cost in guard.summary().items():
    print(f"  {svc}: ${cost:.4f}")
print(f"  오늘 합계: ${guard.total_today():.4f} / $5.00")
print("=" * 65)
