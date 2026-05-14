"""HWPX 내부 XML들을 풀어서 마커 패턴 검색."""
import sys, zipfile
sys.stdout.reconfigure(encoding='utf-8')

PATH = r'd:\f1\AKP\samples\output_text_(광주)[2026_1_1_a_공수1_광주고]_v3.hwpx'

PATTERNS = ['【★', '본문 손상', '확인 필요']
hits = {p: 0 for p in PATTERNS}
files_with_hit: dict[str, list[str]] = {p: [] for p in PATTERNS}

with zipfile.ZipFile(PATH) as z:
    for name in z.namelist():
        try:
            data = z.read(name).decode('utf-8', errors='ignore')
        except Exception:
            continue
        for p in PATTERNS:
            n = data.count(p)
            if n:
                hits[p] += n
                files_with_hit[p].append(f"{name}({n})")

for p in PATTERNS:
    print(f'"{p}": {hits[p]}건  in {files_with_hit[p] or "(없음)"}')
