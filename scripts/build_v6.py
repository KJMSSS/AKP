"""
Cycle 15f (patch) — layout_filter 대소문자 수정 재빌드.
광주고 v14 + 대성여고/금호고 v6.
"""
import hashlib
import io
import json
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

ROOT     = Path(__file__).resolve().parent.parent
SRC_DIR  = ROOT / "samples" / "11b"
PROD_DIR = ROOT / "samples" / "11b_production"

VERSION_MAP = {
    "2025_1_1_b_공수1_광주고":   "v14",
    "2025_1_1_b_공수1_대성여고": "v6",
    "2025_1_1_b_공수1_금호고":   "v6",
}

PREV_MAP = {
    "2025_1_1_b_공수1_광주고":   "v13",
    "2025_1_1_b_공수1_대성여고": "v5",
    "2025_1_1_b_공수1_금호고":   "v5",
}

# 이전 baseline (v10~v13 동일)
PREV_BASELINE = "2217502b5e83"

template = next(SRC_DIR.glob("*.hwpx"), None)


def _xml_sha(hwpx_path: Path) -> str:
    with zipfile.ZipFile(hwpx_path) as zf:
        data = zf.read("Contents/section0.xml")
    return hashlib.sha256(data).hexdigest()[:12]


results = []

for source, ver in VERSION_MAP.items():
    stem = source.replace("2025_1_1_b_공수1_", "")
    safe = re.sub(r"[^\w\-]+", "_", source.strip("[]")).strip("_")
    cache = SRC_DIR / f"_{safe}_raw.md"
    if not cache.exists():
        cache = SRC_DIR / f"_{source}_raw.md"
    if not cache.exists():
        print(f"[{stem}] 캐시 없음 — skip")
        results.append({"school": stem, "ver": ver, "status": "skip"})
        continue

    out_hwpx = PROD_DIR / f"{source}_{ver}.hwpx"
    if out_hwpx.exists():
        print(f"[{stem}] {ver} 이미 존재 — skip")
        results.append({"school": stem, "ver": ver, "status": "exist",
                        "xml_sha": _xml_sha(out_hwpx)})
        continue

    pdf = SRC_DIR / f"[{source}].pdf"
    print(f"\n{'='*60}")
    print(f"[{stem}] → {ver}")

    try:
        md_raw = cache.read_text(encoding="utf-8")
        buf = io.StringIO()
        t0  = time.time()
        with redirect_stdout(buf):
            md_processed = apply_fallback(md_raw, pdf)
            r = build_from_markdown(md_processed, out_hwpx, template)
        elapsed = time.time() - t0
        pipeline_out = buf.getvalue()

        xml_sha = _xml_sha(out_hwpx)
        size_kb = out_hwpx.stat().st_size // 1024
        print(f"  완료: p={r['paragraphs']} eq={r['equations']}  {size_kb}KB  {elapsed:.1f}s")
        print(f"  xml_sha: {xml_sha}")
        print(f"  pipeline:\n{pipeline_out.strip()}")

        prev_ver = PREV_MAP.get(source)
        prev_xml_sha = None
        if prev_ver:
            prev_hwpx = PROD_DIR / f"{source}_{prev_ver}.hwpx"
            if prev_hwpx.exists():
                prev_xml_sha = _xml_sha(prev_hwpx)
                match = "동일 ✅" if xml_sha == prev_xml_sha else "다름 ⚠️"
                print(f"  {prev_ver} vs {ver}: {match}")

        if source == "2025_1_1_b_공수1_광주고":
            same_prev = xml_sha == PREV_BASELINE
            print(f"  이전 baseline({PREV_BASELINE}) {'= 동일' if same_prev else '≠ 변경'} → 새 baseline: {xml_sha}")

        results.append({
            "school": stem, "ver": ver, "status": "ok",
            "paragraphs": r["paragraphs"], "equations": r["equations"],
            "size_kb": size_kb, "elapsed": round(elapsed, 1),
            "xml_sha": xml_sha, "prev_xml_sha": prev_xml_sha,
        })

    except Exception:
        err = traceback.format_exc()
        print(f"  오류:\n{err}")
        results.append({"school": stem, "ver": ver, "status": "error", "reason": err[:300]})

print()
print("=" * 60)
print("최종 요약 — Cycle 15f patch (대소문자 수정)")
print(f"  {'학교':<12} {'버전':<4} {'xml_sha':<14} {'이전비교'}")
for r in results:
    if r["status"] in ("ok", "exist"):
        sha = r.get("xml_sha", "-")
        prev = r.get("prev_xml_sha") or "-"
        match = "=" if prev != "-" and sha == prev else ("≠" if prev != "-" else "-")
        print(f"  {r['school']:<12} {r['ver']:<4} {sha:<14} {match}{prev[:10]}")
    else:
        print(f"  {r['school']:<12} {r['ver']:<4} {r['status']}")
print("=" * 60)
