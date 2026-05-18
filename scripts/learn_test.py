"""
Phase 0 검증 — 광주고 단일 쌍 정합 테스트.

사용법:
  py scripts/learn_test.py                    # Mathpix OCR 자동 실행
  py scripts/learn_test.py <마크다운경로>      # 캐시 md 사용
  py scripts/learn_test.py --pdf-id <id>      # 기존 pdf_id 재사용

광주고 HWPX:   samples/11b/[2025_1_1_b_공수1_광주고].hwpx
광주고 PDF:    samples/11b/[2025_1_1_b_공수1_광주고].pdf
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from src.learn.hwpx_reader import read_hwpx
from src.learn.pair_align import align

ROOT    = Path(__file__).resolve().parent.parent
HWPX    = ROOT / "samples" / "11b" / "[2025_1_1_b_공수1_광주고].hwpx"
PDF     = ROOT / "samples" / "11b" / "[2025_1_1_b_공수1_광주고].pdf"
MD_CACHE = ROOT / "samples" / "11b" / "_광주고_2025_1_1_b_raw.md"


def section(title: str) -> None:
    print()
    print("─" * 62)
    print(f"  {title}")
    print("─" * 62)


def _fetch_markdown() -> str:
    """PDF 마크다운 취득: 캐시 → pdf_id 재사용 → 신규 Mathpix 호출."""
    args = sys.argv[1:]

    # --pdf-id <id> 옵션
    if "--pdf-id" in args:
        idx = args.index("--pdf-id")
        pdf_id = args[idx + 1]
        from src.common.ocr.mathpix_client import MathpixClient
        client = MathpixClient()
        print(f"  기존 pdf_id 재사용: {pdf_id}")
        client.poll_pdf(pdf_id, progress=True)
        md = client.fetch_pdf_markdown(pdf_id)
        MD_CACHE.write_text(md, encoding="utf-8")
        return md

    # 위치 인수 = 마크다운 파일 경로
    positional = [a for a in args if not a.startswith("--")]
    if positional:
        p = Path(positional[0])
        if p.exists():
            return p.read_text(encoding="utf-8")
        print(f"마크다운 없음: {p}")
        sys.exit(1)

    # 캐시 확인
    if MD_CACHE.exists():
        print(f"  캐시 사용: {MD_CACHE.name}")
        return MD_CACHE.read_text(encoding="utf-8")

    # 신규 Mathpix 호출
    if not PDF.exists():
        print(f"PDF 없음: {PDF}")
        sys.exit(1)
    from src.common.ocr.mathpix_client import MathpixClient
    client = MathpixClient()
    print(f"  Mathpix OCR 호출: {PDF.name}")
    t0 = time.time()
    pdf_id = client.submit_pdf(PDF)
    print(f"  제출 완료 (pdf_id={pdf_id}) — 재실행 시 --pdf-id {pdf_id} 사용 가능")
    client.poll_pdf(pdf_id, progress=True)
    md = client.fetch_pdf_markdown(pdf_id)
    elapsed = time.time() - t0
    print(f"  완료: {len(md):,}자 ({elapsed:.1f}s)")
    MD_CACHE.write_text(md, encoding="utf-8")
    print(f"  캐시 저장: {MD_CACHE.name}")
    return md


def main() -> None:
    if not HWPX.exists():
        print(f"HWPX 없음: {HWPX}")
        sys.exit(1)

    # ── 1. HWPX 단독 파싱 ─────────────────────────────────────────
    section("1. HWPX 파싱 (gold)")
    gold_probs = read_hwpx(HWPX)
    eq_total   = sum(len(p.equations()) for p in gold_probs)
    img_total  = sum(p.image_count()   for p in gold_probs)
    print(f"  문제 수  : {len(gold_probs)}개")
    print(f"  수식 수  : {eq_total}개")
    print(f"  이미지 수: {img_total}개")

    # 10번, 18번 샘플 출력
    for n in (10, 18):
        prob = next((p for p in gold_probs if p.num == n), None)
        if prob:
            print(f"\n  [gold #{n}번]")
            print(f"    점수  : {prob.score}점")
            print(f"    수식  : {len(prob.equations())}개")
            print(f"    이미지: {prob.image_count()}개")
            print(f"    수식 샘플:")
            for eq in prob.equations()[:5]:
                print(f"      {eq!r}")
            print(f"    보기 수: {len(prob.choices)}개")
            for ci, ch in enumerate(prob.choices[:3], 1):
                ch_eqs = [t.value for t in ch.tokens if t.kind == "eq"]
                print(f"      {ch.bullet}: eqs={ch_eqs[:2]}")

    # ── 2. 정합 ───────────────────────────────────────────────────
    section("2. PDF + HWPX 정합")
    md = _fetch_markdown()
    print(f"  마크다운: {len(md):,}자")

    pairs, only_raw, only_gold = align(md, HWPX)
    print(f"  정합 성공: {len(pairs)}문제")
    print(f"  raw 전용 : {only_raw}")
    print(f"  gold 전용: {only_gold}")

    # ── 3. diff 요약 (전 문제) ────────────────────────────────────
    section("3. 문제별 raw vs gold diff")
    print(f"  {'번':>3}  {'raw_eq':>6}  {'gold_eq':>7}  {'diff':>5}  {'img':>3}  {'score':>6}  {'choices':>7}")
    print("  " + "-" * 55)
    for p in pairs:
        d = p.diff_summary()
        diff_str = f"{d['eq_diff']:+d}"
        print(
            f"  {d['num']:>3}  {d['raw_eq']:>6}  {d['gold_eq']:>7}"
            f"  {diff_str:>5}  {d['gold_img']:>3}  {str(d['gold_score']):>6}  {d['gold_choices']:>7}"
        )

    # ── 4. diff 샘플 5개 ─────────────────────────────────────────
    section("4. raw vs gold 차이 샘플 (상위 5개)")
    sorted_pairs = sorted(pairs, key=lambda p: abs(p.diff_summary()["eq_diff"]), reverse=True)
    shown = 0
    for p in sorted_pairs:
        if shown >= 5:
            break
        d = p.diff_summary()
        if d["eq_diff"] == 0:
            break
        print(f"\n  === {p.num}번 (eq diff={d['eq_diff']:+d}) ===")
        print(f"  raw  inline_eqs ({len(p.raw.inline_eqs)}개): {p.raw.inline_eqs[:3]}")
        print(f"  gold equations  ({len(p.gold.equations())}개): {p.gold.equations()[:3]}")
        shown += 1

    # ── 5. 야간 배치 판정 ─────────────────────────────────────────
    section("5. 야간 배치 진행 가능 여부")
    align_rate = len(pairs) / max(len(gold_probs), 1) * 100
    print(f"  정합률: {align_rate:.1f}%  ({len(pairs)}/{len(gold_probs)})")
    if align_rate >= 80:
        print("  → OK: 야간 배치 진행 가능 (80% 이상 정합)")
    else:
        print("  → 주의: 정합률 부족 — 파싱 로직 보완 필요")


if __name__ == "__main__":
    main()
