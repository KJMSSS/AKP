"""마크다운 vs HWPX XML 마커 카운트 진단."""
import sys, zipfile, re
sys.stdout.reconfigure(encoding='utf-8')

MD   = r'd:\f1\AKP\samples\eval_set\(광주)_2026_공수1\filtered_v3.md'
HWPX = r'd:\f1\AKP\samples\eval_set\(광주)_2026_공수1\output_v3.hwpx'

# ── 마크다운 ──
md_text = open(MD, encoding='utf-8').read()
md_block = md_text.count('본문 손상')
md_star  = md_text.count('【★')
first_block_line = next(
    (ln for ln in md_text.split('\n') if '본문 손상' in ln),
    '(없음)'
)
print('[markdown]')
print(f'  "본문 손상": {md_block}건')
print(f'  "【★"     : {md_star}건')
print(f'  첫 "본문 손상" 줄: {first_block_line!r}')

# ── HWPX 내부 XML ──
print('\n[HWPX XML]')
totals = {'본문 손상': 0, '【': 0, '★': 0, '】': 0, '확인 필요': 0}
per_file: dict[str, dict[str, int]] = {}
with zipfile.ZipFile(HWPX) as z:
    for name in z.namelist():
        if not (name.endswith('.xml') or name.endswith('.xhtml')):
            continue
        try:
            data = z.read(name).decode('utf-8', errors='ignore')
        except Exception:
            continue
        per = {}
        for p in totals:
            n = data.count(p)
            if n:
                per[p] = n
                totals[p] += n
        if per:
            per_file[name] = per
for p, n in totals.items():
    print(f'  "{p}": {n}건')
print('  per-file:')
for name, per in per_file.items():
    print(f'    {name}: {per}')
