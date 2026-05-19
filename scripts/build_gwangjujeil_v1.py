"""
Cycle 16 — 광주제일고 v1 baseline 빌드.

특이사항:
  - PDF rotation=180 (광주여고 270과 다름)
  - Mathpix 이미지 12개 (대부분 scratch work 혼합)
  - 문제 순서 OCR에서 뒤섞임 → text_builder 출력으로 학원장 확인
  - 선택형 22문항 + 서술형 4문항 = 100점
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

from src.text_only.ocr_fallback import apply_fallback
from src.text_only.text_builder import build_from_markdown
from src.text_only.page_extractor import compare_scripts
from src.ocr.llm_postprocess import postprocess_markdown
from src.ocr.cost_guard import CostGuard, CostCapError

ROOT     = Path(__file__).resolve().parent.parent
SRC_DIR  = ROOT / "samples" / "11b"
PROD_DIR = ROOT / "samples" / "11b_production"
LOG_DIR  = ROOT / "log" / "cycle_16"

SOURCE  = "2025_1_1_b_공수1_광주제일고"
VER     = "v4"

safe    = re.sub(r"[^\w\-]+", "_", SOURCE.strip("[]")).strip("_")
cache   = SRC_DIR / f"_{safe}_raw.md"
if not cache.exists():
    cache = SRC_DIR / f"_{SOURCE}_raw.md"
template = SRC_DIR / f"[{SOURCE}].hwpx"
gold     = template
out_hwpx = PROD_DIR / f"{SOURCE}_{VER}.hwpx"
pdf      = SRC_DIR / f"[{SOURCE}].pdf"


def _xml_sha(hwpx_path: Path) -> str:
    with zipfile.ZipFile(hwpx_path) as zf:
        data = zf.read("Contents/section0.xml")
    return hashlib.sha256(data).hexdigest()[:12]


guard = CostGuard(cap_usd=5.0)

print(f"\n{'='*60}")
print(f"[광주제일고] → {VER}  (Cycle 16: 선택형22+서술형4, 이미지12개, v1/v3 기존존재→v4신규)")

if out_hwpx.exists():
    print(f"  {VER} 이미 존재 ({_xml_sha(out_hwpx)}) — 재빌드하려면 수동 삭제")
    sys.exit(0)

if not cache.exists():
    print(f"  캐시 없음: {cache}")
    sys.exit(1)

if not template.exists():
    print(f"  template 없음: {template}")
    sys.exit(1)

# ── LLM 후처리 ────────────────────────────────────────────────────────────
print("\n[1/2] LLM 후처리 (temperature=0, 헤더 보호)")
md_raw = cache.read_text(encoding="utf-8")
md_llm = md_raw
llm_meta: dict = {}

try:
    guard.check_or_raise("llm")
    md_llm, llm_meta = postprocess_markdown(md_raw, log_stem=f"광주제일고_{VER}")
    if llm_meta.get("skipped"):
        print(f"  SKIP: {llm_meta.get('reason')}")
    else:
        cost        = llm_meta["cost_usd"]
        corrections = llm_meta.get("corrections", 0)
        rejected    = llm_meta.get("rejected", 0)
        print(f"  완료: ${cost:.4f}  교정 {corrections}건 / 거부 {rejected}건")
        guard.record("llm", cost)
except CostCapError as e:
    print(f"  [비용 cap] {e}")

# ── HWPX 빌드 ────────────────────────────────────────────────────────────
print("\n[2/2] HWPX 빌드")
try:
    buf = io.StringIO()
    t0  = time.time()
    with redirect_stdout(buf):
        md_proc = apply_fallback(md_llm, pdf)
        r       = build_from_markdown(md_proc, out_hwpx, template)
    elapsed = time.time() - t0

    xml_sha = _xml_sha(out_hwpx)
    size_kb = out_hwpx.stat().st_size // 1024
    pipeline_out = buf.getvalue()

    print(f"  완료: p={r['paragraphs']} eq={r['equations']}  {size_kb}KB  {elapsed:.1f}s")
    print(f"  xml_sha: {xml_sha}")
    if pipeline_out.strip():
        print(f"  pipeline:\n{pipeline_out.strip()}")

    # gold 비교
    print(f"\n  ── gold 비교 ({gold.name}) ──")
    diffs = compare_scripts(gold, out_hwpx, start_idx=0)
    print(f"  script 차이: {len(diffs)}건")
    for d in diffs[:8]:
        idx = d["idx"]
        if idx == -1:
            print(f"    총계: gold={d['a']} / v1={d['b']}")
        else:
            print(f"    #{idx}: {d['a'][:60]} → {d['b'][:60]}")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    report_path = LOG_DIR / f"광주제일고_{VER}_baseline_diff.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# 광주제일고 v1 baseline 비교\n\n")
        f.write(f"생성: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"## SHA\n- gold: `{_xml_sha(gold)}`\n- v1: `{xml_sha}`\n\n")
        f.write(f"## script 차이: {len(diffs)}건\n")
        for d in diffs:
            idx = d["idx"]
            if idx == -1:
                f.write(f"- 총계: gold={d['a']} / v1={d['b']}\n")
            else:
                f.write(f"- `#{idx}`: `{d['a'][:80]}` → `{d['b'][:80]}`\n")
        f.write(f"\n## LLM\n- 교정: {llm_meta.get('corrections','N/A')}건\n")
        f.write(f"- 비용: ${llm_meta.get('cost_usd',0):.4f}\n")
    print(f"\n  보고서: {report_path}")

except Exception:
    print(f"  오류:\n{traceback.format_exc()}")
    sys.exit(1)

# ── 비용 요약 ──────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print("비용 요약")
for svc, cost in guard.summary().items():
    print(f"  {svc}: ${cost:.4f}")
print(f"  오늘 합계: ${guard.total_today():.4f} / $5.00")
print("=" * 60)
