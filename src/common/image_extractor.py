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
다음은 한국 수학 시험지 페이지 이미지들입니다(순서대로 p1, p2, ...).

[찾을 대상 — 인쇄된 순수 시각 요소만]
• 좌표평면 / 수직선 (x축·y축과 그 위의 곡선·점)
• 기하 도형 (삼각형·원·포물선 등 선만으로 이루어진 인쇄 그림)
• 벡터·선분 다이어그램

[반드시 제외]
• 문제 번호 및 문제 설명 텍스트
• 수식 ($...$ 형태의 기호·수식)
• 선택지 ①②③④⑤
• 조건 (가)(나) / 보기 ㄱ ㄴ ㄷ 텍스트 박스
• 학생 손글씨 풀이 (연필·볼펜 필기 흔적)
• 빈 칸(답 기입란)

[bbox 규칙]
그래프·도형 자체만 타이트하게 표시. 주변 텍스트·여백은 bbox에 포함하지 말 것.
그림 상단이 잘리지 않도록 그림 위쪽 경계를 실제보다 3~5% 위에서 시작할 것.

JSON 배열만 출력 (마크다운·설명 없이):
[{"problem":"7","page":2,"bbox":[left%,top%,right%,bottom%]}]

인쇄된 그림이 없으면: []\
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

    # 상단 추가 여백 (Vision이 top을 낮게 잡는 경향 보정)
    _TOP_MARGIN_PCT = 8

    result: dict[str, Path] = {}
    for fig in figures_json:
        prob = str(fig.get("problem", "")).strip()
        pg   = int(fig.get("page", 1))
        bbox = fig.get("bbox", [])
        if not prob or not bbox or len(bbox) != 4:
            continue
        if pg < 1 or pg > len(page_pngs):
            continue

        # 페이지 이미지에서 bbox 크롭 (top에 여백 추가)
        page_png = page_pngs[pg - 1]
        img = PILImage.open(page_png)
        W, H = img.size
        x0 = max(0, int(bbox[0] / 100 * W))
        y0 = max(0, int((bbox[1] - _TOP_MARGIN_PCT) / 100 * H))
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


_CROP_VISION_PROMPT = """\
이 이미지는 수학 시험지에서 문제 {num}번만 크롭한 것입니다.

이미지 구조 (위→아래 순서):
  [상단] 문제 번호 + 문제 설명 텍스트 (이미지 상위 약 15~30%)
  [중간] 수학 그래프·도형 (있는 경우)
  [하단] 선택지 ①②③④⑤ 또는 답 기입란 (이미지 하위 약 25~40%)

판단: 이 이미지에 인쇄된 수학 그래프·좌표평면·기하 도형이 있는가?

[그림 아님 → has_figure: false]
• 텍스트·수식만 있는 경우
• 학생 손글씨
• 선택지 ①②③④⑤만 있는 경우

[그림 있음 → has_figure: true + bbox]
bbox 규칙:
• left%, right%: 그래프·도형의 좌우 경계 (여백 5~10% 포함)
• top%: 실제 그림 시작점보다 **10% 더 위** (문제 텍스트 끝 직후라도 좋음)
• bottom%: 선택지 ①②③ 첫 글자가 보이는 줄의 **바로 위** (선택지 포함 금지)
  선택지가 없으면 이미지 높이의 90%를 넘지 말 것

JSON만 출력 (마크다운·설명 없이):
{"has_figure": true, "bbox": [left%, top%, right%, bottom%]}
또는
{"has_figure": false}\
"""


