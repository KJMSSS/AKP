"""이미지레벨 손풀이 제거(filter_handwriting_pdf) 회귀 테스트 (무네트워크).

method="color"(기본): 유색 펜 픽셀을 채도로 마스킹 — 실데이터(주황 손풀이)에 최적.
method="vision": Claude Vision bbox(모킹) — 무채색 손글씨용.
no-op(원본 반환) 케이스도 검증.
"""
from __future__ import annotations

import io

import numpy as np
from PIL import Image

import src.common.pdf_utils as pu


def _pdf_with_square(path, color) -> None:
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.draw_rect(fitz.Rect(50, 50, 150, 150), color=color, fill=color)
    doc.save(str(path))
    doc.close()


def _center_gray(pdf_path) -> int:
    import fitz
    d = fitz.open(str(pdf_path))
    pix = d[0].get_pixmap(matrix=fitz.Matrix(1, 1))
    arr = np.array(Image.open(io.BytesIO(pix.tobytes("png"))).convert("L"))
    d.close()
    H, W = arr.shape
    return int(arr[H // 2, W // 2])


# ── color 방식 (기본) ─────────────────────────────────────────────
def test_color_masks_colored_handwriting(tmp_path):
    p = tmp_path / "orange.pdf"
    _pdf_with_square(p, (1, 0.5, 0))            # 주황 = 유색 손글씨 가정
    out = pu.filter_handwriting_pdf(p, method="color")
    assert out != p and out.name.endswith("_clean.pdf")
    assert _center_gray(out) > 200              # 주황 영역 → 흰색


def test_color_noop_on_black_only(tmp_path):
    p = tmp_path / "black.pdf"
    _pdf_with_square(p, (0, 0, 0))              # 검정(인쇄) = 유색 아님
    assert pu.filter_handwriting_pdf(p, method="color") == p   # no-op


# ── vision 방식 (모킹) ────────────────────────────────────────────
def test_vision_masks_region(tmp_path, monkeypatch):
    p = tmp_path / "v.pdf"
    _pdf_with_square(p, (0, 0, 0))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(pu, "_vision_handwriting_boxes",
                        lambda png, key: [(25.0, 25.0, 75.0, 75.0)])
    out = pu.filter_handwriting_pdf(p, method="vision")
    assert out.name.endswith("_clean.pdf")
    assert _center_gray(out) > 200


def test_vision_noop_without_key(tmp_path, monkeypatch):
    p = tmp_path / "n.pdf"
    _pdf_with_square(p, (0, 0, 0))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert pu.filter_handwriting_pdf(p, method="vision") == p   # no-op
