"""
PDF + 워드초벌 HWPX → 완성 HWPX (전략 B: 문항별 그룹핑 + 타입별 매칭)

사용법:
    py scripts/pdf_to_hwpx.py <PDF> <템플릿.hwpx> <출력.hwpx> [옵션]

옵션:
    --pdf-id <id>            이미 처리된 Mathpix pdf_id 재사용 (재과금 방지)
    --dry-run                XML 치환 없이 분석만 출력
    --highlight              변경된 수식에 색상 표시 (빨강=검수필요, 파랑=형식변경)
    --min-confidence <0~1>   이 값 미만 신뢰도 변경은 적용 안 함 (기본: 0.0)
    --changes <path>         변경 로그 JSON 저장 경로 (기본: samples/changes_<name>.json)
    --report <path>          검수 리포트 MD 저장 경로 (기본: docs/review_<name>.md)

예시:
    py scripts/pdf_to_hwpx.py \\
        "samples/[2025_2_1_a_확통_경신여고].pdf" \\
        "samples/[2025_2_1_a_확통_경신여고][순열 ~ 확률의 뜻과 활용][워드초벌].hwpx" \\
        "samples/output_확통_v2.hwpx" \\
        --pdf-id 0202d1c2-9ca9-4d2f-8f51-ae117bc15d8c \\
        --highlight \\
        --min-confidence 0.5
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.ocr.mathpix_client import MathpixClient, MathpixError
from src.ocr.pdf_parser import parse_pdf_markdown
from src.hwpx.slot_analyzer import analyze_slots, build_slot_map
from src.hwpx.pdf_filler import fill_hwpx_from_pdf_markdown
from src.hwpx.change_log import write_change_log, write_review_report

import zipfile

SEP = "─" * 62


def parse_args():
    args = sys.argv[1:]
    pos, kwargs = [], {}
    i = 0
    while i < len(args):
        if args[i].startswith("--") and i + 1 < len(args) and not args[i + 1].startswith("--"):
            kwargs[args[i][2:]] = args[i + 1]; i += 2
        elif args[i].startswith("--"):
            kwargs[args[i][2:]] = True; i += 1
        else:
            pos.append(args[i]); i += 1
    return pos, kwargs


def main():
    pos, opts = parse_args()
    if len(pos) < 3:
        print("사용법: py scripts/pdf_to_hwpx.py <PDF> <템플릿.hwpx> <출력.hwpx> [옵션]")
        sys.exit(1)

    pdf_path      = Path(pos[0])
    template_path = Path(pos[1])
    output_path   = Path(pos[2])

    pdf_id         = opts.get("pdf-id")
    dry_run        = "dry-run" in opts
    highlight      = "highlight" in opts
    min_confidence = float(opts.get("min-confidence", 0.0))

    # 변경 로그 / 리포트 경로 기본값: 출력 파일명 기반
    stem = output_path.stem
    changes_path = Path(opts.get("changes", f"samples/changes_{stem}.json"))
    report_path  = Path(opts.get("report",  f"docs/review_{stem}.md"))

    for p, label in [(pdf_path, "PDF"), (template_path, "템플릿")]:
        if not p.exists():
            print(f"[오류] {label} 파일 없음: {p}")
            sys.exit(1)

    # ── 1. 마크다운 취득 ────────────────────────────────────────
    print(SEP)
    print("[ 1단계 ] PDF 마크다운 취득")
    print(SEP)

    client = MathpixClient()

    if pdf_id:
        print(f"  기존 pdf_id 재사용: {pdf_id}")
        md = client.fetch_pdf_markdown(pdf_id)
    else:
        print(f"  PDF 제출: {pdf_path.name}")
        result = client.ocr_pdf_to_result(pdf_path, progress=True)
        pdf_id = result.raw.get("pdf_id", "")
        md = client.fetch_pdf_markdown(pdf_id)
        print(f"  pdf_id: {pdf_id}")

    print(f"  마크다운 크기: {len(md):,}자")

    # ── 2. PDF 파싱 ─────────────────────────────────────────────
    print()
    print(SEP)
    print("[ 2단계 ] PDF 문항별 파싱")
    print(SEP)

    pdf_problems = parse_pdf_markdown(md)
    pdf_map = {p.number: p for p in pdf_problems}

    print(f"  파싱된 문항: {len(pdf_problems)}개")
    for prob in pdf_problems:
        kind  = "서술형" if prob.is_essay else "선택형"
        f_cnt = len(prob.formulas)
        a_cnt = len(prob.answers)
        q_cnt = len(prob.quantities)
        print(
            f"  [{prob.number:2d}번/{kind}] "
            f"수식={f_cnt}  답지={a_cnt}  수량={q_cnt}  "
            f"→ 총 {len(prob.tokens)}개 토큰"
        )

    # ── 3. 템플릿 분석 ──────────────────────────────────────────
    print()
    print(SEP)
    print("[ 3단계 ] 템플릿 슬롯 분석")
    print(SEP)

    with zipfile.ZipFile(template_path) as zf:
        xml = zf.read("Contents/section0.xml").decode("utf-8")

    slot_groups = analyze_slots(xml)
    slot_map    = build_slot_map(slot_groups)

    total_slots = sum(g.total() for g in slot_groups)
    print(f"  문항 수: {len(slot_groups)}개")
    print(f"  슬롯 수: {total_slots}개")
    for g in slot_groups:
        in_pdf = "✓" if g.problem in pdf_map else "✗"
        print(
            f"  [{g.problem:2d}번] {in_pdf} "
            f"본문={len(g.content_slots):2d}  답지={len(g.answer_slots)}개"
        )

    if dry_run:
        print("\n[dry-run] 실제 저장 없이 종료.")
        return

    # ── 4. 매칭 + 채우기 ────────────────────────────────────────
    print()
    print(SEP)
    print("[ 4단계 ] 매칭 + HWPX 생성")
    print(SEP)
    if highlight:
        print(f"  하이라이트 ON (빨강=검수필요, 파랑=형식변경)")
    if min_confidence > 0:
        print(f"  최소 신뢰도: {min_confidence} (낮은 신뢰도 변경 건너뜀)")

    report = fill_hwpx_from_pdf_markdown(
        template_path, md, output_path,
        verbose=True,
        highlight=highlight,
        min_confidence=min_confidence,
    )

    # ── 5. 변경 로그 + 검수 리포트 ─────────────────────────────
    write_change_log(report.changes, changes_path)
    write_review_report(report.changes, output_path, report_path, total_slots=report.total_slots)

    # ── 6. 결과 보고 ────────────────────────────────────────────
    print()
    print(SEP)
    print("[ 5단계 ] 결과 요약")
    print(SEP)
    print(f"  전체 슬롯    : {report.total_slots}개")
    print(f"  매칭 성공    : {report.filled}개  ({report.coverage:.1%})")
    print(f"  ├ 답지 슬롯  : {report.answer_filled}개")
    print(f"  └ 본문 슬롯  : {report.content_filled}개")
    print(f"  미매칭 (원본): {report.skipped}개")
    print()

    changes     = report.changes
    suspicious  = [c for c in changes if c.is_suspicious]
    safe        = [c for c in changes if not c.is_suspicious]
    print(f"  변경된 슬롯  : {len(changes)}개")
    print(f"  ├ 형식 변경  : {len(safe)}개 (안전)")
    print(f"  └ 내용 변경  : {len(suspicious)}개 (검수 필요)")

    if suspicious:
        print()
        print("  [ ⚠️  검수 필요 슬롯 ]")
        for rec in suspicious:
            print(f"    [{rec.slot_idx:03d}] {rec.slot_label}")
            print(f"           원본: {rec.original!r}")
            print(f"           적용: {rec.applied!r}")

    # 미완전 매칭 문항
    problem_issues = [
        (pnum, st) for pnum, st in sorted(report.problem_stats.items())
        if not st['pdf_found'] or st['filled'] < st['total']
    ]
    if problem_issues:
        print()
        print("  [ 미완전 매칭 문항 ]")
        for pnum, st in problem_issues:
            reason = "PDF 없음" if not st['pdf_found'] else f"{st['filled']}/{st['total']}"
            print(f"    {pnum:2d}번: {reason}")

    print()
    output_size = output_path.stat().st_size
    print(f"  출력 파일  : {output_path}  ({output_size:,} bytes)")
    print(f"  변경 로그  : {changes_path}")
    print(f"  검수 리포트: {report_path}")
    if highlight:
        print(f"  색상 제거  : py scripts/remove_highlights.py {output_path}")
    print()
    print("  → 한글에서 열어 수식 확인하세요.")


if __name__ == "__main__":
    main()
