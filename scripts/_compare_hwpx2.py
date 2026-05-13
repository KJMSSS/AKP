"""HWPX 슬롯 구조 심층 비교"""
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

# ── 문항별 슬롯 개수 추출 ──────────────────────────────────────
# slot_analyzer와 동일한 로직: hp:equation 블록을 문항 번호로 구분
def extract_problem_slots(xml):
    """문항별 (problem_num → [script_content]) 딕셔너리."""
    # 수식 객체의 그룹을 추적하기 위해 XML을 세그먼트로 분할
    # 문항 번호 마커: 숫자 + . 패턴의 단독 텍스트 런
    # 단순 접근: XML을 순차 파싱하며 문항 번호 전환 감지

    # hp:t 텍스트 및 hp:script 추출 순서
    tokens = re.findall(
        r'<hp:t>([^<]*)</hp:t>|<hp:script>(.*?)</hp:script>',
        xml, re.DOTALL
    )

    current_problem = 0
    problem_slots = {}  # int -> list of scripts

    for text_match, script_match in tokens:
        if text_match:
            stripped = text_match.strip()
            # 문항 번호 패턴: "1." "2." ... "22."
            m = re.fullmatch(r'(\d+)\.', stripped)
            if m:
                num = int(m.group(1))
                if 1 <= num <= 30:
                    current_problem = num
                    if current_problem not in problem_slots:
                        problem_slots[current_problem] = []
        if script_match and current_problem > 0:
            if current_problem not in problem_slots:
                problem_slots[current_problem] = []
            problem_slots[current_problem].append(script_match.strip())

    return problem_slots

pa = extract_problem_slots(a_sec)
pg = extract_problem_slots(g_sec)

all_problems = sorted(set(list(pa.keys()) + list(pg.keys())))

print('=== 문항별 슬롯 개수 비교 ===')
print(f'  {"문항":>4}  {"AUTO":>6}  {"GOLD":>6}  {"차이":>6}')
print('  ' + '-'*32)
for n in all_problems:
    a_cnt = len(pa.get(n, []))
    g_cnt = len(pg.get(n, []))
    diff = g_cnt - a_cnt
    flag = '  ★' if diff != 0 else ''
    print(f'  {n:4d}번  {a_cnt:6d}  {g_cnt:6d}  {diff:+6d}{flag}')
print('  ' + '-'*32)
a_total = sum(len(v) for v in pa.values())
g_total = sum(len(v) for v in pg.values())
print(f'  {"합계":>4}  {a_total:6d}  {g_total:6d}  {g_total-a_total:+6d}')
print()

# ── 슬롯 유형 분류 상세 ────────────────────────────────────────
def classify(s):
    s = s.strip()
    if re.fullmatch(r'\d+', s): return 'N (숫자)'
    if re.fullmatch(r'[a-zA-Zα-ωΑ-Ωαβγδεζηθικλμνξπρστυφχψω]', s): return 'V (단일변수)'
    if '`' in s: return 'F (수식+백틱)'
    if re.search(r'[\\{}^_]', s): return 'E (HWP수식)'
    return 'T (텍스트/복합)'

print('=== 슬롯 유형 분류 ===')
for label, sec in [('AUTO', a_sec), ('GOLD', g_sec)]:
    scripts = re.findall(r'<hp:script>(.*?)</hp:script>', sec, re.DOTALL)
    counts = {}
    for s in scripts:
        k = classify(s)
        counts[k] = counts.get(k, 0) + 1
    total = len(scripts)
    print(f'  [{label}] 총 {total}개:')
    for k in sorted(counts):
        pct = counts[k]/total*100
        print(f'     {k}: {counts[k]}개 ({pct:.1f}%)')
print()

# ── 슬롯 내용 유사도: 공통 슬롯 스크립트 비율 ────────────────
print('=== 슬롯 내용 유사도 ===')
sa_set = set(s.strip() for s in re.findall(r'<hp:script>(.*?)</hp:script>', a_sec, re.DOTALL))
sg_set = set(s.strip() for s in re.findall(r'<hp:script>(.*?)</hp:script>', g_sec, re.DOTALL))
common = sa_set & sg_set
print(f'  AUTO 고유 슬롯 내용: {len(sa_set)}종')
print(f'  GOLD 고유 슬롯 내용: {len(sg_set)}종')
print(f'  공통 슬롯 내용: {len(common)}종')
print(f'  공통 항목: {sorted(common)[:20]}')
print()

# ── masterpage 비교 ────────────────────────────────────────────
print('=== masterpage 전문 비교 ===')
a_mp = get_xml(auto, 'Contents/masterpage0.xml')
g_mp = get_xml(gold, 'Contents/masterpage0.xml')
print(f'  AUTO masterpage: {len(a_mp):,}자')
print(f'  GOLD masterpage: {len(g_mp):,}자')
print(f'  동일 여부: {a_mp == g_mp}')
if a_mp != g_mp:
    # 첫 번째 차이 위치
    for i, (ca, cg) in enumerate(zip(a_mp, g_mp)):
        if ca != cg:
            print(f'  첫 차이 위치: index {i}')
            print(f'    AUTO: …{a_mp[max(0,i-30):i+50]}…')
            print(f'    GOLD: …{g_mp[max(0,i-30):i+50]}…')
            break
print()

# ── 이미지 파일 해시 비교 ─────────────────────────────────────
import hashlib
print('=== 이미지 파일 비교 ===')
with zipfile.ZipFile(auto) as za, zipfile.ZipFile(gold) as zg:
    a_bins = {n: hashlib.md5(za.read(n)).hexdigest()[:8] for n in za.namelist() if n.startswith('BinData/')}
    g_bins = {n: hashlib.md5(zg.read(n)).hexdigest()[:8] for n in zg.namelist() if n.startswith('BinData/')}

all_bins = sorted(set(list(a_bins.keys()) + list(g_bins.keys())))
for n in all_bins:
    a_h = a_bins.get(n, 'MISSING')
    g_h = g_bins.get(n, 'MISSING')
    same = '✓ 동일' if a_h == g_h else '✗ 다름'
    print(f'  {n}: AUTO={a_h}  GOLD={g_h}  {same}')
