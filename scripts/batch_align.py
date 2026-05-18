"""
야간 배치 — samples/11b/ 전체 쌍 정합 + JSONL 저장

사용법:
  py scripts/batch_align.py                 # 전체 실행
  py scripts/batch_align.py --dry-run       # OCR 호출 없이 캐시만 처리
  py scripts/batch_align.py --limit N       # N쌍만 처리

출력:
  samples/11b/_aligned_dataset.jsonl
  samples/11b/_batch_log.txt

한도:
  Mathpix: $20 cap (페이지당 ~$0.007, 6페이지 기준 쌍당 ~$0.04)
  최대 약 500쌍 처리 가능 (11b는 ~30쌍)
"""
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from src.learn.pair_align import align

ROOT      = Path(__file__).resolve().parent.parent
DIR_11B   = ROOT / "samples" / "11b"
OUT_JSONL = DIR_11B / "_aligned_dataset.jsonl"
OUT_LOG   = DIR_11B / "_batch_log.txt"

# Mathpix 비용 한도 (USD) — 초과 시 중단
MATHPIX_COST_CAP = 20.0
# 페이지당 예상 비용 (보수적 추정)
COST_PER_PAGE = 0.008
# 문서당 기본 예상 페이지
DEFAULT_PAGES = 8


def _log(msg: str, f=None) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    if f:
        f.write(line + "\n")
        f.flush()


def _find_pairs() -> list[tuple[Path, Path]]:
    """PDF + HWPX 쌍 탐색 (파일명 stem 일치 기준)."""
    pairs = []
    for pdf in sorted(DIR_11B.glob("*.pdf")):
        hwpx = pdf.with_suffix(".hwpx")
        if hwpx.exists():
            pairs.append((pdf, hwpx))
    return pairs


def _cache_path(pdf: Path) -> Path:
    """PDF → 캐시 마크다운 경로."""
    stem = pdf.stem.strip("[]")   # "[2025_1_1_b_...]" → "2025_1_1_b_..."
    return DIR_11B / f"_{stem}_raw.md"


def _source_name(pdf: Path) -> str:
    """파일명에서 소스 태그 추출 (예: 2025_1_1_b_공수1_광주고)."""
    return pdf.stem.strip("[]")


def _fetch_md(pdf: Path, cache: Path, dry_run: bool, log_f) -> str | None:
    """마크다운 취득: 캐시 → Mathpix 호출. dry_run이면 캐시만 사용."""
    if cache.exists():
        _log(f"  캐시 사용: {cache.name}", log_f)
        return cache.read_text(encoding="utf-8")
    if dry_run:
        _log(f"  캐시 없음 + dry-run → skip: {pdf.name}", log_f)
        return None
    from src.common.ocr.mathpix_client import MathpixClient
    client = MathpixClient()
    _log(f"  Mathpix 제출: {pdf.name}", log_f)
    t0 = time.time()
    try:
        pdf_id = client.submit_pdf(pdf)
        _log(f"  pdf_id={pdf_id}", log_f)
        client.poll_pdf(pdf_id, progress=True)
        md = client.fetch_pdf_markdown(pdf_id)
        elapsed = time.time() - t0
        _log(f"  완료: {len(md):,}자 ({elapsed:.1f}s)", log_f)
        cache.write_text(md, encoding="utf-8")
        return md
    except Exception as e:
        _log(f"  OCR 실패: {e}", log_f)
        return None


def main() -> None:
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    limit = None
    if "--limit" in args:
        idx = args.index("--limit")
        limit = int(args[idx + 1])

    if dry_run:
        print("  [dry-run 모드: OCR 호출 없음]")

    pairs = _find_pairs()
    if limit:
        pairs = pairs[:limit]

    total_pairs   = len(pairs)
    done_ok       = 0
    done_skip     = 0
    done_err      = 0
    total_records = 0
    estimated_cost = 0.0

    # 출력 파일 열기 (append 모드 — 재실행 시 이어쓰기)
    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    seen_sources: set[str] = set()
    if OUT_JSONL.exists():
        with open(OUT_JSONL, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    seen_sources.add(rec.get("source", ""))
                except json.JSONDecodeError:
                    pass

    print("─" * 62)
    print(f"  배치 시작: {total_pairs}쌍  (이미 완료: {len(seen_sources)}개 소스)")
    print("─" * 62)

    with (
        open(OUT_LOG, "a", encoding="utf-8") as log_f,
        open(OUT_JSONL, "a", encoding="utf-8") as jsonl_f,
    ):
        _log(f"=== 배치 시작 {datetime.now():%Y-%m-%d %H:%M:%S} ===", log_f)

        for i, (pdf, hwpx) in enumerate(pairs, 1):
            source = _source_name(pdf)
            _log(f"\n[{i}/{total_pairs}] {source}", log_f)

            # 이미 완료된 소스는 skip
            if source in seen_sources:
                _log("  이미 완료 → skip", log_f)
                done_skip += 1
                continue

            # 비용 한도 체크
            if estimated_cost >= MATHPIX_COST_CAP:
                _log(f"  Mathpix 비용 한도 도달 (${estimated_cost:.2f}) → 중단", log_f)
                break

            cache = _cache_path(pdf)
            is_new_ocr = not cache.exists()

            try:
                md = _fetch_md(pdf, cache, dry_run, log_f)
                if md is None:
                    done_skip += 1
                    continue

                if is_new_ocr:
                    estimated_cost += DEFAULT_PAGES * COST_PER_PAGE
                    _log(f"  예상 누적 비용: ${estimated_cost:.2f}", log_f)

                pairs_aligned, only_raw, only_gold = align(md, hwpx)
                n_ok = len(pairs_aligned)
                n_all = len(pairs_aligned) + len(only_gold)
                rate  = n_ok / max(n_all, 1) * 100

                _log(f"  정합: {n_ok}/{n_all} ({rate:.0f}%)  미정합 gold={only_gold}", log_f)

                for ap in pairs_aligned:
                    rec = ap.to_record(source=source)
                    jsonl_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    total_records += 1
                jsonl_f.flush()

                done_ok += 1

            except Exception:
                err = traceback.format_exc()
                _log(f"  오류: {err}", log_f)
                done_err += 1

        _log(
            f"\n=== 배치 완료 ===\n"
            f"  성공: {done_ok}  skip: {done_skip}  오류: {done_err}\n"
            f"  총 레코드: {total_records}개\n"
            f"  예상 비용: ${estimated_cost:.2f}",
            log_f,
        )

    print()
    print("─" * 62)
    print(f"  성공: {done_ok}  skip: {done_skip}  오류: {done_err}")
    print(f"  총 레코드: {total_records}개")
    print(f"  예상 Mathpix 비용: ${estimated_cost:.2f}")
    print(f"  출력: {OUT_JSONL}")
    print("─" * 62)


if __name__ == "__main__":
    main()
