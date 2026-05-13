"""HWPX 두 파일 비교 분석 (compare_layout)"""
import sys, zipfile, re
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path

auto = Path('samples/[2024_1_1_a_수상_동성고][워드초벌].hwpx')
gold = Path('samples/[2024_1_1_a_수상_동성고][워드초벌]1.hwpx')

def get_xml(f, entry):
    with zipfile.ZipFile(f) as zf:
        return zf.read(entry).decode('utf-8')

a_sec = get_xml(auto, 'Contents/section0.xml')
g_sec = get_xml(gold, 'Contents/section0.xml')
a_hdr = get_xml(auto, 'Contents/header.xml')
g_hdr = get_xml(gold, 'Contents/header.xml')
a_mp  = get_xml(auto, 'Contents/masterpage0.xml')
g_mp  = get_xml(gold, 'Contents/masterpage0.xml')

# ── 페이지 레이아웃 ───────────────────────────────────────────
print('=== 페이지 레이아웃 ===')
for label, mp in [('AUTO', a_mp), ('GOLD', g_mp)]:
    w = re.search(r'width="(\d+)"', mp)
    h = re.search(r'height="(\d+)"', mp)
    t = re.search(r'top="(\d+)"', mp)
    b = re.search(r'bottom="(\d+)"', mp)
    l = re.search(r'left="(\d+)"', mp)
    r = re.search(r'right="(\d+)"', mp)
    wv = w.group(1) if w else '?'
    hv = h.group(1) if h else '?'
    tv = t.group(1) if t else '?'
    bv = b.group(1) if b else '?'
    lv = l.group(1) if l else '?'
    rv = r.group(1) if r else '?'
    print(f'  [{label}] 용지={wv}x{hv}  여백 top={tv} bottom={bv} left={lv} right={rv}')
print()

# ── 헤더/푸터 ────────────────────────────────────────────────
print('=== 헤더/푸터 ===')
for label, sec in [('AUTO', a_sec), ('GOLD', g_sec)]:
    hh = '<hp:header' in sec
    ff = '<hp:footer' in sec
    print(f'  [{label}] 헤더:{hh}  푸터:{ff}')
print()

# ── 폰트 목록 ────────────────────────────────────────────────
print('=== 폰트 ===')
for label, hdr in [('AUTO', a_hdr), ('GOLD', g_hdr)]:
    fonts = sorted(set(re.findall(r'face="([^"]+)"', hdr)))
    print(f'  [{label}]: {fonts}')
print()

# ── 스타일 수 ────────────────────────────────────────────────
print('=== 스타일 ===')
for label, hdr in [('AUTO', a_hdr), ('GOLD', g_hdr)]:
    nc = len(re.findall(r'<hh:charPr ', hdr))
    np_ = len(re.findall(r'<hh:paraPr ', hdr))
    print(f'  [{label}] charPr={nc}개  paraPr={np_}개')
print()

# ── 그림(pic) 개수 및 이미지 참조 ────────────────────────────
print('=== 그림 ===')
for label, sec in [('AUTO', a_sec), ('GOLD', g_sec)]:
    pics = re.findall(r'<hp:pic\b[^>]*>', sec)
    bins = re.findall(r'href="([^"]+)"', sec)
    print(f'  [{label}] pic태그={len(pics)}  이미지참조={len(bins)} → {sorted(set(bins))[:5]}')
print()

# ── 문항 경계 감지 ────────────────────────────────────────────
print('=== 문항 번호 등장 ===')
for label, sec in [('AUTO', a_sec), ('GOLD', g_sec)]:
    nums = re.findall(r'<hp:t>(\d+)\.</hp:t>', sec)
    print(f'  [{label}]: {nums[:30]}')
print()

# ── 색상 사용 ─────────────────────────────────────────────────
print('=== 색상(foreColor) 종류 ===')
for label, hdr in [('AUTO', a_hdr), ('GOLD', g_hdr)]:
    colors = sorted(set(re.findall(r'foreColor="([^"]+)"', hdr)))
    print(f'  [{label}]: {colors}')
print()

# ── 수식 스크립트 첫 30개 비교 ──────────────────────────────
print('=== AUTO 슬롯 (처음 30개) ===')
sa = re.findall(r'<hp:script>(.*?)</hp:script>', a_sec, re.DOTALL)
for i, s in enumerate(sa[:30]):
    print(f'  [{i+1:3d}] {s.strip()[:60]}')
print()
print('=== GOLD 슬롯 (처음 30개) ===')
sg = re.findall(r'<hp:script>(.*?)</hp:script>', g_sec, re.DOTALL)
for i, s in enumerate(sg[:30]):
    print(f'  [{i+1:3d}] {s.strip()[:60]}')
