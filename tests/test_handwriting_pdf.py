"""이미지레벨 손풀이 제거(filter_handwriting_pdf) 회귀 테스트 (무네트워크).

Vision 호출을 모킹해 (1) 손글씨로 지목된 영역이 흰색 마스킹되는지,
(2) 자격증명 없음/손글씨 없음 시 원본 그대로 반환(no-op)하는지 검증.
"""
from __future__ import annotations

import io

import numpy as np
from PIL import Image

import src.common.pdf_utils as pu


def _make_pdf_with_black_square(path) -> None:
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.draw_rect(fitz.Rect(50, 50, 150, 150), color=(0, 0, 0), fill=(0, 0, 0))
    doc.save(str(path))
    doc.close()


def test_masks_handwriting_region(tmp_path, monkeypatch):
    import fitz
    p = tmp_path / "hw.pdf"
    _make_pdf_with_black_square(p)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    # Vision이 중앙 사각형(25~75%)을 손글씨로 반환하도록 모킹
    monkeypatch.setattr(pu, "_vision_handwriting_boxes",
                        lambda png, key: [(25.0, 25.0, 75.0, 75.0)])

    out = pu.filter_handwriting_pdf(p)
    assert out != p and out.name.endswith("_clean.pdf")

    d = fitz.open(str(out))
    pix = d[0].get_pixmap(matrix=fitz.Matrix(1, 1))
    arr = np.array(Image.open(io.BytesIO(pix.tobytes("png"))).convert("L"))
    d.close()
    H, W = arr.shape
    assert arr[H // 2, W // 2] > 200, f"마스킹 중앙이 흰색 아님: {arr[H//2, W//2]}"


def test_noop_without_api_key(tmp_path, monkeypatch):
    import fitz
    p = tmp_path / "x.pdf"
    doc = fitz.open(); doc.new_page(); doc.save(str(p)); doc.close()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert pu.filter_handwriting_pdf(p) == p   # 원본 그대로


def test_noop_when_no_handwriting(tmp_path, monkeypatch):
    import fitz
    p = tmp_path / "y.pdf"
    doc = fitz.open(); doc.new_page(); doc.save(str(p)); doc.close()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(pu, "_vision_handwriting_boxes", lambda png, key: [])
    assert pu.filter_handwriting_pdf(p) == p   # 손글씨 없음 → 원본
