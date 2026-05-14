"""조대부고 v1 — '손상' 가설 사실 확인."""
import sys, zipfile, re
sys.stdout.reconfigure(encoding='utf-8')

RAW  = r'd:\f1\AKP\samples\eval_set\(광주)_2024_수1_조대부고\raw_ocr.md'
FILT = r'd:\f1\AKP\samples\eval_set\(광주)_2024_수1_조대부고\filtered_v1.md'
HWPX = r'd:\f1\AKP\samples\eval_set\(광주)_2024_수1_조대부고\output_v1.hwpx'

raw_text  = open(RAW,  encoding='utf-8').read()
filt_text = open(FILT, encoding='utf-8').read()

print('=== 텍스트 안 "nroot" 검색 ===')
print(f'  raw_ocr.md  : {raw_text.lower().count("nroot")}건')
print(f'  filtered_v1.md: {filt_text.lower().count("nroot")}건')

print('\n=== 문제 1 영역 (filtered) ===')
m = re.search(r'^1\.[^\n]*[\s\S]*?(?=^2\.)', filt_text, re.MULTILINE)
if m:
    print(m.group(0))

print('\n=== HWPX 내부 검색 ===')
with zipfile.ZipFile(HWPX) as z:
    sec = z.read('Contents/section0.xml').decode('utf-8', errors='ignore')

# section0.xml의 모든 hp:script 추출
scripts = re.findall(r'<hp:script>([\s\S]*?)</hp:script>', sec)
print(f'  hp:script 객체 수: {len(scripts)}')
print(f'  section0.xml 안 "nroot" 전체: {sec.lower().count("nroot")}건')

# 첫 번째 hp:script 5개 출력
print('\n=== 처음 5개 hp:script 내용 ===')
for i, s in enumerate(scripts[:5], 1):
    # XML 엔티티 디코딩
    decoded = (s.replace('&lt;', '<').replace('&gt;', '>')
                .replace('&amp;', '&').replace('&quot;', '"'))
    print(f'  #{i}: {decoded}')

# nroot가 들어 있는 hp:script 다 출력
print('\n=== nroot 포함 hp:script (전체) ===')
for i, s in enumerate(scripts, 1):
    if 'nroot' in s.lower():
        decoded = (s.replace('&lt;', '<').replace('&gt;', '>')
                    .replace('&amp;', '&').replace('&quot;', '"'))
        print(f'  #{i}: {decoded}')

# 「★」 마커 위치 (filtered)
print('\n=== filtered 안 ★ 마커 위치 ===')
for m in re.finditer(r'(\d+)?\s*\n?(【★[^】]+】)', filt_text):
    line_no = filt_text[:m.start()].count('\n') + 1
    print(f'  L{line_no}: {m.group(0).strip()[:80]}')
