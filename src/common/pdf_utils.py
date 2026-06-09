"""PDF 전처리 유틸리티."""
from __future__ import annotations

import io
import os
from collections import Counter
from pathlib import Path

# OSD orientation_conf 최소 신뢰도 — 이 미만이면 방향 미확정으로 본다
_OSD_CONF_MIN = 1.0
# 잉크(어두운) 픽셀 비율이 이 미만이면 백지로 간주 (회전 무의미)
_BLANK_INK_RATIO = 0.004


def _set_tesseract_cmd(pytesseract) -> None:
    """Tesseract 바이너리 경로 설정 (image_extractor와 동일 규칙)."""
    tess_cmd = os.environ.get("TESSERACT_CMD", "")
    if tess_cmd:
        pytesseract.pytesseract.tesseract_cmd = tess_cmd
    elif os.name == "nt":
        default = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        if Path(default).exists():
            pytesseract.pytesseract.tesseract_cmd = default


def _classify_page_rotation(page, dpi: int = 150) -> tuple[str, int | None]:
    """
    페이지의 회전 상태를 분류한다 (메타 rotation=0 페이지 대상).

    반환:
      ("osd", angle)      OSD가 확신한 보정각 (정상으로 만들기 위해 시계방향으로
                          돌릴 각도, 0/90/180/270). angle=0이면 회전 불필요.
      ("blank", 0)        백지 — 회전 의미 없음.
      ("uncertain", None) 가로(landscape)인데 OSD가 실패/저신뢰 — 누웠을
                          가능성이 높아 호출자가 이웃 페이지로 보간해야 함.

    PDF 회전 메타데이터가 0이어도 내용이 물리적으로 누운 스캔본을 잡기 위함.
    """
    try:
        import fitz
        import pytesseract
        from pytesseract import Output
        from PIL import Image
        import numpy as np
    except ImportError:
        return ("osd", 0)

    _set_tesseract_cmd(pytesseract)

    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72))
        img = Image.open(io.BytesIO(pix.tobytes("png")))
    except Exception:
        return ("osd", 0)

    W, H = img.size

    # 백지 판정 — 잉크 픽셀이 거의 없으면 회전 무의미
    try:
        gray = np.asarray(img.convert("L"))
        ink_ratio = float((gray < 128).mean())
    except Exception:
        ink_ratio = 1.0
    if ink_ratio < _BLANK_INK_RATIO:
        return ("blank", 0)

    # OSD: 'rotate' = 정상으로 만들기 위해 시계방향으로 돌릴 각도
    try:
        osd = pytesseract.image_to_osd(img, output_type=Output.DICT)
        rotate = int(osd.get("rotate", 0)) % 360
        conf = float(osd.get("orientation_conf", 0) or 0)
    except Exception:
        rotate, conf = -1, 0.0

    if conf >= _OSD_CONF_MIN and rotate in (0, 90, 180, 270):
        return ("osd", rotate)

    # OSD 실패/저신뢰: 가로면 누웠을 가능성 높음 → 보간 신호, 세로면 정방향 가정
    if W > H:
        return ("uncertain", None)
    return ("osd", 0)


def _insert_rendered_page(new_doc, pix) -> None:
    """렌더된 pixmap을 새 이미지 페이지로 삽입."""
    w_pt = pix.width * 72 / 300
    h_pt = pix.height * 72 / 300
    new_page = new_doc.new_page(width=w_pt, height=h_pt)
    new_page.insert_image(new_page.rect, pixmap=pix)


def normalize_pdf_rotation(src_path: Path, use_content_detection: bool = True) -> Path:
    """
    PDF 페이지의 회전을 정상화한다.

    세 종류의 회전을 처리:
      1. **메타데이터 회전** (page.rotation != 0): 300 DPI 렌더로 구워낸다.
      2. **내용 회전** (메타 0이지만 스캔이 물리적으로 누움): Tesseract OSD로
         감지 → set_rotation으로 강제 회전 후 렌더.
      3. **불확실 페이지** (가로인데 OSD 실패): 같은 PDF의 확정 보정각 최빈값으로
         보간한다 (옅은 텍스트 페이지를 누운 채 OCR하는 미탐 방지).
         양면 교차회전이라 최빈값이 일부 틀릴 수 있으나, 0(미보정)보다 안전.
      백지 페이지는 보정하지 않는다.

    use_content_detection=False면 2·3단계를 건너뛰고 메타 회전만 처리.

    - 보정할 페이지가 없으면 원본 경로 그대로 반환.
    - 보정 필요 시 새 PDF로 저장 후 반환. 파일명: {stem}_rotfix.pdf
    """
    import fitz  # PyMuPDF

    doc = fitz.open(str(src_path))

    # 1패스: 페이지별 원시 분류
    raw: list[tuple[str, int | None]] = []
    for page in doc:
        if page.rotation != 0:
            raw.append(("meta", page.rotation))
        elif use_content_detection:
            raw.append(_classify_page_rotation(page))
        else:
            raw.append(("osd", 0))

    # 2패스: 불확실 페이지를 확정 보정각의 최빈값으로 보간
    known = [a for k, a in raw if k in ("meta", "osd") and a in (90, 180, 270)]
    fallback = Counter(known).most_common(1)[0][0] if known else 0

    plans: list[tuple[str, int]] = []
    for kind, ang in raw:
        if kind == "uncertain":
            plans.append(("content", fallback) if fallback else ("none", 0))
        elif kind == "meta":
            plans.append(("meta", ang or 0))
        elif kind == "blank":
            plans.append(("none", 0))
        else:  # osd
            plans.append(("content", ang) if ang else ("none", 0))

    needs_fix = any(k == "meta" or (k == "content" and a) for k, a in plans)
    if not needs_fix:
        doc.close()
        return src_path

    meta_pages    = [i + 1 for i, (k, _) in enumerate(plans) if k == "meta"]
    content_pages = [(i + 1, a) for i, (k, a) in enumerate(plans) if k == "content"]
    interp_pages  = [i + 1 for i, (k, _) in enumerate(raw) if k == "uncertain"]
    print(f"  [회전 감지] 메타:{meta_pages} 내용:{content_pages} → 보정 중...")
    if interp_pages:
        print(f"  [회전 보간] OSD 실패 가로 페이지 {interp_pages} → 최빈각 {fallback} 적용")

    new_doc = fitz.open()
    mat = fitz.Matrix(300 / 72, 300 / 72)  # 300 DPI

    for i, page in enumerate(doc):
        kind, ang = plans[i]
        if kind == "meta":
            # 메타 회전은 get_pixmap이 자동 반영
            _insert_rendered_page(new_doc, page.get_pixmap(matrix=mat))
        elif kind == "content":
            # 내용 회전 강제 적용 후 렌더 (정방향으로 굽기)
            page.set_rotation(ang)
            _insert_rendered_page(new_doc, page.get_pixmap(matrix=mat))
        else:
            # 회전 없음 — 벡터 내용 그대로 복사 (품질 보존)
            new_doc.insert_pdf(doc, from_page=i, to_page=i)

    fixed_path = src_path.with_name(src_path.stem + "_rotfix.pdf")
    new_doc.save(str(fixed_path), garbage=4, deflate=True)
    new_doc.close()
    doc.close()

    print(f"  [회전 보정 완료] {fixed_path.name} ({fixed_path.stat().st_size:,} bytes)")
    return fixed_path
