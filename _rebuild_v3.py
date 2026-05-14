"""필터+보강된 마크다운으로 v3 HWPX 빌드 (Mathpix 재호출 없음)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding='utf-8')

from src.text_only.text_builder import build_from_markdown

ROOT = Path(__file__).resolve().parent
md = (ROOT / 'output_text_temp_filtered.md').read_text(encoding='utf-8')
base = ROOT / 'samples' / '(광주)[2025_1_1_a_공수1_광덕고].hwpx'
out  = ROOT / 'samples' / 'output_text_(광주)[2026_1_1_a_공수1_광주고]_v3.hwpx'

result = build_from_markdown(md, out, base)
print(f'문단 {result["paragraphs"]}개 / 수식 {result["equations"]}개 / {out.stat().st_size:,} bytes')
print(f'출력: {out}')
