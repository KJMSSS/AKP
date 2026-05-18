"""
Cycle 15h-1 — LLM 확장 (수식 플레이스홀더 + 자모 감지) 광주고 v17.

변경사항 vs v16:
  - 수식을 【수식N】으로 마스킹 후 LLM 전달 → 누등식/존제핟ㄷ톨 교정 목표
  - 자모 분리 [자모:X] 마킹 → LLM 힌트
  - 클로바 CLOVA_DISABLED=1 환경변수로 우회

측정:
  - v16 vs v17 hp:t 교정 건수 비교
  - 1번 누등식, 7번 존제핟ㄷ톨 교정 여부
  - 헤더 4건 교정 여부

회귀:
  - v17 page 2~N scripts = v16 page 2~N scripts
  - 비용 cap $20
"""
import hashlib
import io
import os
import re
import sys
import time
import traceback
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

# 클로바 비활성화
os.environ.setdefault("CLOVA_DISABLED", "1")

from src.text_only.ocr_fallback import apply_fallback
from src.text_only.text_builder import build_from_markdown
from src.text_only.page_extractor import compare_scripts, get_hwpx_scripts
from src.ocr.llm_postprocess import postprocess_markdown
from src.ocr.cost_guard import CostGuard, CostCapError
from src.common.image_extractor import extract_images

ROOT     = Path(__file__).resolve().parent.parent
SRC_DIR  = ROOT / "samples" / "11b"
PROD_DIR = ROOT / "samples" / "11b_production"
LOG_DIR  = ROOT / "log" / "cycle_15h"

SOURCE  = "2025_1_1_b_공수1_광주고"
VER     = "v17"
PREV    = "v16"
V15_SHA = "0c95a49f296b"
V16_SHA = "8bc62331adf5"

PAGE1_SCRIPT_COUNT = 30


def _xml_sha(hwpx_path: Path) -> str:
    with zipfile.ZipFile(hwpx_path) as zf:
        data = zf.read("Contents/section0.xml")
    return hashlib.sha256(data).hexdigest()[:12]


def _get_hp_t_texts(hwpx: Path) -> list[str]:
    with zipfile.ZipFile(hwpx) as zf:
        xml = zf.read("Contents/section0.xml").decode("utf-8")
    return re.findall(r"<hp:t[^>]*>([^<]+)</hp:t>", xml)


guard = CostGuard(cap_usd=20.0)
safe  = re.sub(r"[^\w\-]+", "_", SOURCE.strip("[]")).strip("_")
cache = SRC_DIR / f"_{safe}_raw.md"
if not cache.exists():
    cache = SRC_DIR / f"_{SOURCE}_raw.md"
template = next(SRC_DIR.glob("*.hwpx"), None)
out_hwpx = PROD_DIR / f"{SOURCE}_{VER}.hwpx"
pdf      = SRC_DIR / f"[{SOURCE}].pdf"

print(f"\n{'='*60}")
print(f"[광주고] → {VER}  (Cycle 15h-1: 수식 플레이스홀더 + 자모)")

if out_hwpx.exists():
    print(f"  {VER} 이미 존재 ({_xml_sha(out_hwpx)}) — 재빌드하려면 수동 삭제")
    sys.exit(0)

if not cache.exists():
    print("  캐시 없음 — 중단")
    sys.exit(1)

# ── LLM 후처리 ─────────────────────────────────────────────────────────
print("\n[1/3] LLM 후처리 (수식 마스킹 + 자모 감지)")
md_raw = cache.read_text(encoding="utf-8")
md_llm = md_raw
llm_meta: dict = {}

try:
    guard.check_or_raise("llm")
    md_llm, llm_meta = postprocess_markdown(md_raw, log_stem=f"광주고_{VER}")
    if llm_meta.get("skipped"):
        print(f"  SKIP: {llm_meta.get('reason')}")
    else:
        cost = llm_meta["cost_usd"]
        corrections = llm_meta.get("corrections", 0)
        rejected    = llm_meta.get("rejected", 0)
        print(f"  완료: ${cost:.4f}  교정 {corrections}건 적용 / {rejected}건 거부")
        guard.record("llm", cost)
except CostCapError as e:
    print(f"  [비용 cap] {e}")

# ── 그림 캡처 ──────────────────────────────────────────────────────────
print("\n[2/3] 그림 캡처")
extracted_images = []
if pdf.exists():
    try:
        IMG_DIR = LOG_DIR / "images" / "광주고_v17"
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        extracted_images = extract_images(pdf, IMG_DIR, dpi=150, pages=[0])
        print(f"  page 1 그림: {len(extracted_images)}개")
    except Exception as e:
        print(f"  [오류] {e}")
else:
    print(f"  PDF 없음 — skip")

# ── HWPX 빌드 ──────────────────────────────────────────────────────────
print("\n[3/3] HWPX 빌드")
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

    # ── page 2~N 회귀 ────────────────────────────────────────────────
    prev_hwpx = PROD_DIR / f"{SOURCE}_{PREV}.hwpx"
    if prev_hwpx.exists():
        diffs = compare_scripts(prev_hwpx, out_hwpx, start_idx=PAGE1_SCRIPT_COUNT)
        if diffs:
            print(f"\n  ⚠️  page 2~N 스크립트 변경 {len(diffs)}건:")
            for d in diffs[:5]:
                idx = d["idx"]
                if idx == -1:
                    print(f"    총계: {d['a']} → {d['b']}")
                else:
                    print(f"    #{idx}: {d['a'][:60]} → {d['b'][:60]}")
        else:
            print(f"\n  ✅ page 2~N 스크립트 동일 ({PREV}→{VER})")

    # ── hp:t 교정 측정 (vs v16) ──────────────────────────────────────
    if prev_hwpx.exists():
        t16 = _get_hp_t_texts(prev_hwpx)
        t17 = _get_hp_t_texts(out_hwpx)
        ht_diffs = [(i, a, b) for i, (a, b) in enumerate(zip(t16, t17)) if a != b]
        print(f"\n  v16→v17 hp:t 변경 {len(ht_diffs)}건:")
        for i, a, b in ht_diffs[:15]:
            print(f"    #{i}: {repr(a[:50])} → {repr(b[:50])}")

        # 목표 항목 명시 확인
        targets = {
            "누등식": "부등식",
            "존제핟": "존재하",
            "ㄷ톨": "도록",
        }
        print("\n  목표 교정 달성 여부:")
        v17_all_text = " ".join(t17)
        for wrong, correct in targets.items():
            fixed = wrong not in v17_all_text
            present = correct in v17_all_text
            status = "✅" if fixed and present else ("⚠️ 오자 남음" if wrong in v17_all_text else "?")
            print(f"    {wrong}→{correct}: {status}")

    # baseline
    print(f"\n  v15: {V15_SHA}")
    print(f"  v16: {V16_SHA}")
    print(f"  v17: {xml_sha}")

except Exception:
    print(f"  오류:\n{traceback.format_exc()}")
    sys.exit(1)

# ── 비용 요약 ───────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print("비용 요약")
summary = guard.summary()
for svc, cost in summary.items():
    print(f"  {svc}: ${cost:.4f}")
print(f"  오늘 합계: ${guard.total_today():.4f} / $20.00")
print("=" * 60)
