"""snap_rect_bbox: VLM 눈대중 bbox → 인쇄 테두리 정밀 스냅 (2026-07-19 용봉중 실사고).

합성 이미지로 결정적 검증 — 실사고 재현 조건:
- bbox 가 상자를 위로 통째로 빗나감(문항5) / 마지막 줄을 자름(문항6)
- 상자 테두리에 연필 낙서가 붙어 컨투어 boundingRect 가 늘어남
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from pipeline.figure import snap_rect_bbox  # noqa: E402

W, H = 1200, 1600
RECT = (150, 500, 950, 720)  # x0,y0,x1,y1 (px)


def _page(tmp_path) -> str:
    img = np.full((H, W), 255, np.uint8)
    x0, y0, x1, y1 = RECT
    cv2.rectangle(img, (x0, y0), (x1, y1), 0, 3)
    # 상자 안 '수식' 노이즈(글자 덩어리)
    for i in range(6):
        cv2.putText(img, "x2-9x+14=0", (x0 + 60 + i * 8, y0 + 60 + i * 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, 80, 2)
    # 테두리 아래 붙은 '연필 낙서'(경계 확장 유발 — 실사고 재현)
    cv2.line(img, (x0 + 300, y1), (x0 + 380, y1 + 90), 120, 3)
    cv2.putText(img, "-2  3x-4", (x0 + 250, y1 + 70), cv2.FONT_HERSHEY_SIMPLEX, 0.8, 120, 2)
    # 상자 위 본문 텍스트 줄
    cv2.putText(img, "problem text line", (x0, y0 - 60), cv2.FONT_HERSHEY_SIMPLEX, 0.9, 60, 2)
    p = str(tmp_path / "page.png")
    cv2.imwrite(p, img)
    return p


def _assert_close(bbox):
    x0, y0, x1, y1 = RECT
    assert bbox is not None
    assert abs(bbox[0] * W - x0) < 12 and abs(bbox[1] * H - y0) < 12
    assert abs(bbox[2] * W - x1) < 12 and abs(bbox[3] * H - y1) < 12


def test_snap_recovers_from_upward_miss(tmp_path):
    # 문항5 재현: bbox 가 상자 위 본문 영역을 가리킴
    page = _page(tmp_path)
    off = [140 / W, 380 / H, 900 / W, 560 / H]
    _assert_close(snap_rect_bbox(page, off))


def test_snap_recovers_cut_last_line(tmp_path):
    # 문항6 재현: bbox 하단이 상자 마지막 줄을 자름
    page = _page(tmp_path)
    cut = [160 / W, 505 / H, 940 / W, 640 / H]
    _assert_close(snap_rect_bbox(page, cut))


def test_snap_none_when_no_rect(tmp_path):
    # 테두리 없는 순수 텍스트 영역이면 None(원래 bbox 폴백)
    img = np.full((H, W), 255, np.uint8)
    for i in range(8):
        cv2.putText(img, "plain text " * 3, (100, 400 + i * 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, 60, 2)
    p = str(tmp_path / "plain.png")
    cv2.imwrite(p, img)
    assert snap_rect_bbox(p, [100 / W, 380 / H, 800 / W, 700 / H]) is None
