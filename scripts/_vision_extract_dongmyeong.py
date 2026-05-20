"""동명고 크롭 이미지에서 Claude vision으로 인쇄된 문제 텍스트 추출."""
import sys, base64
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

import anthropic

CLIENT = anthropic.Anthropic()
MODEL  = "claude-opus-4-7"

CROPS  = Path("log/cycle_16/dongmyeong_crops")

EXTRACT_PROMPT = """\
이 이미지는 수학 시험지의 일부입니다.
학생 손글씨/필기는 완전히 무시하고, 인쇄된 문제 텍스트만 LaTeX 수식($...$)으로 추출해주세요.

출력 형식:
- 문제 번호와 본문
- 선택지 (①②③④⑤)
- 점수 표기 유지 (예: [3.5점] 또는 (5.1점))

인쇄된 텍스트만, 다른 설명 없이 그대로 출력하세요."""


def extract(img_path: Path, label: str) -> str:
    b64 = base64.standard_b64encode(img_path.read_bytes()).decode()
    msg = CLIENT.messages.create(
        model=MODEL, max_tokens=1024,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            {"type": "text", "text": EXTRACT_PROMPT},
        ]}],
    )
    return msg.content[0].text.strip()


for i in [1, 2, 3, 4]:
    img = CROPS / f"prob_{i}.png"
    print(f"\n{'='*50}")
    print(f"[{i}번 이미지]")
    print('='*50)
    result = extract(img, f"{i}번")
    print(result)
    out = CROPS / "ocr" / f"vision_{i}.md"
    out.write_text(result, encoding="utf-8")
