"""
Claude API 클라이언트 (단순 텍스트 입출력)
"""
import os

import anthropic
from dotenv import load_dotenv

load_dotenv()

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 4096


def call_claude(system: str, user: str) -> str:
    """Claude에게 system 프롬프트와 user 메시지를 보내고 텍스트 응답을 반환."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text
