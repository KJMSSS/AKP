"""
학원 시험지 일괄 변환 자동화

흐름:
    D:\\f1\\AKP\\학원공유\\미처리\\*.pdf
        ↓ 파일명에서 학교명/과목 추출
        ↓ samples/ 에서 워드초벌 매칭
        ↓ pdf_to_hwpx.py 실행
    D:\\f1\\AKP\\학원공유\\완료\\<원본명>.hwpx + review.md
    매칭 실패 시: 미처리\\_매칭실패\\로 이동

사용법:
    py D:\\f1\\AKP\\scripts\\batch_convert.py
    py D:\\f1\\AKP\\scripts\\batch_convert.py --min-confidence 0.6
    py D:\\f1\\AKP\\scripts\\batch_convert.py --dry-run    # 매칭만 미리보기
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Windows 콘솔에서 한글 깨짐 방지
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ── 경로 설정 ───────────────────────────────────────────────
# __file__ 기준으로 ROOT 자동 인식 (Windows·Linux 양쪽 동작)
ROOT          = Path(__file__).resolve().parent.parent.parent
SAMPLES_DIR   = ROOT / "samples"
SCRIPTS_DIR   = ROOT / "scripts"
SHARED_DIR    = ROOT / "학원공유"
INBOX_DIR     = SHARED_DIR / "미처리"
OUTBOX_DIR    = SHARED_DIR / "완료"
FAILED_DIR    = INBOX_DIR / "_매칭실패"
DONE_DIR      = INBOX_DIR / "_done"   # 변환 끝난 원본 PDF 보관

CONVERT_SCRIPT = ROOT / "scripts" / "template" / "pdf_to_hwpx.py"

SEP = "─" * 70

# ── 파일명 파싱 ─────────────────────────────────────────────
# 예: [2025_2_1_a_확통_경신여고]...pdf
#  → year=2025, sem=2, term=1, div=a, subject=확통, school=경신여고
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

    def key_full(self) -> str:
        return f"{self.year}_{self.sem}_{self.term}_{self.div}_{self.subject}_{self.school}"


def parse_filename(name: str) -> ParsedName | None:
    m = TAG_RE.search(name)
    if not m:
        return None
    return ParsedName(**m.groupdict())


# ── 템플릿 매칭 ─────────────────────────────────────────────
@dataclass
class MatchResult:
    template: Path
    score: int      # 높을수록 정확 (4: 학교+과목+학기 완전일치 등)
    reason: str


def find_template(p: ParsedName) -> MatchResult | None:
    """samples/ 에서 워드초벌.hwpx 매칭. 가장 점수 높은 것 반환."""
    candidates: list[MatchResult] = []
    for f in SAMPLES_DIR.glob("*[워드초벌]*.hwpx"):
        m = TAG_RE.search(f.name)
        if not m:
            continue
        t = ParsedName(**m.groupdict())
        # 학교 일치는 필수 — 다른 학교 템플릿으로 변환하면 안 됨
        if t.school != p.school:
            continue
        score = 4  # 학교 일치 기본점
        if t.subject == p.subject:
            score += 3
        if t.year == p.year:
            score += 1
        if t.sem == p.sem and t.term == p.term:
            score += 1
        reason = f"학교:O 과목:{'O' if t.subject==p.subject else 'X'} 학기:{t.year}_{t.sem}_{t.term}"
        candidates.append(MatchResult(f, score, reason))

    if not candidates:
        return None
    candidates.sort(key=lambda x: -x.score)
    return candidates[0]


# ── 인자 파싱 ───────────────────────────────────────────────
def parse_args():
    args = sys.argv[1:]
    # 기본값 0.0 — 워드초벌(이전 시험)에서 새 시험으로 통째로 갈아끼우는 워크플로우용.
    # _confidence()는 형식 동일=1.0, 내용 다름=0.3 두 값밖에 없어서 0.5는 모든 내용 변경을 차단함.
    opts = {"min-confidence": "0.0", "dry-run": False}
    i = 0
    while i < len(args):
        if args[i] == "--dry-run":
            opts["dry-run"] = True
            i += 1
        elif args[i].startswith("--") and i + 1 < len(args):
            opts[args[i][2:]] = args[i + 1]
            i += 2
        else:
            i += 1
    return opts


# ── 리포트 분석 ─────────────────────────────────────────────
SUSPICIOUS_PATTERNS = [
    "검수필요", "신뢰도 낮", "⚠", "WARN", "주의",
    "low confidence", "수동확인",
]


def count_suspicious(report_path: Path) -> int:
    if not report_path.exists():
        return 0
    text = report_path.read_text(encoding="utf-8", errors="ignore")
    return sum(text.count(p) for p in SUSPICIOUS_PATTERNS)


# ── 메인 ────────────────────────────────────────────────────
def main():
    opts = parse_args()
    min_conf = opts["min-confidence"]
    dry_run = opts["dry-run"]

    # 폴더 보장
    for d in [INBOX_DIR, OUTBOX_DIR, FAILED_DIR, DONE_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    if not CONVERT_SCRIPT.exists():
        print(f"[오류] 변환 스크립트 없음: {CONVERT_SCRIPT}")
        sys.exit(1)

    pdfs = sorted(
        p for p in INBOX_DIR.glob("*.pdf")
        if p.is_file()  # _매칭실패, _done 등 하위폴더 제외
    )

    print(SEP)
    print(f" 일괄 변환 시작 — 대상 {len(pdfs)}개 (PDF in {INBOX_DIR})")
    print(f" min-confidence: {min_conf}{'  (DRY-RUN)' if dry_run else ''}")
    print(SEP)

    if not pdfs:
        print("  변환할 PDF가 없습니다. 미처리 폴더에 PDF를 넣어주세요.")
        return

    success: list[tuple[Path, Path, int]] = []   # (pdf, hwpx, suspicious_count)
    failed: list[tuple[Path, str]] = []          # (pdf, 사유)
    unmatched: list[Path] = []

    for idx, pdf in enumerate(pdfs, 1):
        print(f"\n[{idx}/{len(pdfs)}] {pdf.name}")
        parsed = parse_filename(pdf.name)
        if not parsed:
            print(f"   ⨯ 파일명 형식 인식 실패 — [...년도_학기_차수_a_과목_학교명...] 형태 필요")
            unmatched.append(pdf)
            if not dry_run:
                shutil.move(str(pdf), str(FAILED_DIR / pdf.name))
            continue

        print(f"   학교={parsed.school}  과목={parsed.subject}  {parsed.year}_{parsed.sem}_{parsed.term}_{parsed.div}")

        match = find_template(parsed)
        if not match:
            print(f"   ⨯ 매칭되는 워드초벌 없음 (학교: {parsed.school})")
            unmatched.append(pdf)
            if not dry_run:
                shutil.move(str(pdf), str(FAILED_DIR / pdf.name))
            continue

        print(f"   ✓ 템플릿: {match.template.name}  [점수 {match.score}]")

        if dry_run:
            continue

        # ── 변환 실행 ──
        out_hwpx = OUTBOX_DIR / (pdf.stem + ".hwpx")
        changes_json = OUTBOX_DIR / (pdf.stem + ".changes.json")
        report_md = OUTBOX_DIR / (pdf.stem + ".review.md")

        cmd = [
            sys.executable, str(CONVERT_SCRIPT),
            str(pdf), str(match.template), str(out_hwpx),
            "--min-confidence", str(min_conf),
            "--changes", str(changes_json),
            "--report", str(report_md),
        ]
        print(f"   ▶ 실행: pdf_to_hwpx.py …")
        print(f"   ┄┄┄┄ pdf_to_hwpx.py 출력 시작 ┄┄┄┄")
        t0 = time.time()
        try:
            # stdout/stderr를 직접 전달 — 자식 프로세스 출력이 그대로 콘솔/로그에 흐름
            r = subprocess.run(cmd)
        except Exception as e:
            print(f"   ⨯ 실행 오류: {e}")
            failed.append((pdf, f"실행 오류: {e}"))
            continue
        dt = time.time() - t0
        print(f"   ┄┄┄┄ pdf_to_hwpx.py 출력 끝 ┄┄┄┄")

        if r.returncode != 0:
            print(f"   ⨯ 변환 실패 (exit {r.returncode}, {dt:.1f}s)")
            failed.append((pdf, f"exit {r.returncode}"))
            continue

        if not out_hwpx.exists():
            print(f"   ⨯ 변환은 끝났는데 출력 파일이 없음: {out_hwpx.name}")
            failed.append((pdf, "출력 파일 누락"))
            continue

        susp = count_suspicious(report_md)
        flag = f"  ⚠ 의심 변경 {susp}건" if susp else ""
        print(f"   ✓ 완료 ({dt:.1f}s){flag}")

        # 원본 PDF는 _done 폴더로
        shutil.move(str(pdf), str(DONE_DIR / pdf.name))
        success.append((pdf, out_hwpx, susp))

    # ── 요약 ─────────────────────────────────────────────────
    print("\n" + SEP)
    print(" 요약")
    print(SEP)
    print(f"  성공         : {len(success)}개")
    print(f"  실패         : {len(failed)}개")
    print(f"  매칭 실패    : {len(unmatched)}개")

    if success:
        print("\n  [변환 완료]")
        for pdf, hwpx, susp in success:
            mark = f"  ⚠ {susp}건 의심" if susp else ""
            print(f"    • {hwpx.name}{mark}")

    if failed:
        print("\n  [변환 실패]")
        for pdf, reason in failed:
            print(f"    • {pdf.name}  ({reason})")

    if unmatched:
        print(f"\n  [매칭 실패 — {FAILED_DIR} 로 이동]")
        for pdf in unmatched:
            print(f"    • {pdf.name}")

    print(f"\n  결과 폴더: {OUTBOX_DIR}")


if __name__ == "__main__":
    main()
# end
