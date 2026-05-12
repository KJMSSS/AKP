import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.ocr.mathpix_client import (
    MathpixClient,
    MathpixError,
    OcrBlock,
    OcrResult,
    _parse_line,
)


# ── MathpixClient 초기화 ────────────────────────────────────────

def test_init_raises_when_credentials_missing():
    with patch.dict(os.environ, {"MATHPIX_APP_ID": "", "MATHPIX_APP_KEY": ""}):
        with pytest.raises(MathpixError, match="MATHPIX_APP_ID"):
            MathpixClient()


def test_init_success():
    with patch.dict(os.environ, {"MATHPIX_APP_ID": "id", "MATHPIX_APP_KEY": "key"}):
        client = MathpixClient()
    assert client.app_id == "id"
    assert client.app_key == "key"


# ── OcrResult 파싱 ──────────────────────────────────────────────

def test_ocr_result_parses_lines():
    data = {
        "text": "ignored when lines exist",
        "data": {
            "lines": [
                {"type": "text", "text": "1번 문제"},
                {"type": "math", "latex": r"\int_0^1 f(x)\,dx"},
                {"type": "table", "html": "<table></table>"},
            ]
        },
    }
    result = OcrResult.from_response(data)
    assert len(result.blocks) == 3
    assert result.blocks[0] == OcrBlock(kind="text", content="1번 문제")
    assert result.blocks[1] == OcrBlock(kind="formula_display", content=r"\int_0^1 f(x)\,dx")
    assert result.blocks[2] == OcrBlock(kind="table", content="<table></table>")


def test_ocr_result_falls_back_to_text():
    data = {"text": "수식 없는 텍스트"}
    result = OcrResult.from_response(data)
    assert len(result.blocks) == 1
    assert result.blocks[0].kind == "text"


def test_ocr_result_empty_response():
    result = OcrResult.from_response({})
    assert result.blocks == []


# ── _parse_line 헬퍼 ────────────────────────────────────────────

def test_parse_line_skips_empty_math():
    assert _parse_line({"type": "math", "latex": ""}) == []


def test_parse_line_skips_empty_text():
    assert _parse_line({"type": "text", "text": "   "}) == []


# ── ocr_image (HTTP 목킹) ───────────────────────────────────────

def _fake_response(data: dict, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = data
    resp.text = str(data)
    return resp


def test_ocr_image_calls_api_and_returns_result(tmp_path):
    img = tmp_path / "page.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)  # 최소한의 PNG 헤더

    api_response = {"text": "2x + 3 = 7", "data": {}}

    with patch.dict(os.environ, {"MATHPIX_APP_ID": "id", "MATHPIX_APP_KEY": "key"}):
        client = MathpixClient()

    with patch("src.ocr.mathpix_client.httpx.Client") as MockClient:
        mock_ctx = MockClient.return_value.__enter__.return_value
        mock_ctx.post.return_value = _fake_response(api_response)
        result = client.ocr_image(img)

    assert isinstance(result, OcrResult)
    assert result.raw == api_response


def test_ocr_image_retries_on_rate_limit(tmp_path):
    img = tmp_path / "page.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)

    rate_limited = _fake_response({}, status=429)
    ok_response = _fake_response({"text": "ok", "data": {}})

    with patch.dict(os.environ, {"MATHPIX_APP_ID": "id", "MATHPIX_APP_KEY": "key"}):
        client = MathpixClient()

    with patch("src.ocr.mathpix_client.httpx.Client") as MockClient:
        with patch("src.ocr.mathpix_client.time.sleep"):
            mock_ctx = MockClient.return_value.__enter__.return_value
            mock_ctx.post.side_effect = [rate_limited, ok_response]
            result = client.ocr_image(img, retries=3)

    assert result.raw["text"] == "ok"
