"""두 section0.xml의 첫 단락 + header.xml 비교."""
import sys, re
sys.stdout.reconfigure(encoding='utf-8')

x1 = open(r'd:\f1\AKP\temp_v3\Contents\section0.xml', encoding='utf-8').read()
x2 = open(r'd:\f1\AKP\temp_v3_after\Contents\section0.xml', encoding='utf-8').read()
h1 = open(r'd:\f1\AKP\temp_v3\Contents\header.xml', encoding='utf-8').read()
h2 = open(r'd:\f1\AKP\temp_v3_after\Contents\header.xml', encoding='utf-8').read()

# 첫 hp:p 단락 추출
def first_p(xml):
    m = re.search(r'<hp:p[\s>][\s\S]*?</hp:p>', xml)
    return m.group(0) if m else ''

p1 = first_p(x1)
p2 = first_p(x2)
print(f'[첫 단락 길이] before {len(p1)} / after {len(p2)}')
print('\n--- before 첫 단락 ---')
print(p1[:1800])
print('\n--- after  첫 단락 ---')
print(p2[:1800])

# header.xml의 어떤 정의들이 빠졌는지 (한글이 정리)
print('\n[header.xml 태그 카운트]')
HEADER_TAGS = [
    'hh:fontface', 'hh:font', 'hh:borderFill', 'hh:charPr',
    'hh:paraPr', 'hh:style', 'hh:numbering', 'hh:bullet',
    'hh:tabPr', 'hh:bgColor', 'hh:beginNum', 'hh:refList',
]
for t in HEADER_TAGS:
    a = len(re.findall(rf'<{re.escape(t)}(?=[\s/>])', h1))
    b = len(re.findall(rf'<{re.escape(t)}(?=[\s/>])', h2))
    if a or b:
        print(f'  {t:20s}: {a:4d} → {b:4d}   (Δ {b-a:+d})')

# 빌더가 만든 hp:lineseg 속성값 vs 한글이 만든 lineseg 속성값 비교
def first_lineseg(xml):
    m = re.search(r'<hp:lineseg [^/>]+/?>', xml)
    return m.group(0) if m else ''
print('\n[hp:lineseg 첫 항목]')
print(f'  before: {first_lineseg(x1)}')
print(f'  after : {first_lineseg(x2)}')

# 단락에서 hp:run/hp:t 분포 비교
print('\n[첫 단락의 hp:run / hp:t 개수]')
print(f'  before: hp:run={len(re.findall(r"<hp:run[\\s>]", p1))}, hp:t={len(re.findall(r"<hp:t[\\s>/]", p1))}')
print(f'  after : hp:run={len(re.findall(r"<hp:run[\\s>]", p2))}, hp:t={len(re.findall(r"<hp:t[\\s>/]", p2))}')
