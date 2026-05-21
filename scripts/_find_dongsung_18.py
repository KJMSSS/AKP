"""동성고 18번 위치 찾기 — page 4~6 시도."""
import sys, base64, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")

import fitz
import anthropic

PDF = Path("samples/11b/[2025_1_1_b_공수1_동성고].pdf")
OUT = Path("log/cycle_16/dongsung_crops")
CLIENT = anthropic.Anthropic()

DETECT = (
    "이 이미지는 수학 시험지 페이지입니다.\n"
    "찾을 문제: 18번\n"
    "이미지 크기: {img_w}x{img_h}px\n\n"
    '규칙: 인쇄된 아라비아 숫자 "18." 또는 "18．"로 시작하는 문제만 찾으세요.\n\n'
    "JSON만 출력:\n"
    '{{"problems": [{{"number": 18, "y_top": 픽셀, "y_bottom": 픽셀, "col": "left" 또는 "right"}}]}}\n\n'
    "18번이 없으면: {{\"problems\": []}}"
)

doc = fitz.open(str(PDF))
print(f"총 {doc.page_count}페이지")

for pg_idx in [3, 4, 5]:
    if pg_idx >= doc.page_count:
        break
    page = doc[pg_idx]
    mat = fitz.Matrix(100/72, 100/72)
    pix = page.get_pixmap(matrix=mat)
    thumb_path = OUT / f"pg{pg_idx+1}_thumb.png"
    pix.save(str(thumb_path))

    b64 = base64.standard_b64encode(pix.tobytes("png")).decode()
    msg = CLIENT.messages.create(
        model="claude-opus-4-7", max_tokens=256,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            {"type": "text", "text": DETECT.format(img_w=pix.width, img_h=pix.height)},
        ]}]
    )
    raw = msg.content[0].text.strip()
    print(f"page{pg_idx+1}: {raw[:300]}")

    try:
        start = raw.find('{')
        end   = raw.rfind('}') + 1
        result = json.loads(raw[start:end])
        if result.get("problems"):
            p = result["problems"][0]
            print(f"  *** 18번 발견: page{pg_idx+1}, col={p['col']}, y={p['y_top']}~{p['y_bottom']}")
    except Exception as e:
        print(f"  [parse error] {e}")

doc.close()
