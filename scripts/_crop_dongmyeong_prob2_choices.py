"""동명고 2번 선택지 영역 추가 크롭 + Vision 추출."""
import sys, base64
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

import fitz
import anthropic

DPI    = 300
SCALE  = DPI / 72
PDF    = Path("samples/11b/[2025_1_1_b_공수1_동명고].pdf")
OUT    = Path("log/cycle_16/dongmyeong_crops")

# 2번 선택지 영역: left col, y=2400~2900
doc  = fitz.open(str(PDF))
page = doc[0]
mat  = fitz.Matrix(SCALE, SCALE)
pix_full = page.get_pixmap(matrix=mat)
W, H = pix_full.width, pix_full.height
mid  = W // 2

# 2번 문제 + 선택지 전체 (좀 넉넉하게)
x0, x1 = 0, mid - 20
y0, y1 = 2350, min(H, 3100)
rect = fitz.Rect(x0/SCALE, y0/SCALE, x1/SCALE, y1/SCALE)
pix2 = page.get_pixmap(matrix=mat, clip=rect)
doc.close()

img_path = OUT / "prob_2_ext.png"
pix2.save(str(img_path))
print(f"크롭: {img_path.name}  ({pix2.width}×{pix2.height})")

# Vision 추출
CLIENT = anthropic.Anthropic()
b64 = base64.standard_b64encode(img_path.read_bytes()).decode()
msg = CLIENT.messages.create(
    model="claude-opus-4-7", max_tokens=512,
    messages=[{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
        {"type": "text", "text": "이 이미지에서 인쇄된 선택지(①②③④⑤)만 추출해주세요. 학생 손글씨는 무시. LaTeX 수식으로 출력."},
    ]}],
)
result = msg.content[0].text.strip()
print("\n추출 결과:")
print(result)

out_md = OUT / "ocr" / "prob_2_choices.md"
out_md.write_text(result, encoding="utf-8")
print(f"\n저장: {out_md.name}")