def find_figure_by_density(
    crop_png: Path,
    problem_no: str,
    output_dir: Path,
    text_threshold: float = 0.04,
    smooth_window: int = 20,
    min_fig_height_pct: float = 0.10,
) -> Path | None:
    """
    행별 픽셀 밀도 분석으로 그림 영역 추출 (API 불필요).

    원리: 텍스트 행(고밀도) 사이의 최대 갭 = 그림 구간
      - 문제 설명 텍스트: 다크픽셀 비율 15~30% (매우 고밀도)
      - 그림 (좌표축·곡선): 비율 0.3~4% (저밀도)
      - 선택지: 비율 4~8% (중밀도)
    """
    import numpy as np
    from PIL import Image as PILImage

    output_dir.mkdir(parents=True, exist_ok=True)
    img = PILImage.open(crop_png).convert("L")
    arr = np.array(img)
    H, W = arr.shape

    dark = (arr < 180)
    row_density = dark.sum(axis=1) / W

    kernel = np.ones(smooth_window) / smooth_window
    smooth = np.convolve(row_density, kernel, mode="same")
    is_text = smooth > text_threshold

    # ── 상단: 문제 텍스트 끝 찾기 ────────────────────────────────────────
    # 첫 연속 고밀도 블록이 끝나는 지점 → 그림 시작
    text_rows = list(np.where(is_text)[0])
    if not text_rows:
        return None

    # 첫 텍스트 블록 끝 (첫 번째 큰 갭 직전)
    first_block_end = text_rows[0]
    for i in range(len(text_rows) - 1):
        if text_rows[i + 1] - text_rows[i] > smooth_window:
            first_block_end = text_rows[i]
            break
    else:
        first_block_end = text_rows[-1]

    # fig_top = 첫 블록 끝 + 작은 버퍼 (텍스트 끝 직후부터)
    fig_top = min(H - 1, first_block_end + smooth_window // 2)

    # ── 하단: 선택지 시작 찾기 (고밀도 블록 역방향 탐색) ──────────────────
    # 선택지는 문제 하단에 있으며 밀도가 높음 (> HIGH_TH)
    HIGH_TH = 0.06
    choices_start = int(H * 0.85)  # 기본값: 보수적 85%

    # 크롭 하단 70%→30% 구간에서 위로 탐색
    scan_lo = int(H * 0.70)
    scan_hi = int(H * 0.30)
    in_dense = False
    last_dense = scan_lo

    for i in range(scan_lo, scan_hi, -1):
        if smooth[i] > HIGH_TH:
            if not in_dense:
                in_dense = True
                last_dense = i
        else:
            if in_dense:
                # 고밀도 블록 상단 직전 = 선택지 위
                choices_start = last_dense
                break
            in_dense = False

    fig_bot = min(H, choices_start)

    # ── 유효성 검사 ───────────────────────────────────────────────────────
    if fig_bot <= fig_top:
        return None
    if (fig_bot - fig_top) < int(H * min_fig_height_pct):
        return None

    if dark[fig_top:fig_bot].sum() < 50:
        return None

    out_path = output_dir / f"fig_density_{problem_no}.png"
    PILImage.fromarray(arr[fig_top:fig_bot, :]).save(str(out_path))
    pct_top = round(fig_top / H * 100)
    pct_bot = round(fig_bot / H * 100)
    print(f"  [density_fig] {problem_no}번: {pct_top}%~{pct_bot}% (높이={fig_bot-fig_top}행) → {out_path.name}")
    return out_path


def detect_figure_in_crop(
    crop_png: Path,
    problem_no: str,
    output_dir: Path,
    api_key: str | None = None,
) -> Path | None:
    """
    단일 문제 크롭에서 그림 추출.

    1차: 밀도 분석 (API 불필요) — 텍스트 고밀도 행 제외 후 중간 구간 크롭
    2차: 밀도 분석 실패 시 Vision 폴백 (API 사용)
    """
    # 1차: 밀도 분석
    result = find_figure_by_density(crop_png, problem_no, output_dir)
    if result:
        return result

    # 2차: Vision 폴백
    import anthropic
    from PIL import Image as PILImage

    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    client = anthropic.Anthropic(api_key=key)

    b64 = base64.standard_b64encode(crop_png.read_bytes()).decode()
    prompt = _CROP_VISION_PROMPT.replace("{num}", problem_no)

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=256,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
    except Exception:
        return None

    raw = resp.content[0].text.strip()
    try:
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
    except Exception:
        return None

    if not data.get("has_figure"):
        return None

    bbox = data.get("bbox", [])
    if not bbox or len(bbox) != 4:
        return None

    img = PILImage.open(crop_png)
    W, H = img.size
    _TOP_M = 10
    x0 = max(0, int(bbox[0] / 100 * W))
    y0 = max(0, int((bbox[1] - _TOP_M) / 100 * H))
    x1 = min(W, int(bbox[2] / 100 * W))
    y1 = min(H, int(bbox[3] / 100 * H))
    if x1 <= x0 or y1 <= y0:
        return None

    out_path = output_dir / f"fig_crop_{problem_no}.png"
    img.crop((x0, y0, x1, y1)).save(str(out_path))
    print(f"  [vision_fig] {problem_no}번 Vision 폴백: bbox={bbox} → {out_path.name}")
    return out_path


def extract_figures_from_crops(
    crop_pngs: dict[str, Path],
    output_dir: Path,
    api_key: str | None = None,
) -> dict[str, Path]:
    """
    문제별 크롭 PNG에서 그림을 개별 Vision으로 감지·추출.

    crop_pngs: {item_no: crop_png_path}
    반환: {item_no: figure_png_path}
    """
    result: dict[str, Path] = {}
    for num, crop in sorted(crop_pngs.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 999):
        fig = detect_figure_in_crop(crop, num, output_dir, api_key=api_key)
        if fig:
            result[num] = fig
        else:
            print(f"  [vision_fig] {num}번: 그림 없음")
    return result


def extract_figures_with_bbox_detection(
    pdf_path: Path,
    problem_numbers: set[str],
    output_dir: Path,
    api_key: str | None = None,
) -> dict[str, Path]:
    """
    BBoxDetector로 문제 위치 감지 → 문제별 크롭 → detect_figure_in_crop.

    스캔 PDF 전용 2단계 추출:
      1. BBoxDetector: 페이지·컬럼·y범위 획득
      2. 문제 크롭 PNG 생성
      3. detect_figure_in_crop: 크롭 내 그래프/도형 bbox만 추출

    problem_numbers: Claude OCR이 마킹한 그림 문제 번호 집합 {"2","9",...}
    반환: {item_no: figure_png_path}
    """
    from src.pipeline.bbox_detector import BBoxDetector, THUMB_DPI
    from src.pipeline.crop_ocr_builder import CROP_SCALE, THUMB_SCALE

    output_dir.mkdir(parents=True, exist_ok=True)
    thumb_dir = output_dir / "thumbs"

    print(f"  [bbox] 문제 위치 감지 중...")
    detector = BBoxDetector()
    bboxes = detector.detect_all(pdf_path, thumb_dir, verbose=False)
    # _adjust_bboxes 사용 안 함 — 원본 bbox + 고정 여백으로 타이트하게 크롭

    doc = fitz.open(str(pdf_path))
    mat = fitz.Matrix(CROP_SCALE, CROP_SCALE)

    # 여백 (thumb px 단위): 위 5px, 아래 50px
    # BBoxDetector가 전체 문제(선택지 포함)를 감지하므로 패딩은 작게
    TOP_PAD  = 5
    BOT_PAD  = 50

    crop_pngs: dict[str, Path] = {}
    for num_str in problem_numbers:
        try:
            num = int(num_str)
        except ValueError:
            continue
        if num not in bboxes:
            print(f"  [bbox] {num_str}번 위치 미감지")
            continue
        bbox = bboxes[num]
        page = doc[bbox["page"]]
        pix_full = page.get_pixmap(matrix=mat)
        W_full, H_full = pix_full.width, pix_full.height
        mid = W_full // 2

        # thumb → crop px 변환
        scale = CROP_SCALE / THUMB_SCALE
        y_top = max(0, int((bbox["y_top"] - TOP_PAD) * scale))
        y_bot = min(H_full, int((bbox["y_bottom"] + BOT_PAD) * scale))

        col = bbox.get("col", "left")
        x0, x1 = (0, mid - 20) if col == "left" else (mid + 20, W_full)

        clip = fitz.Rect(x0 / CROP_SCALE, y_top / CROP_SCALE,
                         x1 / CROP_SCALE, y_bot / CROP_SCALE)
        pix = page.get_pixmap(matrix=mat, clip=clip)
        crop_path = output_dir / f"prob_crop_{num_str}.png"
        pix.save(str(crop_path))
        crop_pngs[num_str] = crop_path

    doc.close()

    if not crop_pngs:
        return {}

    print(f"  [bbox] {len(crop_pngs)}문제 크롭 완료 → Vision 판정")
    return extract_figures_from_crops(crop_pngs, output_dir, api_key=api_key)


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
