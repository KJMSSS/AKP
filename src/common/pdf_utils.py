"""PDF 전처리 유틸리티."""
from __future__ import annotations

import io
import os
from pathlib import Path


def _set_tesseract_cmd(pytesseract) -> None:
    """Tesseract 바이너리 경로 설정 (image_extractor와 동일 규칙)."""
    tess_cmd = os.environ.get("TESSERACT_CMD", "")
    if tess_cmd:
        pytesseract.pytesseract.tesseract_cmd = tess_cmd
    elif os.name == "nt":
        default = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        if Path(default).exists():
            pytesseract.pytesseract.tesseract_cmd = default


def _detect_content_rotation(page, dpi: int = 150) -> int:
    """
    Tesseract OSD로 페이지 내용의 회전을 감지.

    PDF 회전 메타데이터(page.rotation)가 0이어도 내용이 물리적으로 누운
    스캔본을 잡기 위함. 반환값은 "정상으로 만들기 위해 시계방향으로 돌릴 각도"
    (0/90/180/270). 감지 실패·텍스트 부족 시 0.
    """
    try:
        import fitz  # noqa: F401  (page는 이미 fitz.Page)
        import pytesseract
        from pytesseract import Output
        from PIL import Image
    except ImportError:
        return 0

    _set_tesseract_cmd(pytesseract)

    try:
        import fitz
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72))
        img = Image.open(io.BytesIO(pix.tobytes("png")))
    except Exception:
        return 0

    try:
        # image_to_osd: 'rotate' = 정상으로 만들기 위해 시계방향으로 돌릴 각도
        osd = pytesseract.image_to_osd(img, output_type=Output.DICT)
        rotate = int(osd.get("rotate", 0)) % 360
        # OSD 신뢰도가 너무 낮으면 무시 (오탐 방지)
        conf = float(osd.get("orientation_conf", 0) or 0)
        if rotate in (90, 180, 270) and conf >= 1.0:
            return rotate
        return 0
    except Exception:
        # 텍스트 부족 등으로 OSD 실패 — 보정하지 않음
        return 0


def _insert_rendered_page(new_doc, pix) -> None:
    """렌더된 pixmap을 새 이미지 페이지로 삽입."""
    w_pt = pix.width * 72 / 300
    h_pt = pix.height * 72 / 300
    new_page = new_doc.new_page(width=w_pt, height=h_pt)
    new_page.insert_image(new_page.rect, pixmap=pix)


def normalize_pdf_rotation(src_path: Path, use_content_detection: bool = True) -> Path:
    """
    PDF 페이지의 회전을 정상화한다.

    두 종류의 회전을 모두 처리:
      1. **메타데이터 회전** (page.rotation != 0): PyMuPDF가 자동 반영하므로
         300 DPI 렌더로 구워낸다.
      2. **내용 회전** (메타는 0이지만 스캔이 물리적으로 누움):
         Tesseract OSD로 감지 → set_rotation으로 강제 회전 후 렌더.
         use_content_detection=False면 이 단계를 건너뛴다.

    - 보정할 페이지가 하나도 없으면 원본 경로를 그대로 반환.
    - 보정이 필요하면 새 PDF로 저장 후 반환. 파일명: {stem}_rotfix.pdf
    """
    import fitz  # PyMuPDF

    doc = fitz.open(str(src_path))

    # 각 페이지의 보정 방식 결정: ("meta"|"content"|"none", angle)
    plans: list[tuple[str, int]] = []
    for page in doc:
        if page.rotation != 0:
            plans.append(("meta", page.rotation))
        elif use_content_detection:
            ang = _detect_content_rotation(page)
            plans.append(("content", ang) if ang else ("none", 0))
        else:
            plans.append(("none", 0))

    needs_fix = any(kind == "meta" or (kind == "content" and ang) for kind, ang in plans)
    if not needs_fix:
        doc.close()
        return src_path

    meta_pages    = [i + 1 for i, (k, _) in enumerate(plans) if k == "meta"]
    content_pages = [(i + 1, a) for i, (k, a) in enumerate(plans) if k == "content"]
    print(f"  [회전 감지] 메타:{meta_pages} 내용:{content_pages} → 보정 중...")

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
