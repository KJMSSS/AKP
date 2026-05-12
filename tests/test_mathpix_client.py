import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.ocr.mathpix_client import (
    MathpixClient,
    MathpixError,
    OcrBlock,
    OcrResult,
    _parse_text,
)

_FIXTURE = Path("samples/last_response.json")


# ── 픽스처 ──────────────────────────────────────────────────────

@pytest.fixture
def real_response() -> dict:
    if not _FIXTURE.exists():
        pytest.skip(f"{_FIXTURE} 없음 — samples/ 폴더에 파일 필요")
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


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


# ── _parse_text ──────────────────────────────────────────────────

def test_parse_text_pure_text():
    blocks = _parse_text("안녕하세요. 수식 없음.")
    assert blocks == [OcrBlock(kind="text", content="안녕하세요. 수식 없음.")]


def test_parse_text_inline_only():
    blocks = _parse_text(r"\( x^2 + 1 \)")
    assert blocks == [OcrBlock(kind="formula_inline", content=r"x^2 + 1")]


def test_parse_text_display_only():
    blocks = _parse_text(r"\[ \int_0^1 f(x)\,dx \]")
    assert blocks == [OcrBlock(kind="formula_display", content=r"\int_0^1 f(x)\,dx")]


def test_parse_text_mixed_inline():
    raw = r"이차함수 \( y=-2x^2 \) 의 그래프"
    blocks = _parse_text(raw)
    assert len(blocks) == 3
    assert blocks[0] == OcrBlock(kind="text",           content="이차함수")
    assert blocks[1] == OcrBlock(kind="formula_inline", content="y=-2x^2")
    assert blocks[2] == OcrBlock(kind="text",           content="의 그래프")


def test_parse_text_skips_empty_segments():
    # 수식만 있어도 앞뒤 빈 문자열은 블록 안 만들어야 함
    blocks = _parse_text(r"  \( x \)  ")
    assert len(blocks) == 1
    assert blocks[0].kind == "formula_inline"


def test_parse_text_multiple_inline():
    raw = r"\( a \) 와 \( b \) 의 합"
    blocks = _parse_text(raw)
    kinds = [b.kind for b in blocks]
    assert kinds == ["formula_inline", "text", "formula_inline", "text"]


# ── OcrResult.from_response ──────────────────────────────────────

def test_from_response_metadata():
    data = {
        "text": "x",
        "confidence": 0.95,
        "image_width": 800,
        "image_height": 600,
        "is_printed": True,
        "is_handwritten": False,
        "data": [{"type": "latex", "value": "x"}],
        "mmd": "x",
    }
    r = OcrResult.from_response(data)
    assert r.confidence    == pytest.approx(0.95)
    assert r.image_width   == 800
    assert r.image_height  == 600
    assert r.is_printed    is True
    assert r.is_handwritten is False
    assert len(r.raw_data) == 1
    assert r.mmd           == "x"


def test_from_response_raw_data_list():
    data = {"text": "", "data": [{"type": "latex", "value": "x^2"}]}
    r = OcrResult.from_response(data)
    assert r.raw_data == [{"type": "latex", "value": "x^2"}]


def test_from_response_raw_data_non_list_becomes_empty():
    # 과거 코드에서 dict를 반환한다고 가정했던 경우 방어
    data = {"text": "", "data": {"unexpected": "dict"}}
    r = OcrResult.from_response(data)
    assert r.raw_data == []


def test_from_response_empty_response():
    r = OcrResult.from_response({})
    assert r.blocks        == []
    assert r.confidence    == 0.0
    assert r.raw_data      == []


# ── 실제 last_response.json 픽스처 기반 테스트 ─────────────────

def test_real_fixture_metadata(real_response):
    r = OcrResult.from_response(real_response)
    assert r.confidence    == pytest.approx(0.8594818115234375)
    assert r.image_width   == 1070
    assert r.image_height  == 400
    assert r.is_printed    is True
    assert r.is_handwritten is False


def test_real_fixture_raw_data_is_list(real_response):
    r = OcrResult.from_response(real_response)
    assert isinstance(r.raw_data, list)
    assert all(d.get("type") == "latex" for d in r.raw_data)


def test_real_fixture_blocks_not_empty(real_response):
    r = OcrResult.from_response(real_response)
    assert len(r.blocks) > 0


def test_real_fixture_has_text_and_inline(real_response):
    r = OcrResult.from_response(real_response)
    kinds = {b.kind for b in r.blocks}
    assert "text"           in kinds
    assert "formula_inline" in kinds


def test_real_fixture_first_block_is_text(real_response):
    r = OcrResult.from_response(real_response)
    assert r.blocks[0].kind == "text"
    assert "이차함수" in r.blocks[0].content


def test_real_fixture_inline_latex_values(real_response):
    r = OcrResult.from_response(real_response)
    formulas = [b.content for b in r.blocks if b.kind == "formula_inline"]
    assert any("x^{2}" in f for f in formulas), f"수식 목록: {formulas}"


# ── ocr_image HTTP 목킹 ──────────────────────────────────────────

def _mock_response(data: dict, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = data
    resp.text = str(data)
    return resp


def test_ocr_image_returns_ocr_result(tmp_path):
    img = tmp_path / "page.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)

    payload = {
        "text": r"수식 \( x^2 \) 포함",
        "confidence": 0.99,
        "image_width": 100,
        "image_height": 50,
        "data": [],
    }
    with patch.dict(os.environ, {"MATHPIX_APP_ID": "id", "MATHPIX_APP_KEY": "key"}):
        client = MathpixClient()
    with patch("src.ocr.mathpix_client.httpx.Client") as M:
        M.return_value.__enter__.return_value.post.return_value = _mock_response(payload)
        result = client.ocr_image(img)

    assert isinstance(result, OcrResult)
    assert result.confidence   == pytest.approx(0.99)
    assert result.image_width  == 100
    assert any(b.kind == "formula_inline" for b in result.blocks)


def test_ocr_image_retries_on_429(tmp_path):
    img = tmp_path / "page.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)

    rate_limited = _mock_response({}, status=429)
    ok           = _mock_response({"text": "ok", "data": [], "confidence": 1.0})

    with patch.dict(os.environ, {"MATHPIX_APP_ID": "id", "MATHPIX_APP_KEY": "key"}):
        client = MathpixClient()
    with patch("src.ocr.mathpix_client.httpx.Client") as M:
        with patch("src.ocr.mathpix_client.time.sleep"):
            M.return_value.__enter__.return_value.post.side_effect = [rate_limited, ok]
            result = client.ocr_image(img, retries=3)

    assert result.raw_text == "ok"
