"""HWPX 슬롯 구조 심층 비교 (실제 slot_analyzer 사용)"""
import sys, zipfile, re, hashlib
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))
from pathlib import Path
from src.hwpx.slot_analyzer import analyze_slots, build_slot_map

auto = Path('samples/[2024_1_1_a_수상_동성고][워드초벌].hwpx')
gold = Path('samples/[2024_1_1_a_수상_동성고][워드초벌]1.hwpx')

def get_xml(f, entry):
    with zipfile.ZipFile(f) as zf:
        return zf.read(entry).decode('utf-8')

a_sec = get_xml(auto, 'Contents/section0.xml')
g_sec = get_xml(gold, 'Contents/section0.xml')

ga = analyze_slots(a_sec)
gg = analyze_slots(g_sec)

ma = build_slot_map(ga)
mg = build_slot_map(gg)

all_probs = sorted(set(list(ma.keys()) + list(mg.keys())))

# ── 비교 1: 문항별 슬롯 ────────────────────────────────────────
print('=== 문항별 슬롯 개수 ===')
print(f'  {"문항":>4}  {"본문A":>5}  {"답지A":>5}  {"합A":>5}  │  {"본문G":>5}  {"답지G":>5}  {"합G":>5}  {"차이":>5}')
print('  ' + '-'*62)
for n in all_probs:
    a = ma.get(n)
    g = mg.get(n)
    ac = len(a.content_slots) if a else 0
    aa = len(a.answer_slots) if a else 0
    at = ac + aa
    gc = len(g.content_slots) if g else 0
    ga_ = len(g.answer_slots) if g else 0
    gt = gc + ga_
    diff = gt - at
    flag = '  ★' if diff != 0 else ''
    print(f'  {n:4d}번  {ac:5d}  {aa:5d}  {at:5d}  │  {gc:5d}  {ga_:5d}  {gt:5d}  {diff:+5d}{flag}')
print('  ' + '-'*62)
a_tot = sum(g.total() for g in ga)
g_tot = sum(g.total() for g in gg)
a_con = sum(len(g.content_slots) for g in ga)
a_ans = sum(len(g.answer_slots) for g in ga)
g_con = sum(len(g.content_slots) for g in gg)
g_ans = sum(len(g.answer_slots) for g in gg)
print(f'  {"합계":>4}  {a_con:5d}  {a_ans:5d}  {a_tot:5d}  │  {g_con:5d}  {g_ans:5d}  {g_tot:5d}  {g_tot-a_tot:+5d}')
print()
print(f'  AUTO 총 슬롯: {a_tot}개  (인식된 문항: {len(ga)}개 중 문항번호 확인={len(all_probs)}개)')
print(f'  GOLD 총 슬롯: {g_tot}개')
all_a_scripts = [s.content for g in ga for s in g.all_slots]
all_g_scripts = [s.content for g in gg for s in g.all_slots]
a_raw = re.findall(r'<hp:script>(.*?)</hp:script>', a_sec, re.DOTALL)
g_raw = re.findall(r'<hp:script>(.*?)</hp:script>', g_sec, re.DOTALL)
print(f'  AUTO raw hp:script: {len(a_raw)}개  (문항 귀속: {a_tot}개, 미귀속: {len(a_raw)-a_tot}개)')
print(f'  GOLD raw hp:script: {len(g_raw)}개  (문항 귀속: {g_tot}개, 미귀속: {len(g_raw)-g_tot}개)')
print()

