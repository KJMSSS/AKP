"""PDF 마크다운 전체 출력 + 토큰 패턴 분석."""
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path

# 저장된 pdf_id로 마크다운 재취득
from dotenv import load_dotenv; load_dotenv()
from src.common.ocr.mathpix_client import MathpixClient

client = MathpixClient()
pdf_id = '0202d1c2-9ca9-4d2f-8f51-ae117bc15d8c'
md = client.fetch_pdf_markdown(pdf_id)

print(f'=== 마크다운 전체 ({len(md)}자) ===')
print(md)
print()
print('=== 답지 패턴 탐색 ===')
# (1) 42 형식
choices_paren = re.findall(r'\((\d)\)\s*([^\n\(]+)', md)
print(f'(N) 형식 답지: {len(choices_paren)}개')
for num, val in choices_paren[:20]:
    print(f'  ({num}) {val.strip()[:30]}')

print()
# 원문자 ①②③④⑤
choices_circle = re.findall(r'[①②③④⑤]\s*(\S+)', md)
print(f'원문자 답지: {len(choices_circle)}개')
for v in choices_circle[:10]:
    print(f'  {v}')
