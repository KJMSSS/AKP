"""
PDF 그림 영역 자동 감지 + PNG 추출 (pymupdf 기반).

전략:
  1. 각 페이지에서 벡터/래스터 오브젝트 bbox 수집
  2. 텍스트 없는 큰 사각형 영역 = 그림 후보
  3. 인접 문제 텍스트 bbox와 매칭 → 문항 번호 태그

반환: list[ExtractedImage]  (파일 경로 + 문항 매칭 정보)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz  # pymupdf

# 그림으로 인정할 최소 크기 (pt)
_MIN_WIDTH_PT  = 60.0
_MIN_HEIGHT_PT = 40.0

# 텍스트 밀도가 이 이하면 그림 영역으로 간주
_MAX_TEXT_DENSITY = 0.05

# 문제 번호 패턴
_ITEM_NO_RE = re.compile(r"^(\d{1,2})[.．]")


@dataclass
class ExtractedImage:
    page: int             # 0-indexed PDF 페이지
    bbox: fitz.Rect       # pt 기준 bbox
    image_path: Path      # 저장된 PNG 경로
    item_no: str = ""     # 연관 문항 번호 ("3", "7" 등)
    confidence: float = 0.0


def _has_meaningful_text(page: fitz.Page, rect: fitz.Rect, threshold: float = 0.1) -> bool:
    """rect 영역 내 텍스트 밀도 계산 — 높으면 텍스트 영역."""
    words = page.get_text("words", clip=rect)
    if not words:
        return False
    total_char = sum(len(w[4]) for w in words)
    area = rect.width * rect.height
    return (total_char / area) > threshold if area > 0 else False


def _find_image_rects(page: fitz.Page) -> list[fitz.Rect]:
    """페이지에서 그림 영역 bbox 목록 반환."""
    page_rect = page.rect
    page_area = page_rect.width * page_rect.height
    rects: list[fitz.Rect] = []

    # 1. 래스터 이미지 블록
    for block in page.get_text("dict")["blocks"]:
        if block["type"] == 1:  # image block
            r = fitz.Rect(block["bbox"])
            if r.width >= _MIN_WIDTH_PT and r.height >= _MIN_HEIGHT_PT:
                rects.append(r)

    # 2. 벡터 드로잉 — 텍스트 없는 영역만
    for path in page.get_drawings():
        r = fitz.Rect(path.get("rect") or path.get("clip") or [0, 0, 0, 0])
        if (r.width >= _MIN_WIDTH_PT and r.height >= _MIN_HEIGHT_PT
                and not _has_meaningful_text(page, r)):
            rects.append(r)

    # 중복 제거 (겹치는 rect 병합)
    merged: list[fitz.Rect] = []
    for r in rects:
        absorbed = False
        for i, m in enumerate(merged):
            if abs(m & r):  # 교차 있으면 병합
                merged[i] = m | r
                absorbed = True
                break
        if not absorbed:
            merged.append(r)

    # 페이지 전체 크기(80% 이상)는 제외
    return [
        r for r in merged
        if r.width >= _MIN_WIDTH_PT
        and r.height >= _MIN_HEIGHT_PT
        and (r.width * r.height) / page_area < 0.8
    ]


def _nearest_item_no(page: fitz.Page, rect: fitz.Rect) -> str:
    """그림 위쪽 텍스트에서 가장 가까운 문제 번호 반환."""
    search_rect = fitz.Rect(rect.x0, max(0, rect.y0 - 60), rect.x1, rect.y0)
    words = page.get_text("words", clip=search_rect)
    for w in reversed(words):  # 위쪽 → 아래쪽 순이므로 reversed
        m = _ITEM_NO_RE.match(w[4])
        if m:
            return m.group(1)
    return ""


def extract_images(
    pdf_path: Path,
    output_dir: Path,
    dpi: int = 150,
    pages: list[int] | None = None,
) -> list[ExtractedImage]:
    """
    PDF에서 그림 영역을 추출해 PNG로 저장.

    pages: None이면 전체 페이지, 지정하면 해당 페이지만 (0-indexed).
    반환: list[ExtractedImage]
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    results: list[ExtractedImage] = []
    mat = fitz.Matrix(dpi / 72, dpi / 72)

    target_pages = pages if pages is not None else list(range(len(doc)))

    for pg_idx in target_pages:
        if pg_idx >= len(doc):
            continue
        page = doc[pg_idx]
        image_rects = _find_image_rects(page)

        for i, rect in enumerate(image_rects):
            clip = page.get_pixmap(matrix=mat, clip=rect, alpha=False)
            stem = f"{pdf_path.stem}_p{pg_idx + 1}_fig{i + 1}"
            out_path = output_dir / f"{stem}.png"
            clip.save(str(out_path))

            item_no = _nearest_item_no(page, rect)
            results.append(ExtractedImage(
                page=pg_idx,
                bbox=rect,
                image_path=out_path,
                item_no=item_no,
            ))

    doc.close()
    return results
