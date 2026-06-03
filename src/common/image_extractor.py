"""
PDF 그림 영역 자동 감지 + PNG 추출.

두 가지 전략:
  A. PyMuPDF (텍스트 기반 PDF):
     벡터/래스터 오브젝트 bbox 수집 → 텍스트 없는 영역 = 그림 후보
  B. Claude Vision (스캔 PDF):
     렌더링된 페이지 PNG들을 한 번에 전송 → 문제별 그림 bbox 반환

반환: list[ExtractedImage]  (파일 경로 + 문항 매칭 정보)
"""
from __future__ import annotations

import base64
import json
import os
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


_VISION_PROMPT = """\
다음은 한국 수학 시험지의 페이지 이미지들입니다(순서대로 1페이지, 2페이지, ...).
각 페이지에서 수학 그래프, 좌표평면, 도형, 다이어그램 등 **그림** 요소를 찾아주세요.
(수식, 텍스트, 선택지 ①②③④⑤, 조건/보기 박스는 제외)

각 그림에 대해 아래 JSON 배열로만 응답하세요. 설명 없이 JSON만 출력:
[
  {"problem": "7", "page": 2, "bbox": [52, 35, 95, 65]},
  ...
]

- problem: 해당 그림이 속한 문제 번호(숫자 문자열)
- page: 이미지 순서 번호(1부터)
- bbox: [left%, top%, right%, bottom%] (페이지 이미지 전체 크기 대비 백분율, 정수)

그림이 전혀 없으면 빈 배열 [] 만 출력.\
"""


def extract_figures_by_vision(
    page_pngs: list[Path],
    output_dir: Path,
    api_key: str | None = None,
) -> dict[str, Path]:
    """
    Claude Vision으로 시험지 페이지들에서 그림 감지 + 크롭.

    page_pngs: 렌더링된 페이지 PNG 리스트 (순서대로 p1, p2, ...)
    output_dir: 크롭된 그림 PNG 저장 폴더
    api_key: Anthropic API key (None이면 환경변수 사용)

    반환: {item_no: cropped_figure_path}
    """
    import anthropic
    from PIL import Image as PILImage

    if not page_pngs:
        return {}

    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY 환경변수 없음")

    output_dir.mkdir(parents=True, exist_ok=True)
    client = anthropic.Anthropic(api_key=key)

    # 이미지 콘텐츠 블록 구성
    content: list[dict] = []
    for png in page_pngs:
        b64 = base64.standard_b64encode(png.read_bytes()).decode()
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64},
        })
    content.append({"type": "text", "text": _VISION_PROMPT})

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": content}],
    )
    raw = resp.content[0].text.strip()

    # JSON 파싱
    try:
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        figures_json: list[dict] = json.loads(m.group(0)) if m else []
    except Exception:
        print(f"  [vision_fig] JSON 파싱 실패: {raw[:200]}")
        return {}

    result: dict[str, Path] = {}
    for fig in figures_json:
        prob = str(fig.get("problem", "")).strip()
        pg   = int(fig.get("page", 1))
        bbox = fig.get("bbox", [])
        if not prob or not bbox or len(bbox) != 4:
            continue
        if pg < 1 or pg > len(page_pngs):
            continue

        # 페이지 이미지에서 bbox 크롭
        page_png = page_pngs[pg - 1]
        img = PILImage.open(page_png)
        W, H = img.size
        x0 = max(0, int(bbox[0] / 100 * W))
        y0 = max(0, int(bbox[1] / 100 * H))
        x1 = min(W, int(bbox[2] / 100 * W))
        y1 = min(H, int(bbox[3] / 100 * H))
        if x1 <= x0 or y1 <= y0:
            continue

        cropped = img.crop((x0, y0, x1, y1))
        out_path = output_dir / f"fig_vision_{prob}.png"
        cropped.save(str(out_path))
        result[prob] = out_path
        print(f"  [vision_fig] {prob}번: p{pg} bbox={bbox} → {out_path.name}")

    return result


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
