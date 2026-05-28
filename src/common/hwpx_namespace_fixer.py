"""
HWPX 네임스페이스 후처리 유틸리티.

차용 출처: D:/f1/exam-studio @ 4a96778
차용 일자: 2026-05-28
변경사항:
  - 모듈로 재패키징 (AKP src/common/ 통합)
  - __main__ CLI 블록 유지

AKP 적용 방침:
  - 파이프라인에서 HWPX 빌드 직후 항상 호출 (의미 변경 없음, 안전)
  - AKP text_builder.py는 이미 hp:/hh:/hc:/hs: 프리픽스 직접 사용
    → 대부분 no-op이지만 방어적으로 호출

동작:
  python-hwpx 등 외부 도구가 생성한 ns0:/ns1: 자동 프리픽스를
  한컴오피스 표준 프리픽스(hh/hc/hp/hs)로 교체.
  이를 적용하지 않으면 한글 Viewer(특히 macOS)에서 빈 페이지로 표시될 수 있음.
"""

import os
import re
import sys
import zipfile

NS_MAP = {
    "http://www.hancom.co.kr/hwpml/2011/head": "hh",
    "http://www.hancom.co.kr/hwpml/2011/core": "hc",
    "http://www.hancom.co.kr/hwpml/2011/paragraph": "hp",
    "http://www.hancom.co.kr/hwpml/2011/section": "hs",
}


def fix_hwpx_namespaces(hwpx_path: str) -> None:
    """
    HWPX 파일의 ns0:/ns1: 자동 생성 프리픽스를 한컴오피스 표준으로 교체.
    표준 프리픽스를 이미 사용 중이면 no-op.
    """
    tmp_path = hwpx_path + ".tmp"

    with zipfile.ZipFile(hwpx_path, "r") as zin:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)

                if item.filename.startswith("Contents/") and item.filename.endswith(".xml"):
                    text = data.decode("utf-8")

                    ns_aliases: dict[str, str] = {}
                    for match in re.finditer(r'xmlns:(ns\d+)="([^"]+)"', text):
                        alias, uri = match.group(1), match.group(2)
                        if uri in NS_MAP:
                            ns_aliases[alias] = NS_MAP[uri]

                    for old_prefix, new_prefix in ns_aliases.items():
                        text = text.replace(f"xmlns:{old_prefix}=", f"xmlns:{new_prefix}=")
                        text = text.replace(f"<{old_prefix}:", f"<{new_prefix}:")
                        text = text.replace(f"</{old_prefix}:", f"</{new_prefix}:")

                    data = text.encode("utf-8")

                zout.writestr(item, data)

    os.replace(tmp_path, hwpx_path)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python hwpx_namespace_fixer.py <file.hwpx>")
        sys.exit(1)
    path = sys.argv[1]
    if not os.path.exists(path):
        print(f"Error: File not found: {path}")
        sys.exit(1)
    fix_hwpx_namespaces(path)
    print(f"Fixed namespaces: {path}")
