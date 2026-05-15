"""
samples/11b/ 전체 학교 일괄 production HWPX 생성.

사용법:
  py scripts/batch_production.py            # 전체 (명진고 자동 제외)
  py scripts/batch_production.py --limit N  # N개만

출력: samples/11b_production/[소스명]_v1.hwpx

파이프라인 (Mathpix 호출 없음, 캐시 사용):
  cached_md → apply_fallback → build_from_markdown → .hwpx
"""
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from src.text_only.text_builder import build_from_markdown
from src.text_only.ocr_fallback import apply_fallback

ROOT        = Path(__file__).resolve().parent.parent
DIR_11B     = ROOT / "samples" / "11b"
OUT_DIR     = ROOT / "samples" / "11b_production"

# 명진고 제외 (OCR 실패)
SKIP_SOURCES = {"2025_1_1_b_공수1_명진고"}

# header.xml 참조용 템플릿 — 11b 금 HWPX 중 첫 번째
_TEMPLATE = next(DIR_11B.glob("*.hwpx"), None)


def _find_pairs() -> list[tuple[Path, Path]]:
    """캐시 md + PDF 쌍 탐색."""
    pairs = []
    for pdf in sorted(DIR_11B.glob("*.pdf")):
        stem   = pdf.stem.strip("[]")
        cache  = DIR_11B / f"_{stem}_raw.md"
        if cache.exists():
            pairs.append((pdf, cache))
    return pairs


def _source_name(pdf: Path) -> str:
    return pdf.stem.strip("[]")


def main() -> None:
    args    = sys.argv[1:]
    limit   = None
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])

    pairs = _find_pairs()
    if limit:
        pairs = pairs[:limit]

    template = _TEMPLATE
    if not template or not template.exists():
        print("오류: 11b 폴더에 템플릿 HWPX가 없습니다.")
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("─" * 64)
    print(f"  batch_production 시작: {len(pairs)}개 쌍")
    print(f"  제외: {SKIP_SOURCES}")
    print(f"  출력: {OUT_DIR}")
    print(f"  템플릿: {template.name}")
    print("─" * 64)

    results = []
    done_ok = done_skip = done_err = 0

    for i, (pdf, cache) in enumerate(pairs, 1):
        source = _source_name(pdf)
        print(f"\n[{i}/{len(pairs)}] {source}")

        if source in SKIP_SOURCES:
            print("  → 제외 대상 skip")
            done_skip += 1
            results.append({"source": source, "status": "skip", "reason": "제외 목록"})
            continue

        out_hwpx = OUT_DIR / f"{source}_v1.hwpx"
        if out_hwpx.exists():
            print(f"  → 이미 존재 skip ({out_hwpx.name})")
            done_skip += 1
            results.append({"source": source, "status": "skip", "reason": "기존 파일"})
            continue

        try:
            md = cache.read_text(encoding="utf-8")
            t0 = time.time()

            md = apply_fallback(md, pdf)
            r  = build_from_markdown(md, out_hwpx, template)

            elapsed = time.time() - t0
            size_kb = out_hwpx.stat().st_size // 1024
            print(f"  → 완료: p={r['paragraphs']} eq={r['equations']}  {size_kb}KB  {elapsed:.1f}s")

            done_ok += 1
            results.append({
                "source":     source,
                "status":     "ok",
                "paragraphs": r["paragraphs"],
                "equations":  r["equations"],
                "size_kb":    size_kb,
                "elapsed":    round(elapsed, 1),
            })

        except Exception:
            err = traceback.format_exc()
            print(f"  → 오류:\n{err}")
            done_err += 1
            results.append({"source": source, "status": "error", "reason": err[:120]})

    # ── 요약 ────────────────────────────────────────────────────────────
    print()
    print("─" * 64)
    print(f"  완료: {done_ok}  skip: {done_skip}  오류: {done_err}")
    print()
    print(f"  {'학교':<30} {'상태':<6} {'문단':>5} {'수식':>5} {'KB':>5} {'초':>5}")
    print(f"  {'-'*30} {'-'*6} {'-'*5} {'-'*5} {'-'*5} {'-'*5}")
    for r in results:
        if r["status"] == "ok":
            print(
                f"  {r['source'][-20:]:<30} ok     "
                f"{r['paragraphs']:>5} {r['equations']:>5} "
                f"{r['size_kb']:>5} {r['elapsed']:>5}"
            )
        else:
            reason = r.get('reason', '')[:20]
            print(f"  {r['source'][-20:]:<30} {r['status']:<6} — {reason}")
    print("─" * 64)


if __name__ == "__main__":
    main()