# ── 비교 2: 슬롯 내용 샘플 (처음 10문항) ─────────────────────
print('=== 문항별 슬롯 내용 상세 (공통 문항 비교) ===')
for n in all_probs[:15]:
    a = ma.get(n)
    g = mg.get(n)
    if not a or not g:
        status = 'AUTO만' if a else 'GOLD만'
        print(f'  [{n:2d}번] {status}에만 존재')
        continue
    print(f'  [{n:2d}번]  본문: AUTO={len(a.content_slots)} GOLD={len(g.content_slots)}  │  답지: AUTO={len(a.answer_slots)} GOLD={len(g.answer_slots)}')
    # content slots 최대 3개
    max_show = max(len(a.content_slots), len(g.content_slots))
    for i in range(min(3, max_show)):
        a_s = a.content_slots[i].content.strip()[:40] if i < len(a.content_slots) else '(없음)'
        g_s = g.content_slots[i].content.strip()[:40] if i < len(g.content_slots) else '(없음)'
        same = '=' if a_s == g_s else '≠'
        print(f'      본문[{i+1}] {same}  AUTO: {a_s!r}')
        print(f'              GOLD: {g_s!r}')
print()

# ── 비교 3: 슬롯 포맷 차이 ────────────────────────────────────
print('=== 슬롯 포맷 유형 분포 ===')
def classify(s):
    s = s.strip()
    if re.fullmatch(r'-?\d+', s): return 'N 순수숫자'
    if re.fullmatch(r'[a-zA-Zα-ωΑ-Ω]', s): return 'V 단일변수'
    if '`' in s: return 'F 백틱수식(구형)'
    if re.search(r'\{|\}|\^|_|\\| over | left | right ', s): return 'E HWP내장수식'
    return 'T 텍스트/혼합'

for label, scripts in [('AUTO', a_raw), ('GOLD', g_raw)]:
    counts = {}
    for s in scripts:
        k = classify(s)
        counts[k] = counts.get(k, 0) + 1
    total = len(scripts)
    print(f'  [{label}] 총 {total}개:')
    for k in sorted(counts):
        pct = counts[k]/total*100
        bar = '█' * int(pct/3)
        print(f'     {k:20s}: {counts[k]:3d}개 ({pct:5.1f}%) {bar}')
print()

# ── 비교 4: 이미지 비교 ─────────────────────────────────────────
print('=== 이미지 파일 비교 ===')
with zipfile.ZipFile(auto) as za, zipfile.ZipFile(gold) as zg:
    a_imgs = {}
    g_imgs = {}
    for n in za.namelist():
        if n.startswith('BinData/'):
            data = za.read(n)
            a_imgs[n] = (len(data), hashlib.md5(data).hexdigest()[:8])
    for n in zg.namelist():
        if n.startswith('BinData/'):
            data = zg.read(n)
            g_imgs[n] = (len(data), hashlib.md5(data).hexdigest()[:8])

all_imgs = sorted(set(list(a_imgs.keys()) + list(g_imgs.keys())))
for n in all_imgs:
    a_info = a_imgs.get(n)
    g_info = g_imgs.get(n)
    if a_info and g_info:
        same = '✓ 동일' if a_info[1] == g_info[1] else '✗ 다름'
        print(f'  {n}: AUTO={a_info[0]:,}B  GOLD={g_info[0]:,}B  {same}')
    elif a_info:
        print(f'  {n}: AUTO={a_info[0]:,}B  GOLD=없음  ← AUTO에만 존재')
    else:
        print(f'  {n}: AUTO=없음  GOLD={g_info[0]:,}B  ← GOLD에만 존재')
print()

# ── 비교 5: 학교명 검증 ────────────────────────────────────────
print('=== 학교명 잔재 전수조사 ===')
for label, sec in [('AUTO', a_sec), ('GOLD', g_sec)]:
    for school in ['동성고', '인성고', '광주고', '문성고']:
        positions = []
        start = 0
        while True:
            idx = sec.find(school, start)
            if idx == -1: break
            ctx = sec[max(0,idx-30):idx+len(school)+30].replace('\n', ' ')
            positions.append(f'…{ctx}…')
            start = idx + 1
        cnt = len(positions)
        print(f'  [{label}] "{school}": {cnt}곳')
        for p in positions[:3]:
            print(f'    {p}')
