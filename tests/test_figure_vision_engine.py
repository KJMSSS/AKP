"""Google Vision 그림 엔진 통합 회귀 테스트 (무네트워크).

- `_figure_bbox_from_text_boxes`: 텍스트 마스킹 공용 로직(엔진 무관) 정확성.
- `_text_bbox`: FIGURE_TEXT_ENGINE/자격증명에 따른 엔진 선택·폴백.
Vision API는 호출하지 않는다(자격증명/라이브러리 없을 때 tesseract 폴백 확인).
"""
from __future__ import annotations

import numpy as np

import src.common.image_extractor as ie
from src.common.image_extractor import _figure_bbox_from_text_boxes, _text_bbox


def test_text_masking_isolates_figure():
    arr = np.full((120, 120), 255, dtype=np.uint8)
    arr[10:25, 10:110] = 0     # 텍스트 줄 (마스킹돼야)
    arr[60:90, 50:80] = 0      # 그림 (남아야)
    bbox = _figure_bbox_from_text_boxes(arr, [(10, 10, 100, 15)], pad_px=4)
    assert bbox is not None
    x0, y0, x1, y1 = bbox
    assert x0 <= 50 and y0 <= 60 and x1 >= 80 and y1 >= 90   # 그림 포함
    assert y0 >= 60 - 15 - 1                                  # 텍스트는 제외(마스킹)


def test_no_text_boxes_returns_none():
    arr = np.full((50, 50), 255, dtype=np.uint8)
    assert _figure_bbox_from_text_boxes(arr, [], pad_px=4) is None


def test_engine_force_tesseract(tmp_path, monkeypatch):
    monkeypatch.setenv("FIGURE_TEXT_ENGINE", "tesseract")
    monkeypatch.setattr(ie, "_tesseract_bbox", lambda *a, **k: (1, 2, 3, 4))
    bbox, eng = _text_bbox(tmp_path / "x.png")
    assert eng == "tesseract" and bbox == (1, 2, 3, 4)


def test_engine_auto_uses_google_when_creds(tmp_path, monkeypatch):
    monkeypatch.setenv("FIGURE_TEXT_ENGINE", "auto")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path / "key.json"))
    monkeypatch.setattr(ie, "_google_vision_bbox", lambda *a, **k: (5, 6, 7, 8))
    bbox, eng = _text_bbox(tmp_path / "x.png")
    assert eng == "google" and bbox == (5, 6, 7, 8)


def test_engine_auto_falls_back_without_creds(tmp_path, monkeypatch):
    monkeypatch.setenv("FIGURE_TEXT_ENGINE", "auto")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr(ie, "_tesseract_bbox", lambda *a, **k: None)
    _, eng = _text_bbox(tmp_path / "x.png")
    assert eng == "tesseract"
