"""
Cycle 15g Step 1a — \right 패치 + 광주고 v15 빌드.

변경사항: latex_to_hwp.py \right 토큰 앞 공백 추가 + \right. null delimiter 복구.
회귀 확인: v14 vs v15 스크립트 diff (xRIGHT 패턴만 변경 기대).
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
from src.text_only.page_extractor import compare_scripts, get_hwpx_scripts

ROOT     = Path(__file__).resolve().parent.parent
SRC_DIR  = ROOT / "samples" / "11b"
PROD_DIR = ROOT / "samples" / "11b_production"

SOURCE = "2025_1_1_b_공수1_광주고"
VER    = "v15"
PREV   = "v14"

# v14 baseline (대소문자 패치 이후)
V14_SHA = "1111703b2ef1"


def _xml_sha(hwpx_path: Path) -> str:
    with zipfile.ZipFile(hwpx_path) as zf:
        data = zf.read("Contents/section0.xml")
    return hashlib.sha256(data).hexdigest()[:12]


safe   = re.sub(r"[^\w\-]+", "_", SOURCE.strip("[]")).strip("_")
cache  = SRC_DIR / f"_{safe}_raw.md"
if not cache.exists():
    cache = SRC_DIR / f"_{SOURCE}_raw.md"

template = next(SRC_DIR.glob("*.hwpx"), None)
out_hwpx = PROD_DIR / f"{SOURCE}_{VER}.hwpx"
pdf      = SRC_DIR / f"[{SOURCE}].pdf"

print(f"\n{'='*60}")
print(f"[광주고] → {VER}  (\\right 패치)")

if out_hwpx.exists():
    print(f"  {VER} 이미 존재 ({_xml_sha(out_hwpx)}) — 삭제 후 재빌드하려면 파일 수동 제거")
    sys.exit(0)

if not cache.exists():
    print("  캐시 없음 — 중단")
    sys.exit(1)

try:
    md_raw = cache.read_text(encoding="utf-8")
    buf    = io.StringIO()
    t0     = time.time()
    with redirect_stdout(buf):
        md_proc = apply_fallback(md_raw, pdf)
        r       = build_from_markdown(md_proc, out_hwpx, template)
    elapsed = time.time() - t0

    pipeline_out = buf.getvalue()
    xml_sha  = _xml_sha(out_hwpx)
    size_kb  = out_hwpx.stat().st_size // 1024

    print(f"  완료: p={r['paragraphs']} eq={r['equations']}  {size_kb}KB  {elapsed:.1f}s")
    print(f"  xml_sha: {xml_sha}")
    if pipeline_out.strip():
        print(f"  pipeline:\n{pipeline_out.strip()}")

    # ── v14 vs v15 스크립트 전체 diff ────────────────────────────────────
    prev_hwpx = PROD_DIR / f"{SOURCE}_{PREV}.hwpx"
    if prev_hwpx.exists():
        diffs = compare_scripts(prev_hwpx, out_hwpx, start_idx=0)
        print(f"\n  v14→v15 스크립트 변경: {len(diffs)}건")
        for d in diffs:
            if d["idx"] == -1:
                print(f"    총계: {d['a']} → {d['b']}")
            else:
                print(f"    #{d['idx']}:")
                print(f"      v14: {d['a'][:100]}")
                print(f"      v15: {d['b'][:100]}")

        # xRIGHT / }RIGHT 패턴 외 변경 탐지
        unexpected = []
        for d in diffs:
            if d["idx"] == -1:
                continue
            v14_s = d["a"]
            v15_s = d["b"]
            # 허용 변경 1: (\w|}) RIGHT 공백 추가
            v14_fixed = re.sub(r'([\w}])RIGHT', r'\1 RIGHT', v14_s)
            # 허용 변경 2: LEFT { atop 끝에 RIGHT . 추가
            v14_fixed = re.sub(r'(LEFT \{.+atop[^\n]+?)$', r'\1 RIGHT .', v14_fixed, flags=re.DOTALL)
            if v14_fixed.strip() != v15_s.strip():
                unexpected.append(d)

        if unexpected:
            print(f"\n  ⚠️  예상 외 변경 {len(unexpected)}건:")
            for d in unexpected:
                print(f"    #{d['idx']}: {d['a'][:80]} → {d['b'][:80]}")
        else:
            print("\n  ✅ 모든 변경이 \\right 패치 범위 내")

except Exception:
    print(f"  오류:\n{traceback.format_exc()}")
    sys.exit(1)

print("=" * 60)
print(f"v14 sha: {V14_SHA}")
print(f"v15 sha: {xml_sha}")
print(f"새 baseline: {xml_sha}")
