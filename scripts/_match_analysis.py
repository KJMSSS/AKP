import sys, zipfile, re, json
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
from src.common.latex_to_hwp import convert

# 템플릿 슬롯 목록
hwpx = Path('samples/[2025_2_1_a_확통_경신여고][순열 ~ 확률의 뜻과 활용][워드초벌].hwpx')
with zipfile.ZipFile(hwpx) as zf:
    xml = zf.read('Contents/section0.xml').decode('utf-8')
any_re = re.compile(r'<hp:script>(.*?)</hp:script>', re.DOTALL)
slots = any_re.findall(xml)

# OCR 수식 목록
ocr = json.loads(Path('samples/ocr_확통.json').read_text(encoding='utf-8'))
formulas = [f['latex'] for f in ocr['formulas']]

BACKTICK = '`'


def normalize(s):
    s = s.replace(BACKTICK, '').replace(',', ' ')
    s = re.sub(r'\s+', ' ', s).strip().lower()
    return s


# OCR 수식 → HWP Script 변환 후 정규화
ocr_hwp = [(convert(f), normalize(convert(f))) for f in formulas]
slot_norm = [normalize(s) for s in slots]

print('=== OCR 수식 ↔ 템플릿 슬롯 비교 (처음 20개) ===')
for i, (formula, (hwp, nhwp)) in enumerate(zip(formulas[:20], ocr_hwp[:20]), 1):
    best_j, best_score = -1, 0
    for j, ns in enumerate(slot_norm):
        if not ns:
            continue
        if ns == nhwp:
            best_j, best_score = j, 100
            break
        if ns in nhwp or nhwp in ns:
            score = min(len(ns), len(nhwp)) / max(len(ns), len(nhwp), 1) * 100
            if score > best_score:
                best_j, best_score = j, score

    match_info = (f'-> 슬롯[{best_j+1:03d}] ({best_score:.0f}%): {repr(slots[best_j][:40])}'
                  if best_j >= 0 else '-> 매칭 없음')
    print(f'[{i:03d}] LaTeX: {repr(formula[:40])}')
    print(f'      HWP  : {repr(hwp[:40])}')
    print(f'      {match_info}')

# 슬롯 중 OCR에 매칭된 것 / 안된 것 통계
print()
print('=== 슬롯 매칭 가능성 분석 ===')

matched_slots = set()
for nhwp in [n for _, n in ocr_hwp]:
    for j, ns in enumerate(slot_norm):
        if ns and ns == nhwp:
            matched_slots.add(j)
            break

print(f'정확 매칭 가능한 슬롯: {len(matched_slots)}개 / 전체 {len(slots)}개')

# 슬롯 유형 분류
only_number = sum(1 for s in slots if re.fullmatch(r'\d+', s.strip()))
only_var = sum(1 for s in slots if re.fullmatch(r'[a-zA-Z]', s.strip()))
complex_formula = sum(1 for s in slots if len(s.strip()) > 3 and not re.fullmatch(r'\d+', s.strip()))

print(f'순수 숫자 슬롯: {only_number}개')
print(f'단일 변수 슬롯: {only_var}개')
print(f'복합 수식 슬롯: {complex_formula}개')
print()

# 순수 숫자/변수 슬롯 목록 (처음 20개)
print('숫자 단독 슬롯 목록 (처음 20개):')
nums = [(i+1, s) for i, s in enumerate(slots) if re.fullmatch(r'\d+', s.strip())]
for idx, val in nums[:20]:
    print(f'  [{idx:03d}] {repr(val)}')
