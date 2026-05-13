"""HWPX XML 구조 분석 — 슬롯 주변 텍스트 컨텍스트 파악."""
import sys, zipfile, re
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
import xml.etree.ElementTree as ET

hwpx = Path('samples/[2025_2_1_a_확통_경신여고][순열 ~ 확률의 뜻과 활용][워드초벌].hwpx')
with zipfile.ZipFile(hwpx) as zf:
    raw = zf.read('Contents/section0.xml').decode('utf-8')

# namespace 맵
NS = {'hp': 'http://www.hancom.co.kr/hwpml/2011/paragraph'}

# hp:script 슬롯 각각에 대해 주변 텍스트 context 추출 (raw XML 기반)
# 슬롯 위치를 찾고, 앞 300자 내 텍스트 추출

script_re = re.compile(r'<hp:script>(.*?)</hp:script>', re.DOTALL)
text_re = re.compile(r'<hp:t[^>]*>([^<]+)</hp:t>')

positions = [(m.start(), m.end(), m.group(1)) for m in script_re.finditer(raw)]

print(f'총 {len(positions)}개 슬롯')
print()
print('=== 처음 20개 슬롯 + 주변 텍스트(앞 200자) ===')
for i, (start, end, content) in enumerate(positions[:20], 1):
    # 슬롯 이전 500자에서 텍스트 추출
    before = raw[max(0, start-500):start]
    texts = text_re.findall(before)
    # 숫자로 시작하는 문항 번호 찾기 (1. 2. 등)
    nearby_text = ' '.join(texts[-5:])  # 바로 앞 5개 텍스트

    print(f'[{i:03d}] content={repr(content[:30]):<35}  context={repr(nearby_text[-60:])}')

print()
print('=== 슬롯별 문항 번호 감지 ===')
# 각 슬롯 앞 2000자에서 가장 가까운 문항 번호 찾기
problem_num_re = re.compile(r'(?:^|\n|>)(\d{1,2})\.\s', re.MULTILINE)

for i, (start, end, content) in enumerate(positions[:40], 1):
    before = raw[max(0, start-3000):start]
    # 가장 마지막 문항 번호 찾기
    nums = problem_num_re.findall(before)
    last_num = nums[-1] if nums else '?'

    print(f'[{i:03d}] 문항={last_num:>2}  content={repr(content[:25])}')
