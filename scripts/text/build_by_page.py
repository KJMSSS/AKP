"""
build_by_page — 페이지 단위 빌드 + 회귀 검증 도구.

사용 예:
  python scripts/text/build_by_page.py --school 광주고 --ver v15 --prev v14 --page1-scripts 47

기능:
  1. 지정 학교의 캐시 MD를 읽어 page 1만 추출
  2. apply_fallback + build_from_markdown 실행
  3. page 2~N 회귀: new.scripts[page1_scripts:] == old.scripts[page1_scripts:]
  4. 결과 요약 출력
"""
import argparse
import hashlib
import io
import re
import sys
import time
import traceback
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from src.text_only.ocr_fallback import apply_fallback
from src.text_only.text_builder import build_from_markdown
from src.text_only.page_extractor import (
    get_md_page,
    get_md_page_range,
    diff_hwpx_pages,
    get_hwpx_scripts,
)

ROOT     = Path(__file__).resolve().parent.parent.parent
SRC_DIR  = ROOT / "samples" / "11b"
PROD_DIR = ROOT / "samples" / "11b_production"
PREFIX   = "2025_1_1_b_공수1_"


def _xml_sha(hwpx_path: Path) -> str:
    with zipfile.ZipFile(hwpx_path) as zf:
        data = zf.read("Contents/section0.xml")
    return hashlib.sha256(data).hexdigest()[:12]


def build_page1(source: str, ver: str, prev_ver: str | None, page1_script_count: int) -> dict:
    stem = source.replace(PREFIX, "")
    safe = re.sub(r"[^\w\-]+", "_", source.strip("[]")).strip("_")

    cache = SRC_DIR / f"_{safe}_raw.md"
    if not cache.exists():
        cache = SRC_DIR / f"_{source}_raw.md"
    if not cache.exists():
        return {"status": "skip", "reason": "캐시 없음"}

    md_raw = cache.read_text(encoding="utf-8")
    ranges = get_md_page_range(md_raw)
    print(f"  MD 페이지 범위: {sorted(ranges.keys())}")

    page1_md = get_md_page(md_raw, 1)
    if not page1_md:
        print("  [경고] page 1 추출 실패 — 전체 MD 사용")
        page1_md = md_raw

    template = next(SRC_DIR.glob("*.hwpx"), None)
    out_hwpx = PROD_DIR / f"{source}_{ver}.hwpx"
    pdf = SRC_DIR / f"[{source}].pdf"

    buf = io.StringIO()
    t0  = time.time()
    try:
        with redirect_stdout(buf):
            md_proc = apply_fallback(page1_md, pdf)
            r = build_from_markdown(md_proc, out_hwpx, template)
        elapsed = time.time() - t0
    except Exception:
        return {"status": "error", "reason": traceback.format_exc()}

    pipeline_out = buf.getvalue()
    xml_sha  = _xml_sha(out_hwpx)
    size_kb  = out_hwpx.stat().st_size // 1024

    print(f"  완료: p={r['paragraphs']} eq={r['equations']}  {size_kb}KB  {elapsed:.1f}s")
    print(f"  xml_sha: {xml_sha}")
    if pipeline_out.strip():
        print(f"  pipeline:\n{pipeline_out.strip()}")

    # 페이지 2~N 회귀 검증
    regression = None
    if prev_ver and page1_script_count > 0:
        prev_hwpx = PROD_DIR / f"{source}_{prev_ver}.hwpx"
        if prev_hwpx.exists():
            diff = diff_hwpx_pages(prev_hwpx, out_hwpx, page1_script_count)
            regression = diff["page2n_identical"]
            status = "✅ page2~N 동일" if regression else f"❌ page2~N 다름 ({len(diff['diffs'])}건)"
            print(f"  page 2~N 회귀({prev_ver}→{ver}): {status}")
            if not regression:
                for d in diff["diffs"][:5]:
                    print(f"    idx={d['idx']}  a={d['a'][:60]}  b={d['b'][:60]}")
        else:
            print(f"  [{prev_ver}] HWPX 없음 — 회귀 skip")

    return {
        "status": "ok",
        "paragraphs": r["paragraphs"],
        "equations":  r["equations"],
        "size_kb":    size_kb,
        "elapsed":    round(elapsed, 1),
        "xml_sha":    xml_sha,
        "regression": regression,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--school",  required=True,  help="학교 이름 (e.g. 광주고)")
    ap.add_argument("--ver",     required=True,  help="새 버전 (e.g. v15)")
    ap.add_argument("--prev",    default=None,   help="이전 버전 (e.g. v14) — 회귀 비교용")
    ap.add_argument("--page1-scripts", type=int, default=0,
                    help="page 1 수식 스크립트 개수 (회귀 분리점)")
    ap.add_argument("--force",   action="store_true", help="이미 존재해도 재빌드")
    args = ap.parse_args()

    source = PREFIX + args.school
    out_hwpx = PROD_DIR / f"{source}_{args.ver}.hwpx"

    print(f"\n{'='*60}")
    print(f"[{args.school}] page 1 빌드 → {args.ver}")

    if out_hwpx.exists() and not args.force:
        sha = _xml_sha(out_hwpx)
        print(f"  이미 존재 ({sha}) — --force로 재빌드")
        return

    result = build_page1(source, args.ver, args.prev, args.page1_scripts)
    print(f"\n결과: {result['status']}")
    if result["status"] == "error":
        print(result["reason"])
    print("=" * 60)


if __name__ == "__main__":
    main()
