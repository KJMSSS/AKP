"""동명고 page 1 bbox 감지 + 크롭."""
import sys, json, base64
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

import fitz
import anthropic

DPI    = 300
SCALE  = DPI / 72
CLIENT = anthropic.Anthropic()
MODEL  = "claude-opus-4-7"

PDF    = Path("samples/11b/[2025_1_1_b_공수1_동명고].pdf")
OUT    = Path("log/cycle_16/dongmyeong_crops")
OUT.mkdir(parents=True, exist_ok=True)

# ── 1. 렌더링 ─────────────────────────────────────────────────────────────────
doc  = fitz.open(str(PDF))
page = doc[0]
mat  = fitz.Matrix(SCALE, SCALE)
pix  = page.get_pixmap(matrix=mat)
W, H = pix.width, pix.height
png_bytes = pix.tobytes("png")
doc.close()

full_png = OUT / "page1_full.png"
full_png.write_bytes(png_bytes)
print(f"렌더링: {W}×{H}px  →  {full_png.name}")

# ── 2. Vision bbox 감지 ───────────────────────────────────────────────────────
b64 = base64.standard_b64encode(png_bytes).decode()

PROMPT = f"""\
이 이미지는 2컬럼 레이아웃의 수학 시험지 페이지입니다.

중요 규칙:
1. 상단 헤더(시험 제목, 학교명, 지시사항, 저작권 문구, 이름/반 기입란)는 완전히 무시하세요.
2. 학생 손글씨 풀이, 필기는 완전히 무시하세요.
3. 인쇄된 아라비아 숫자로 시작하는 문제(예: "1.", "2.", "3.")만 찾으세요.

이 페이지의 인쇄된 문제 번호를 모두 찾아주세요 (몇 번까지인지 직접 확인).
각 문제의 y_start(문제 번호 줄의 y픽셀)와 y_end(다음 문제 시작 직전)를 추정해주세요.
이미지 높이: {H}픽셀.
2컬럼 레이아웃이면 column을 "left" 또는 "right"로 표기하세요.

반드시 JSON 배열 형식으로만 응답하세요:
[
  {{"num": "1", "column": "left",  "y_start": ..., "y_end": ...}},
  ...
]"""

print("Vision bbox 감지 중...")
msg = CLIENT.messages.create(
    model=MODEL, max_tokens=1024,
    messages=[{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
        {"type": "text", "text": PROMPT},
    ]}],
)
raw = msg.content[0].text.strip()
if "```" in raw:
    raw = raw.split("```")[1]
    if raw.startswith("json"):
        raw = raw[4:]

data = json.loads(raw)
bbox_path = OUT / "bbox_v1.json"
bbox_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"감지된 문제: {[p['num'] for p in data]}")
print(f"저장: {bbox_path.name}")

# ── 3. 크롭 ──────────────────────────────────────────────────────────────────
mid   = W // 2
col_x = {"left": (0, mid - 20), "right": (mid + 20, W)}

doc  = fitz.open(str(PDF))
page = doc[0]
for p in data:
    num = p["num"]
    col = p.get("column", "left")
    y0  = max(0, int(p["y_start"]) - 20)
    y1  = min(H, int(p["y_end"])   + 20)
    x0, x1 = col_x.get(col, (0, W))

    rect = fitz.Rect(x0/SCALE, y0/SCALE, x1/SCALE, y1/SCALE)
    pix2 = page.get_pixmap(matrix=mat, clip=rect)
    out  = OUT / f"prob_{num.replace(' ', '_')}.png"
    pix2.save(str(out))
    print(f"  {num}번: col={col} y={y0}~{y1} → {out.name} ({pix2.width}×{pix2.height})")
doc.close()
print("\n완료.")
