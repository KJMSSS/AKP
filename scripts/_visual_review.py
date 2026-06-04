"""
LLM 시각 검수 — 크롭 PNG vs raw.md 본문 비교.

각 문제 PNG 이미지를 Claude Vision으로 보내서 raw.md에서 추출한
문제 본문이 이미지의 내용을 완전히 담고 있는지 확인.

사용법:
  python scripts/_visual_review.py 동신여고
  python scripts/_visual_review.py 동신여고 --only 3 7 12   # 특정 문제만
"""
from __future__ import annotations

import base64
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")

import anthropic
from src.text_only.problem_segmenter import parse_problems

ROOT     = Path(__file__).resolve().parent.parent
CROP_ROOT = ROOT / "log" / "cycle_16" / "crops"

_MODEL = "claude-sonnet-4-6"
_INPUT_COST  = 3.0  / 1_000_000
_OUTPUT_COST = 15.0 / 1_000_000

_PROMPT = """\
이 이미지는 수학 시험지 {num}번 문제 크롭입니다.
아래 [추출 텍스트]가 이미지의 문제 본문을 완전히 담고 있는지 확인하세요.
수식 표기 방식 차이(LaTeX vs 한글 수식)는 무시하고, **내용의 완전성**만 판단하세요.

[추출 텍스트]
{text}

규칙:
- OK: 내용이 완전히 포함됨 (사소한 표기 차이 무시)
- WARNING: 일부 누락 또는 오타 (간단히 설명)
- ERROR: 핵심 내용 누락 또는 심각한 오류

딱 한 줄로만 답하세요:
OK
또는
WARNING: (누락/오류 내용)
또는
ERROR: (누락/오류 내용)"""


def _encode_image(path: Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode()


def review_school(school: str, only: list[int] | None = None) -> list[dict]:
    crop_dir = CROP_ROOT / school
    raw_path = crop_dir / "raw.md"

    if not raw_path.exists():
        print(f"[오류] raw.md 없음: {raw_path}")
        return []

    md_raw = raw_path.read_text(encoding="utf-8")
    _, segments = parse_problems(md_raw)

    obj_segs = [s for s in segments if not s.is_subjective]
    if only:
        obj_segs = [s for s in obj_segs if s.number in only]

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    results = []
    total_cost = 0.0

    print(f"\n{'='*55}")
    print(f"[{school}] 시각 검수 — {len(obj_segs)}개 문제")
    print(f"{'='*55}")

    for seg in obj_segs:
        num = seg.number
        img_path = crop_dir / f"prob_{num}.png"

        if not img_path.exists():
            print(f"  {num:>2}번: [이미지 없음]")
            results.append({"num": num, "status": "SKIP", "detail": "이미지 없음"})
            continue

        problem_text = seg.problem_text.strip()

        try:
            response = client.messages.create(
                model=_MODEL,
                max_tokens=200,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": _encode_image(img_path),
                            },
                        },
                        {
                            "type": "text",
                            "text": _PROMPT.format(num=num, text=problem_text),
                        },
                    ],
                }],
            )
            answer = response.content[0].text.strip().splitlines()[0]
            cost = (response.usage.input_tokens * _INPUT_COST
                    + response.usage.output_tokens * _OUTPUT_COST)
            total_cost += cost

            if answer.startswith("OK"):
                status, detail = "OK", ""
                marker = "✓"
            elif answer.startswith("WARNING"):
                status = "WARNING"
                detail = answer[len("WARNING:"):].strip()
                marker = "⚠"
            else:
                status = "ERROR"
                detail = answer[len("ERROR:"):].strip()
                marker = "✗"

            msg = f"  {num:>2}번: {marker} {status}"
            if detail:
                msg += f" — {detail}"
            print(msg)

            results.append({"num": num, "status": status, "detail": detail})

        except Exception as e:
            print(f"  {num:>2}번: [API 오류] {e}")
            results.append({"num": num, "status": "ERROR", "detail": str(e)})

        time.sleep(0.3)  # rate limit 여유

    # 요약
    ok  = sum(1 for r in results if r["status"] == "OK")
    wrn = sum(1 for r in results if r["status"] == "WARNING")
    err = sum(1 for r in results if r["status"] == "ERROR")

    print(f"\n{'─'*55}")
    print(f"결과: OK {ok}개 / WARNING {wrn}개 / ERROR {err}개")
    print(f"비용: ${total_cost:.4f}")

    if wrn or err:
        print("\n[요주의 문제]")
        for r in results:
            if r["status"] in ("WARNING", "ERROR"):
                print(f"  {r['num']}번 ({r['status']}): {r['detail']}")

    return results


def main():
    args = sys.argv[1:]
    if not args:
        print("사용법: python scripts/_visual_review.py <학교명> [--only N N ...]")
        sys.exit(1)

    school = args[0]
    only: list[int] | None = None

    if "--only" in args:
        idx = args.index("--only")
        only = [int(x) for x in args[idx + 1:]]

    review_school(school, only=only)


if __name__ == "__main__":
    main()
