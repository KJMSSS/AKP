"""
이전에 저장한 pdf_id를 재사용해 pdf_to_hwpx.py를 다시 돌리고,
모든 stage의 상세 출력을 콘솔과 로그에 저장한다.

Mathpix는 24시간 결과 캐시 → API 비용 없음.
"""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(r"D:\f1\AKP")
OUTBOX = ROOT / "학원공유" / "완료"
SAMPLES = ROOT / "samples"
INBOX = ROOT / "학원공유" / "미처리"
DONE = INBOX / "_done"

PDF_NAME = "[2026_1_1_a_공수1_경신여고].pdf"
TEMPLATE = "[2025_1_1_a_공수1_경신여고][다항식 ~ 이차함수][워드초벌].hwpx"

LOG = OUTBOX / "diagnose_detail.log"
PDF_ID_FILE = OUTBOX / "[2026_1_1_a_공수1_경신여고].pdf_id.txt"


def find_pdf():
    for d in (INBOX, DONE):
        p = d / PDF_NAME
        if p.exists():
            return p
    return None


def main():
    pdf = find_pdf()
    if not pdf:
        print(f"[ERROR] PDF not found in inbox or _done: {PDF_NAME}")
        sys.exit(1)

    if not PDF_ID_FILE.exists():
        print(f"[ERROR] pdf_id file not found: {PDF_ID_FILE}")
        sys.exit(1)

    pdf_id = PDF_ID_FILE.read_text(encoding="utf-8").strip()
    template = SAMPLES / TEMPLATE
    output = OUTBOX / "[2026_1_1_a_공수1_경신여고].diagnose.hwpx"

    if not template.exists():
        print(f"[ERROR] Template not found: {template}")
        sys.exit(1)

    print("=" * 60)
    print(f"  Re-running pdf_to_hwpx.py with cached pdf_id")
    print(f"  pdf_id: {pdf_id}")
    print(f"  Log: {LOG}")
    print("=" * 60)
    print()

    cmd = [
        sys.executable, "-X", "utf8",
        str(ROOT / "scripts" / "pdf_to_hwpx.py"),
        str(pdf),
        str(template),
        str(output),
        "--pdf-id", pdf_id,
        "--min-confidence", "0.0",   # 모든 내용 변경 적용 (워크플로우상 0.5는 너무 강함)
        "--changes", str(OUTBOX / "[2026_1_1_a_공수1_경신여고].diagnose.changes.json"),
        "--report", str(OUTBOX / "[2026_1_1_a_공수1_경신여고].diagnose.review.md"),
    ]

    OUTBOX.mkdir(parents=True, exist_ok=True)
    with LOG.open("w", encoding="utf-8") as logf:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="")
            logf.write(line)
        rc = proc.wait()

    print()
    print("=" * 60)
    print(f"  Exit code: {rc}")
    print(f"  Detail log saved: {LOG}")
    print("=" * 60)


if __name__ == "__main__":
    main()
