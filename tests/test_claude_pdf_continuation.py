"""claude_pdf_reader max_tokens 절단 감지·이어쓰기 회귀 테스트.

실사고(수완고): 8192 한도에서 "[서술형] 1."까지 출력 후 조용히 절단 →
서술형 문제 전부 유실. stop_reason 확인 + assistant 프리필 이어쓰기 +
한도 초과 시 명시적 실패를 보장한다.
"""
from __future__ import annotations

import pytest

import src.ocr.claude_pdf_reader as reader


class _Usage:
    input_tokens = 1000
    output_tokens = 500


class _Block:
    type = "text"

    def __init__(self, text: str):
        self.text = text


class _Resp:
    def __init__(self, text: str, stop_reason: str):
        self.content = [_Block(text)]
        self.stop_reason = stop_reason
        self.usage = _Usage()


class _Stream:
    def __init__(self, resp: _Resp):
        self._resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._resp


class _FakeClient:
    """messages.stream 호출을 기록하고 준비된 응답을 차례로 반환."""

    def __init__(self, responses: list[_Resp]):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = self

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return _Stream(self._responses.pop(0))


@pytest.fixture()
def fake_client(monkeypatch):
    holder: dict = {}

    def _factory(responses):
        client = _FakeClient(responses)
        monkeypatch.setattr(reader.anthropic, "Anthropic", lambda api_key=None: client)
        holder["client"] = client
        return client

    return _factory


def test_single_round_end_turn(fake_client):
    client = fake_client([_Resp("1. 문제 전체", "end_turn")])
    text, cost = reader._call_api(b"%PDF", "key", 32000, "sys", "prompt")
    assert text == "1. 문제 전체"
    assert len(client.calls) == 1
    assert cost > 0


def test_truncated_then_continued(fake_client):
    client = fake_client([
        _Resp("1. 전반부 내용\n[서술형] 1. ", "max_tokens"),
        _Resp("적분 값을 구하시오. [6점]", "end_turn"),
    ])
    text, _ = reader._call_api(b"%PDF", "key", 32000, "sys", "prompt")
    # 프리필은 rstrip 후 이어붙음
    assert text == "1. 전반부 내용\n[서술형] 1.적분 값을 구하시오. [6점]"

    # 2번째 호출에 assistant 프리필이 들어가고, 공백으로 끝나지 않아야 함
    second = client.calls[1]["messages"]
    assert second[-1]["role"] == "assistant"
    assert not second[-1]["content"].endswith((" ", "\n"))


def test_still_truncated_raises(fake_client):
    n_calls = reader._MAX_CONTINUE + 1
    fake_client([_Resp(f"부분{i}", "max_tokens") for i in range(n_calls)])
    with pytest.raises(RuntimeError, match="잘렸습니다"):
        reader._call_api(b"%PDF", "key", 32000, "sys", "prompt")
