"""
경신여고 PDF 재변환 + 디버그 로그 자동 저장.

이 스크립트는 .bat에서 한글 경로/파일명 인코딩 문제를 피하기 위해
모든 파일 이동 로직을 Python으로 옮긴 것입니다.

사용:
    py D:\\f1\\AKP\\scripts\\diagnose_run.py
"""
from __future__ import annotations
import shutil
import subprocess
import sys
from pathlib import Path

# UTF-8 stdout (Windows 콘솔)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(r"D:\f1\AKP")
INBOX = ROOT / "학원공유" / "미처리"
DONE = INBOX / "_done"
OUTBOX = ROOT / "학원공유" / "완료"

TARGET_PDF = "[2026_1_1_a_공수1_경신여고].pdf"
TARGET_HWPX = "[2026_1_1_a_공수1_경신여고].hwpx"
PREV_HWPX = "[2026_1_1_a_공수1_경신여고].prev.hwpx"

LOG_PATH = OUTBOX / "last_run.log"
SCRIPT = ROOT / "scripts" / "batch_convert.py"


def main():
    print("=" * 60)
    print(" Diagnostic re-run for Gyeongsin-yeogo PDF")
    print("=" * 60)

    # 1) _done → 미처리
    src = DONE / TARGET_PDF
    if src.exists():
        shutil.move(str(src), str(INBOX / TARGET_PDF))
        print(f"  [1/3] PDF restored to inbox")
    elif (INBOX / TARGET_PDF).exists():
        print(f"  [1/3] PDF already in inbox")
    else:
        print(f"  [1/3] PDF NOT FOUND — abort")
        print(f"        searched: {src}")
        print(f"        searched: {INBOX / TARGET_PDF}")
        sys.exit(1)

    # 2) 이전 .hwpx 백업
    prev = OUTBOX / TARGET_HWPX
    if prev.exists():
        backup = OUTBOX / PREV_HWPX
        if backup.exists():
            backup.unlink()
        shutil.move(str(prev), str(backup))
        print(f"  [2/3] Previous output backed up: {PREV_HWPX}")
    else:
        print(f"  [2/3] No previous output to backup")

    # 3) batch_convert.py 실행, 로그를 파일과 콘솔에 모두 저장
    print(f"  [3/3] Running conversion (Mathpix OCR may take minutes)...")
    print()

    OUTBOX.mkdir(parents=True, exist_ok=True)

    cmd = [sys.executable, "-X", "utf8", str(SCRIPT)]
    with LOG_PATH.open("w", encoding="utf-8") as logf:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="")
            logf.write(line)
        rc = proc.wait()

    print()
    print("=" * 60)
    print(f"  Exit code: {rc}")
    print(f"  Log saved: {LOG_PATH}")
    print(f"  Markdown: {OUTBOX}/[2026_1_1_a_공수1_경신여고].mathpix.md")
    print("=" * 60)


if __name__ == "__main__":
    main()
