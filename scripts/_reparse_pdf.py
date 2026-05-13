import sys, json
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv; load_dotenv()
from pathlib import Path
from src.common.ocr.mathpix_client import MathpixClient
from src.common.latex_to_hwp import convert as latex_to_hwp

client = MathpixClient()
pdf_id = '0202d1c2-9ca9-4d2f-8f51-ae117bc15d8c'
result = client.fetch_pdf_to_result(pdf_id, source='확통_경신여고.pdf')

formula_blocks = [b for b in result.blocks if b.kind in ('formula_inline', 'formula_display')]
text_blocks    = [b for b in result.blocks if b.kind == 'text']
print(f'전체 블록: {len(result.blocks)}')
print(f'수식 블록: {len(formula_blocks)}')
print(f'텍스트 블록: {len(text_blocks)}')
print()
print('=== 처음 30개 수식 ===')
for i, b in enumerate(formula_blocks[:30], 1):
    hwp = latex_to_hwp(b.content)
    pad = ' ' * 16
    print(f'[{i:03d}] {b.kind:<16} LaTeX: {repr(b.content[:50])}')
    print(f'      {pad} HWP  : {hwp[:50]}')

# JSON 저장
payload = {
    'pdf_id': pdf_id,
    'formula_count': len(formula_blocks),
    'slot_count': 181,
    'formulas': [
        {'index': i, 'kind': b.kind, 'latex': b.content, 'hwp': latex_to_hwp(b.content)}
        for i, b in enumerate(formula_blocks, 1)
    ]
}
Path('samples/ocr_확통.json').write_text(
    json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8'
)
print(f'\n저장: samples/ocr_확통.json ({len(formula_blocks)}개 수식)')
