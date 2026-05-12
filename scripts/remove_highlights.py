"""
HWPX 하이라이트 색상 제거

사용법:
    py scripts/remove_highlights.py <입력.hwpx> [출력.hwpx]

출력 경로를 생략하면 입력 파일을 덮어씁니다.

예시:
    py scripts/remove_highlights.py samples/output_확통_v2.hwpx
    py scripts/remove_highlights.py samples/output_확통_v2.hwpx samples/output_확통_final.hwpx
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.hwpx.builder import _extract_zip, _pack_zip
from src.hwpx.pdf_filler import remove_highlights

_SECTION = "Contents/section0.xml"


def main():
    args = sys.argv[1:]
    if not args:
        print("사용법: py scripts/remove_highlights.py <입력.hwpx> [출력.hwpx]")
        sys.exit(1)

    input_path  = Path(args[0])
    output_path = Path(args[1]) if len(args) > 1 else input_path

    if not input_path.exists():
        print(f"[오류] 파일 없음: {input_path}")
        sys.exit(1)

    files = _extract_zip(input_path)
    if _SECTION not in files:
        print(f"[오류] section0.xml 없음: {input_path}")
        sys.exit(1)

    xml = files[_SECTION].decode("utf-8")
    xml_clean = remove_highlights(xml)
    files[_SECTION] = xml_clean.encode("utf-8")
    _pack_zip(output_path, files)

    action = "덮어쓰기" if output_path == input_path else f"→ {output_path}"
    print(f"하이라이트 제거 완료 ({action})")


if __name__ == "__main__":
    main()
