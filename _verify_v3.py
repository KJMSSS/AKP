"""v3 검증 — 한글 잔재, ★ 마커, 본문 손상 교체 케이스 카운트."""
import re
import sys
sys.stdout.reconfigure(encoding='utf-8')

md = open('d:/f1/AKP/output_text_temp_filtered.md', encoding='utf-8').read()

INLINE  = re.compile(r'(?<!\$)\$(?!\$)([^\$\n]+?)\$(?!\$)')
DISPLAY = re.compile(r'\$\$([\s\S]+?)\$\$')
KOREAN  = re.compile('[가-힣]')

inline_hits  = [m.group(0) for m in INLINE.finditer(md) if KOREAN.search(m.group(1))]
display_hits = [m.group(0)[:60] for m in DISPLAY.finditer(md) if KOREAN.search(m.group(1))]

marker_total    = md.count('【★')
inline_marker   = md.count('【★ 확인 필요】')
block_total     = md.count('【★ 본문 손상')

print(f'한글 잔재 (inline $..$):   {len(inline_hits)}건')
print(f'한글 잔재 (display $$..$$): {len(display_hits)}건')
print(f'【★】 마커 (전체):          {marker_total}건')
print(f'  ├ 인라인 (확인 필요):    {inline_marker}건')
print(f'  └ 블록 (본문 손상):     {block_total}건')
