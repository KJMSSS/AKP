"""조대부고 검증 수치."""
import sys, re
sys.stdout.reconfigure(encoding='utf-8')

filt = open(r'd:\f1\AKP\samples\eval_set\(광주)_2024_수1_조대부고\filtered_v1.md', encoding='utf-8').read()
raw  = open(r'd:\f1\AKP\samples\eval_set\(광주)_2024_수1_조대부고\raw_ocr.md',  encoding='utf-8').read()

INLINE  = re.compile(r'(?<!\$)\$(?!\$)([^\$\n]+?)\$(?!\$)')
DISPLAY = re.compile(r'\$\$([\s\S]+?)\$\$')
KOREAN  = re.compile('[가-힣]')

inl = sum(1 for m in INLINE.finditer(filt) if KOREAN.search(m.group(1)))
dis = sum(1 for m in DISPLAY.finditer(filt) if KOREAN.search(m.group(1)))

# 문제 수: 선택형(1. ~ N.) + 서술형([서술형N] / 서습형문항 N 등)
sel_nums = set(int(m.group(1)) for m in re.finditer(r'^(\d{1,2})[.．]', filt, re.MULTILINE))
essay_nums = set()
for m in re.finditer(r'\[서[술습슴].{0,2}[형헝](\d+)\]', filt):
    essay_nums.add(int(m.group(1)))
for m in re.finditer(r'^#{0,3}\s*서.{0,1}[형헝]\s*(?:문.{0,1}\s*)?(\d+)', filt, re.MULTILINE):
    essay_nums.add(int(m.group(1)))

# 페이지 수: cdn URL의 마지막 -NN.jpg
pages = set()
for m in re.finditer(r'-(\d+)\.jpg', raw):
    pages.add(int(m.group(1)))

print(f'페이지 수      : {max(pages) if pages else "?"}')
print(f'선택형 문제 수 : {len(sel_nums)} (최대 번호: {max(sel_nums) if sel_nums else "-"})')
print(f'서술형 문제 수 : {len(essay_nums)} (최대 번호: {max(essay_nums) if essay_nums else "-"})')
print(f'한글 잔재      : inline {inl} + display {dis}')
print(f'【★】 마커 총  : {filt.count("【★")}')
print(f'  블록         : {filt.count("【★ 본문 손상")}')
print(f'  인라인       : {filt.count("【★ 확인 필요】")}')
