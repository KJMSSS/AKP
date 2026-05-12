"""
.hwpx 파일을 _unpacked/<stem>/ 에 풀고 XML을 들여쓰기로 정리한다.
사용법:  python -m src.hwpx.unpacker samples/sample.hwpx
"""
import sys
import zipfile
import xml.dom.minidom
from pathlib import Path


def unpack(hwpx_path: Path, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or (Path("_unpacked") / hwpx_path.stem)
    out_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(hwpx_path, "r") as zf:
        for name in zf.namelist():
            raw = zf.read(name)
            target = out_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)

            if name.lower().endswith((".xml", ".hml")):
                try:
                    pretty = xml.dom.minidom.parseString(raw).toprettyxml(
                        indent="  ", encoding="utf-8"
                    )
                    target.write_bytes(pretty)
                except Exception:
                    target.write_bytes(raw)
            else:
                target.write_bytes(raw)

    print(f"\n[unpacked] {hwpx_path.name}  →  {out_dir}\n")
    _tree(out_dir)
    return out_dir


def _tree(path: Path, prefix: str = "") -> None:
    items = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
    for i, item in enumerate(items):
        last = i == len(items) - 1
        print(f"{prefix}{'└── ' if last else '├── '}{item.name}")
        if item.is_dir():
            _tree(item, prefix + ("    " if last else "│   "))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m src.hwpx.unpacker <file.hwpx>")
        sys.exit(1)
    unpack(Path(sys.argv[1]))
