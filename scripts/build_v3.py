"""
광주고 v11 + 14개교 v3 빌드 스크립트.
approved=29 사전 적용, 학교별 교정 통계 수집.
"""
import hashlib
import io
import json
import sys
import time
import traceback
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from src.learn.apply_corrections import apply_corrections
from src.text_only.ocr_fallback import apply_fallback
from src.text_only.text_builder import build_from_markdown

ROOT      = Path(__file__).resolve().parent.parent
SRC_DIR   = ROOT / "samples" / "11b"
PROD_DIR  = ROOT / "samples" / "11b_production"
DICT_PATH = ROOT / "src" / "learn" / "corrections.json"

# 학교 → 출력 버전
VERSION_MAP = {
    "2025_1_1_b_공수1_광주고":     "v11",
    "2025_1_1_b_공수1_광주여고":   "v3",
    "2025_1_1_b_공수1_광주제일고": "v3",
    "2025_1_1_b_공수1_대동고":     "v3",
    "2025_1_1_b_공수1_경신여고":   "v3",
    "2025_1_1_b_공수1_대성여고":   "v3",
    "2025_1_1_b_공수1_동신여고":   "v3",
    "2025_1_1_b_공수1_국제고":     "v3",
    "2025_1_1_b_공수1_고려고":     "v3",
    "2025_1_1_b_공수1_광덕고":     "v3",
    "2025_1_1_b_공수1_대광여고":   "v3",
    "2025_1_1_b_공수1_금호고":     "v3",
    "2025_1_1_b_공수1_금호중앙여고": "v3",
    "2025_1_1_b_공수1_동성고":     "v3",
}
# 광주고 회귀 비교 대상 버전
PREV_MAP = {
    "2025_1_1_b_공수1_광주고":     "v10",
    "2025_1_1_b_공수1_경신여고":   "v2",
}

template = next(SRC_DIR.glob("*.hwpx"), None)

data_entries = json.loads(DICT_PATH.read_text(encoding="utf-8"))["entries"]
approved_text = [e for e in data_entries if e.get("approved") and e["type"] == "text"]

results = []

for source, ver in VERSION_MAP.items():
    pdf  = SRC_DIR / f"[{source}].pdf"
    stem = source.replace("2025_1_1_b_공수1_", "")

    # 캐시 MD: batch_production 규칙과 동일 (re.sub 안전화)
    import re
    safe = re.sub(r"[^\w\-]+", "_", source.strip("[]")).strip("_")
    cache = SRC_DIR / f"_{safe}_raw.md"
    if not cache.exists():
        # 파일명 패턴 두 번째 시도 (stem 직접 매칭)
        cache = SRC_DIR / f"_{source}_raw.md"
    if not cache.exists():
        print(f"[{stem}] 캐시 없음 — skip")
        results.append({"school": stem, "ver": ver, "status": "skip", "reason": "캐시 없음"})
        continue

    out_hwpx = PROD_DIR / f"{source}_{ver}.hwpx"
    if out_hwpx.exists():
        print(f"[{stem}] {ver} 이미 존재 — skip")
        results.append({"school": stem, "ver": ver, "status": "exist"})
        continue

    print(f"\n{'='*60}")
    print(f"[{stem}] → {ver}")

    try:
        md_raw = cache.read_text(encoding="utf-8")

        # ── 교정 사전 효과 사전 측정 ─────────────────────────
        _, corr_log = apply_corrections(md_raw, DICT_PATH, domain="markdown")
        applied_entries = [e for e in corr_log if e.get("count", 0) > 0]

        # ── 실제 파이프라인 실행 (stdout 캡처) ───────────────
        buf = io.StringIO()
        t0  = time.time()
        with redirect_stdout(buf):
            md_processed = apply_fallback(md_raw, pdf)
            r = build_from_markdown(md_processed, out_hwpx, template)
        elapsed = time.time() - t0

        pipeline_out = buf.getvalue()

        sha = hashlib.sha256(out_hwpx.read_bytes()).hexdigest()
        size_kb = out_hwpx.stat().st_size // 1024

        print(f"  완료: p={r['paragraphs']} eq={r['equations']}  {size_kb}KB  {elapsed:.1f}s")
        print(f"  sha256: {sha}")
        print(f"  교정 적용: {len(applied_entries)}건")
        for e in applied_entries:
            print(f"    [{e['old']}]→[{e['new']}] {e['count']}회 ({e['method']})")

        # ── 이전 버전 비교 ───────────────────────────────────
        prev_ver = PREV_MAP.get(source)
        prev_sha = None
        if prev_ver:
            prev_hwpx = PROD_DIR / f"{source}_{prev_ver}.hwpx"
            if prev_hwpx.exists():
                prev_sha = hashlib.sha256(prev_hwpx.read_bytes()).hexdigest()
                match = "동일 ✅" if sha == prev_sha else "다름 ⚠️"
                print(f"  {prev_ver} vs {ver}: {match}")
                if sha != prev_sha:
                    print(f"    {prev_ver} sha: {prev_sha}")
                    print(f"    {ver}  sha: {sha}")

        results.append({
            "school":   stem,
            "ver":      ver,
            "status":   "ok",
            "paragraphs": r["paragraphs"],
            "equations":  r["equations"],
            "size_kb":  size_kb,
            "elapsed":  round(elapsed, 1),
            "applied":  len(applied_entries),
            "entries":  [(e["old"], e["new"], e["count"]) for e in applied_entries],
            "sha":      sha,
            "prev_sha": prev_sha,
        })

    except Exception:
        err = traceback.format_exc()
        print(f"  오류:\n{err}")
        results.append({"school": stem, "ver": ver, "status": "error", "reason": err[:200]})

# ── 최종 요약 ────────────────────────────────────────────────────
print()
print("=" * 60)
print("최종 요약")
print(f"  {'학교':<16} {'버전':<4} {'교정':<4} {'적용 패턴'}")
print(f"  {'-'*16} {'-'*4} {'-'*4} {'-'*30}")
for r in results:
    if r["status"] == "ok":
        patterns = ", ".join(f"[{o}→{n}]×{c}" for o, n, c in r.get("entries", []))
        regression = ""
        if r.get("prev_sha") and r["prev_sha"] != r["sha"]:
            regression = " ← 변경!"
        print(f"  {r['school']:<16} {r['ver']:<4} {r['applied']:<4} {patterns}{regression}")
    else:
        print(f"  {r['school']:<16} {r['ver']:<4} {r['status']}")
print("=" * 60)
