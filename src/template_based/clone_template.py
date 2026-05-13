"""
HWPX 워드초벌 복사 + 학교명 교체 유틸리티

함수 사용:
    from clone_template import clone_template
    old_school, count = clone_template(source, "부산○○고", output)

CLI 사용:
    py scripts/clone_template.py <source.hwpx> <새학교명> <output.hwpx>
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

TAG_RE = re.compile(
    r"\[(?P<year>\d{4})_(?P<sem>\d)_(?P<term>\d)_(?P<div>[ab])_"
    r"(?P<subject>[^_\]]+)_(?P<school>[^_\]]+)\]"
)

# HWPX 내에서 텍스트를 담는 XML-like 파일 확장자
_TEXT_EXTS = {".xml", ".hpf", ".hpsx"}


def clone_template(
    source_hwpx: str | Path,
    new_school_name: str,
    output_path: str | Path,
) -> tuple[str, int]:
    """
    워드초벌 HWPX를 복사하고 학교명 텍스트를 교체한다.

    - 원본 학교명은 source_hwpx 파일명의 TAG_RE 패턴에서 추출
    - XML 파일 내 해당 문자열을 전체 교체 (정확 일치)
    - 이진 파일(이미지 등)은 그대로 복사

    Returns:
        (old_school_name, replacement_count)
    """
    source_hwpx = Path(source_hwpx)
    output_path = Path(output_path)

    m = TAG_RE.search(source_hwpx.name)
    if not m:
        raise ValueError(f"파일명에서 학교명 추출 실패: {source_hwpx.name}")
    old_school = m.group("school")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    with zipfile.ZipFile(source_hwpx, "r") as zin, \
         zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            suffix = Path(item.filename).suffix.lower()
            if suffix in _TEXT_EXTS:
                try:
                    text = data.decode("utf-8")
                    count = text.count(old_school)
                    if count:
                        text = text.replace(old_school, new_school_name)
                        total += count
                    data = text.encode("utf-8")
                except UnicodeDecodeError:
                    pass  # 바이너리 파일은 그대로 복사
            zout.writestr(item, data)

    return old_school, total


def main() -> None:
    args = sys.argv[1:]
    if len(args) != 3:
        print("사용법: py scripts/clone_template.py <source.hwpx> <새학교명> <output.hwpx>")
        sys.exit(1)

    source = Path(args[0])
    new_school = args[1]
    output = Path(args[2])

    if not source.exists():
        print(f"[오류] 파일 없음: {source}")
        sys.exit(1)

    print(f"원본  : {source.name}")
    print(f"새 학교명: {new_school}")

    old_school, count = clone_template(source, new_school, output)

    print(f"교체  : {old_school} → {new_school}  ({count}곳)")
    print(f"저장  : {output}")

    if count == 0:
        print("[경고] XML에서 학교명을 찾지 못했습니다. 파일을 직접 확인하세요.")
    else:
        print("완료. 한글에서 한 번 열어 확인을 권장합니다.")


if __name__ == "__main__":
    main()
