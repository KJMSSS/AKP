"""
LLM 선택지 정규화 — 객관식 문제의 （1）~（5） 레이블 복원.

normalize_choices(segments) → list[ProblemSegment]

22개 객관식 선택지 블록을 한 번의 LLM 호출로 처리.
비용: ~$0.01/학교 (Sonnet 4.6 기준)
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from src.text_only.problem_segmenter import ProblemSegment

load_dotenv()

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 4096

_INPUT_COST  = 3.0  / 1_000_000
_OUTPUT_COST = 15.0 / 1_000_000

_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "log" / "cycle_16" / "choices"

_SYSTEM = """당신은 수학 시험지 선택지 정규화 전문가입니다.

입력: 각 객관식 문제의 선택지 후보 줄 (OCR 오류로 번호 누락/오인식 가능)
출력: 각 문제별 정확히 5개 선택지, 번호 정규화

출력 형식 (문제당 정확히 6줄):
문제N:
（1） 내용
（2） 내용
（3） 내용
（4） 내용
（5） 내용

규칙:
- 반드시 각 문제당 （1）~（5） 정확히 5줄 출력
- 수식 $...$, $$...$$ 형식 그대로 보존
- 번호 없는 줄은 순서로 추정
- 내용 불명확하면 [미상] 사용
- 선택지가 LaTeX array로 합쳐진 경우 분리
- 설명 없이 결과만 출력"""


def normalize_choices(
    segments: list[ProblemSegment],
    log_stem: str = "",
) -> list[ProblemSegment]:
    """
    객관식 문제의 선택지를 LLM으로 （1）~（5） 정규화.
    한 번의 API 호출로 전체 문제 처리.
    반환: 업데이트된 segments (choices 필드 교체)
    """
    obj_segs = [s for s in segments if not s.is_subjective]
    if not obj_segs:
        return segments

    # LLM 입력 블록 생성
    input_parts: list[str] = []
    for seg in obj_segs:
        num = seg.number
        if seg.choices:
            lines_str = " / ".join(seg.choices)
        else:
            # 선택지 없으면 문제 텍스트 마지막 20줄에서 후보 추출
            tail = "\n".join(seg.problem_text.splitlines()[-20:])
            lines_str = tail.replace("\n", " / ")
        input_parts.append(f"문제{num}: {lines_str}")

    user_prompt = (
        "아래 객관식 문제들의 선택지를 （1）~（5）로 정규화하세요.\n"
        "각 문제당 정확히 6줄 (문제N: + 5개 선택지) 출력.\n\n"
        + "\n".join(input_parts)
    )

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    t0 = time.time()
    response = client.messages.create(
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user_prompt}],
        temperature=0,
    )
    elapsed = time.time() - t0
    reply = response.content[0].text.strip()

    cost = (response.usage.input_tokens * _INPUT_COST
            + response.usage.output_tokens * _OUTPUT_COST)

    # 로깅
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    log_path = _LOG_DIR / f"{log_stem}_{ts}.json"
    import json
    log_path.write_text(json.dumps({
        "cost_usd": round(cost, 6),
        "elapsed_s": round(elapsed, 2),
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "input": "\n".join(input_parts),
        "output": reply,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"  [choices] LLM 완료: ${cost:.4f}  {elapsed:.1f}s")

    # 파싱: "문제N:" 블록 추출
    parsed = _parse_reply(reply)
    updated = 0
    for seg in segments:
        if seg.is_subjective:
            continue
        num = seg.number
        if num in parsed:
            new_choices = parsed[num]
            if len(new_choices) == 5:
                seg.choices = new_choices
                updated += 1

    print(f"  [choices] 정규화 적용: {updated}/{len(obj_segs)}개 문제")
    return segments


def _parse_reply(reply: str) -> dict[int, list[str]]:
    """LLM 출력에서 문제번호 → 선택지 5개 파싱."""
    result: dict[int, list[str]] = {}
    current_num: int | None = None
    current_choices: list[str] = []

    header_re = re.compile(r"^문제(\d+)\s*:")
    choice_re = re.compile(r"^[（(]\s*([1-5])\s*[）)]\s*(.*)")

    for line in reply.splitlines():
        s = line.strip()
        if not s:
            continue

        m = header_re.match(s)
        if m:
            # 이전 문제 저장
            if current_num is not None and current_choices:
                result[current_num] = current_choices
            current_num = int(m.group(1))
            current_choices = []
            continue

        m = choice_re.match(s)
        if m and current_num is not None:
            label_n = int(m.group(1))
            content = m.group(2).strip()
            choice_str = f"（{label_n}） {content}"
            current_choices.append(choice_str)
            continue

    # 마지막 문제 저장
    if current_num is not None and current_choices:
        result[current_num] = current_choices

    return result
