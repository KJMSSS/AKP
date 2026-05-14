"""두 HWPX section0.xml 비교. 빌더 누락 패턴 진단."""
import sys, re, difflib
from collections import Counter
sys.stdout.reconfigure(encoding='utf-8')

P1 = r'd:\f1\AKP\temp_v3\Contents\section0.xml'
P2 = r'd:\f1\AKP\temp_v3_after\Contents\section0.xml'

x1 = open(P1, encoding='utf-8').read()
x2 = open(P2, encoding='utf-8').read()

print(f'before: {len(x1):,} chars')
print(f'after : {len(x2):,} chars  (+{len(x2)-len(x1):,})')

# ── 태그 카운트 비교 ──
TAGS = [
    'hp:p', 'hp:run', 'hp:t', 'hp:equation', 'hp:script',
    'hp:lineBreak', 'hp:tab', 'hp:linesegarray', 'hp:lineseg',
    'hp:ctrl', 'hp:colPr', 'hp:autoNum', 'hp:charPr',
    'hp:secPr', 'hp:rect', 'hp:pic', 'hp:tbl',
    'hp:fwSpace', 'hp:nbSpace', 'hp:markpenBegin', 'hp:markpenEnd',
    'hp:fieldBegin', 'hp:fieldEnd',
]

def count_tag(xml, tag):
    return len(re.findall(rf'<{re.escape(tag)}(?=[\s/>])', xml))

print('\n[태그별 카운트 (before → after)]')
for tag in TAGS:
    a, b = count_tag(x1, tag), count_tag(x2, tag)
    if a != b:
        print(f'  {tag:25s}: {a:5d} → {b:5d}   (Δ {b-a:+d})')
    elif a > 0:
        print(f'  {tag:25s}: {a:5d}              (동일)')

# ── 속성 추가/변경 살피기 ──
def attr_pairs(xml):
    return Counter(re.findall(r'(\w+:?\w*)="[^"]*"', xml))

a1 = attr_pairs(x1)
a2 = attr_pairs(x2)
print('\n[속성 키 카운트 (Δ 가 큰 것만 상위 15)]')
all_keys = set(a1) | set(a2)
diffs = sorted(all_keys, key=lambda k: abs(a2.get(k,0) - a1.get(k,0)), reverse=True)
for k in diffs[:15]:
    d = a2.get(k,0) - a1.get(k,0)
    if d != 0:
        print(f'  {k:30s}: {a1.get(k,0):5d} → {a2.get(k,0):5d}   (Δ {d:+d})')

# ── 네임스페이스 선언 차이 ──
NS = re.compile(r'xmlns:[\w]+="[^"]+"')
ns1 = sorted(set(NS.findall(x1)))
ns2 = sorted(set(NS.findall(x2)))
print('\n[네임스페이스 (before에 없고 after에만 있음)]')
for n in ns2:
    if n not in ns1:
        print(f'  + {n}')
print('[네임스페이스 (after에 없고 before에만 있음)]')
for n in ns1:
    if n not in ns2:
        print(f'  - {n}')

# ── linesegarray / charPr 등 자동 정규화 후보 시그니처 ──
print('\n[자동 정규화 후보 — after에만 등장한 태그/속성 시그니처]')
signatures = [
    'hp:linesegarray', 'hp:lineseg', 'hp:secPr', 'hp:colPr',
    'baseLine=', 'spaceLetter=', 'fontFamily=', 'pageBreak=',
    'hp:autoNum', 'hp:fieldBegin',
]
for sig in signatures:
    a = x1.count(sig)
    b = x2.count(sig)
    if b > 0 and (b > a):
        # 첫 등장 위치 컨텍스트
        idx = x2.find(sig)
        ctx = x2[max(0,idx-40):idx+80].replace('\n',' ')
        print(f'  "{sig}" : {a} → {b}')
        print(f'    예: …{ctx}…')

# ── hp:equation 주변 first occurrence 컨텍스트 ──
print('\n[hp:equation 주변 — 첫 등장 200자 (before / after)]')
def first_ctx(xml, target='<hp:equation'):
    i = xml.find(target)
    if i < 0:
        return '(없음)'
    return xml[i:i+220].replace('\n', ' ')
print(f'  before: {first_ctx(x1)}')
print(f'  after : {first_ctx(x2)}')
