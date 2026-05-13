"""
batch_convert.py를 호출하면서 로그를 자동으로 파일에 저장한다.
.bat에서 한글 경로 인코딩 문제를 피하기 위한 wrapper.
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
SCRIPT = ROOT / "scripts" / "template" / "batch_convert.py"
LOG = ROOT / "학원공유" / "완료" / "last_run.log"


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    extra = sys.argv[1:]
    cmd = [sys.executable, "-X", "utf8", str(SCRIPT), *extra]

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
    print(f"  Log saved: {LOG}")
    print(f"  Exit code: {rc}")
    print("=" * 60)
    sys.exit(rc)


if __name__ == "__main__":
    main()
