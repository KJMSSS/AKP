"""
PDF만 던지면 끝나는 자동 변환 스크립트

사용법:
    py scripts/auto.py <PDF 파일>
    py scripts/auto.py "samples/[2025_2_1_a_확통_경신여고].pdf"

흐름:
    1) PDF 파일명 파싱  →  학교/과목/학기 추출
    2) samples/ 에서 워드초벌 매칭 (3단계 우선순위)
         1순위: 학교+과목+학기 완전일치
         2순위: 학교+과목 일치 (다른 학기)
         3순위: 학교만 일치
    3) 매칭 성공 → 자동 변환
    4) 매칭 실패 → 사용 가능한 워드초벌 목록 표시
                → 사용자가 번호 선택
                → clone_template 으로 새 워드초벌 생성 (→ samples/ 에 저장)
                → 변환 진행
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT          = Path(__file__).resolve().parent.parent.parent
SAMPLES_DIR   = ROOT / "samples"
SCRIPTS_DIR   = ROOT / "scripts"
DOCS_DIR      = ROOT / "docs"
CONVERT_SCRIPT = ROOT / "scripts" / "template" / "pdf_to_hwpx.py"

SEP = "─" * 62

TAG_RE = re.compile(
    r"\[(?P<year>\d{4})_(?P<sem>\d)_(?P<term>\d)_(?P<div>[ab])_"
    r"(?P<subject>[^_\]]+)_(?P<school>[^_\]]+)\]"
)


@dataclass
class ParsedName:
    year: str
    sem: str
    term: str
    div: str
    subject: str
    school: str


@dataclass
class MatchResult:
    template: Path
    score: int
    reason: str


def parse_filename(name: str) -> ParsedName | None:
    m = TAG_RE.search(name)
    if not m:
        return None
    return ParsedName(**m.groupdict())


def list_templates() -> list[Path]:
    """samples/ 내 워드초벌 HWPX 목록 (정렬)."""
    return sorted(
        f for f in SAMPLES_DIR.glob("*.hwpx")
        if "워드초벌" in f.name
    )


def find_template(p: ParsedName) -> MatchResult | None:
    """
    3단계 우선순위로 워드초벌 매칭.
    학교명이 하나도 일치하지 않으면 None 반환.
    """
    candidates: list[MatchResult] = []
    for f in list_templates():
        m = TAG_RE.search(f.name)
        if not m:
            continue
        t = ParsedName(**m.groupdict())
        if t.school != p.school:
            continue  # 학교 일치가 최소 조건

        score = 10  # 학교 일치 기본
        subject_ok = t.subject == p.subject
        sem_ok = (t.year == p.year and t.sem == p.sem and t.term == p.term)

        if subject_ok:
            score += 5
        if sem_ok:
            score += 3

        if subject_ok and sem_ok:
            reason = "완전일치 (학교+과목+학기)"
        elif subject_ok:
            reason = f"학교+과목 일치 (학기: {t.year}_{t.sem}_{t.term})"
        else:
            reason = f"학교만 일치 (과목: {t.subject}, {t.year}_{t.sem}_{t.term})"

        candidates.append(MatchResult(f, score, reason))

    if not candidates:
        return None
    candidates.sort(key=lambda x: -x.score)
    return candidates[0]


def run_conversion(
    pdf_path: Path,
    template: Path,
    output_hwpx: Path,
    changes_path: Path,
    report_path: Path,
    min_confidence: float = 0.5,
) -> bool:
    """pdf_to_hwpx.py 실행. 성공 시 True."""
    if not CONVERT_SCRIPT.exists():
        print(f"[오류] 변환 스크립트 없음: {CONVERT_SCRIPT}")
        return False

    cmd = [
        sys.executable, str(CONVERT_SCRIPT),
        str(pdf_path), str(template), str(output_hwpx),
        "--min-confidence", str(min_confidence),
        "--changes", str(changes_path),
        "--report", str(report_path),
    ]
    print(f"\n▶ 변환 실행 중…  (min-confidence={min_confidence})")
    t0 = time.time()
    r = subprocess.run(cmd)
    dt = time.time() - t0
    if r.returncode != 0:
        print(f"\n  ⨯ 변환 실패 (exit {r.returncode}, {dt:.1f}s)")
        return False
    print(f"\n  완료 ({dt:.1f}s)")
    return True


def pick_template_interactive() -> Path | None:
    """사용 가능한 워드초벌 목록을 보여주고 사용자가 번호를 선택하게 한다."""
    templates = list_templates()
    if not templates:
        print("[오류] samples/ 에 워드초벌 파일이 없습니다.")
        return None

    print("어떤 학교 양식을 복사해서 만들까요?")
    print()
    for i, t in enumerate(templates, 1):
        m = TAG_RE.search(t.name)
        if m:
            label = (
                f"{m.group('school')} / {m.group('subject')}"
                f"  ({m.group('year')}_{m.group('sem')}_{m.group('term')})"
            )
        else:
            label = t.name
        print(f"  {i}. {label}")
    print()

    while True:
        try:
            raw = input(f"번호 선택 (1-{len(templates)}): ").strip()
            idx = int(raw) - 1
            if 0 <= idx < len(templates):
                return templates[idx]
        except (ValueError, EOFError):
            pass
        print(f"  1에서 {len(templates)} 사이의 숫자를 입력하세요.")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("사용법: py scripts/auto.py <PDF 파일>")
        sys.exit(1)

    pdf_path = Path(args[0])
    if not pdf_path.exists():
        print(f"[오류] 파일 없음: {pdf_path}")
        sys.exit(1)

    print(SEP)
    print(f" auto.py  ─  {pdf_path.name}")
    print(SEP)

    # ── 1. 파일명 파싱 ──────────────────────────────────────────
    parsed = parse_filename(pdf_path.name)
    if not parsed:
        print("[오류] 파일명 형식 인식 실패.")
        print("       필요: [YYYY_학기_차수_a_과목_학교명]...pdf")
        print(f"       현재: {pdf_path.name}")
        sys.exit(1)

    print(f"  학교  : {parsed.school}")
    print(f"  과목  : {parsed.subject}")
    print(f"  학기  : {parsed.year}_{parsed.sem}_{parsed.term}_{parsed.div}")
    print()

    # ── 2. 워드초벌 매칭 ────────────────────────────────────────
    print("[ 워드초벌 검색 중… ]")
    match = find_template(parsed)
    new_template_created = False

    if match:
        print(f"  ✓ 매칭: {match.template.name}")
        print(f"     사유: {match.reason}")
        template = match.template

    else:
        # ── 3. 매칭 실패 → 사용자 선택 ──────────────────────────
        print(f"  {parsed.school} 워드초벌이 없습니다.")
        print()

        source_template = pick_template_interactive()
        if source_template is None:
            sys.exit(1)

        print(f"\n  선택: {source_template.name}")

        # 새 워드초벌 파일명: [년도_학기_차수_div_과목_새학교][워드초벌].hwpx
        new_stem = (
            f"[{parsed.year}_{parsed.sem}_{parsed.term}_{parsed.div}"
            f"_{parsed.subject}_{parsed.school}][워드초벌]"
        )
        new_template = SAMPLES_DIR / (new_stem + ".hwpx")

        print()
        print("[ 워드초벌 복사 중… ]")
        print(f"  {source_template.name}")
        print(f"  → {new_template.name}")

        # clone_template 함수 직접 호출
        sys.path.insert(0, str(ROOT))
        from src.template_based.clone_template import clone_template as do_clone  # noqa: PLC0415
        old_school, count = do_clone(source_template, parsed.school, new_template)

        print(f"  교체: {old_school} → {parsed.school}  ({count}곳)")
        if count == 0:
            print("  [경고] XML에서 학교명을 찾지 못했습니다. 수동 확인 필요.")
        else:
            print("  복사 완료. 한글에서 한 번 확인을 권장합니다.")

        template = new_template
        new_template_created = True

    # ── 4. 출력 경로 자동 생성 ──────────────────────────────────
    out_stem = (
        f"output_{parsed.subject}_{parsed.school}"
        f"_{parsed.year}_{parsed.sem}_{parsed.term}"
    )
    output_hwpx  = SAMPLES_DIR / f"{out_stem}.hwpx"
    changes_path = SAMPLES_DIR / f"changes_{out_stem}.json"
    report_path  = DOCS_DIR / f"review_{out_stem}.md"
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    print()
    print(SEP)
    print(f"  워드초벌  : {template.name}")
    print(f"  출력 파일 : {output_hwpx.name}")
    print(f"  검수 리포트: {report_path.name}")
    print(SEP)

    # ── 5. 변환 실행 ────────────────────────────────────────────
    ok = run_conversion(pdf_path, template, output_hwpx, changes_path, report_path)
    if not ok:
        sys.exit(1)

    # ── 6. 결과 보고 ────────────────────────────────────────────
    print()
    print(SEP)
    print(" 완료")
    print(SEP)
    print(f"  출력     : {output_hwpx}")
    print(f"  리포트   : {report_path}")
    print(f"  변경로그 : {changes_path}")

    if new_template_created:
        print()
        print(f"  새 워드초벌이 samples/ 에 추가되었습니다:")
        print(f"    {template.name}")
        print(f"  다음부터 자동 매칭됩니다.")

    print()


if __name__ == "__main__":
    main()
