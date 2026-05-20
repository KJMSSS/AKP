"""동아여고 page1 bbox 재감지 (개선된 프롬프트)."""
import sys, json, base64
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

import anthropic

CLIENT = anthropic.Anthropic()
MODEL  = "claude-opus-4-7"

PROMPT = """\
이 이미지는 2컬럼 레이아웃의 수학 시험지 페이지입니다.

중요 규칙:
1. 상단 헤더(시험 제목, 학교명, 지시사항, 저작권 문구, 이름/반 기입란)는 완전히 무시하세요.
2. 학생 손글씨 풀이, 빨간 동그라미, 필기는 완전히 무시하세요.
3. 인쇄된 아라비아 숫자로 시작하는 문제(예: "1.", "2.", "3.")만 찾으세요.

이 페이지에는 문제 번호 1번~5번이 있습니다:
- 왼쪽 컬럼: 1번, 2번
- 오른쪽 컬럼: 3번, 4번, 5번

각 문제의 y_start(문제 번호 줄의 y픽셀)와 y_end(다음 문제 시작 직전 또는 컬럼 끝)를 정확히 추정해주세요.
이미지 높이는 4298픽셀입니다.

반드시 JSON 배열 형식으로만 응답하세요 (다른 텍스트 없이):
[
  {"num": "1", "column": "left",  "y_start": ..., "y_end": ...},
  {"num": "2", "column": "left",  "y_start": ..., "y_end": ...},
  {"num": "3", "column": "right", "y_start": ..., "y_end": ...},
  {"num": "4", "column": "right", "y_start": ..., "y_end": ...},
  {"num": "5", "column": "right", "y_start": ..., "y_end": ...}
]"""

full_png = Path("log/cycle_16/dongah_crops/page1_full.png").read_bytes()
b64 = base64.standard_b64encode(full_png).decode()

msg = CLIENT.messages.create(
    model=MODEL,
    max_tokens=1024,
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

print(raw)
data = json.loads(raw)
out = Path("log/cycle_16/dongah_crops/bbox_v2.json")
out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n저장: {out}")
