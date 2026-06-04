"""PDF 전처리 유틸리티."""
from __future__ import annotations

from pathlib import Path


def normalize_pdf_rotation(src_path: Path) -> Path:
    """
    PDF 페이지의 회전(rotation)을 정상화한다.

    - 회전된 페이지가 없으면 원본 경로를 그대로 반환.
    - 회전된 페이지가 있으면 300 DPI로 렌더링해 새 PDF로 저장 후 반환.
      파일명: {stem}_rotfix.pdf (원본과 같은 디렉토리)

    PyMuPDF get_pixmap()은 rotation 메타데이터를 자동으로 반영하므로
    렌더링된 픽스맵은 항상 "사람 눈에 정상으로 보이는 방향"이다.
    """
    import fitz  # PyMuPDF

    doc = fitz.open(str(src_path))
    rotations = [p.rotation for p in doc]

    if all(r == 0 for r in rotations):
        doc.close()
        return src_path

    rotated_pages = [i + 1 for i, r in enumerate(rotations) if r != 0]
    print(f"  [회전 감지] {rotated_pages}번 페이지 (각도: {rotations}) → 보정 중...")

    new_doc = fitz.open()
    mat = fitz.Matrix(300 / 72, 300 / 72)  # 300 DPI

    for i, page in enumerate(doc):
        rot = page.rotation
        if rot == 0:
            # 회전 없음 — 벡터 내용 그대로 복사
            new_doc.insert_pdf(doc, from_page=i, to_page=i)
        else:
            # 회전 있음 — 렌더링(자동 회전 적용) 후 이미지 페이지로 삽입
            pix = page.get_pixmap(matrix=mat)
            w_pt = pix.width * 72 / 300
            h_pt = pix.height * 72 / 300
            new_page = new_doc.new_page(width=w_pt, height=h_pt)
            new_page.insert_image(new_page.rect, pixmap=pix)

    fixed_path = src_path.with_name(src_path.stem + "_rotfix.pdf")
    new_doc.save(str(fixed_path), garbage=4, deflate=True)
    new_doc.close()
    doc.close()

    print(f"  [회전 보정 완료] {fixed_path.name} ({fixed_path.stat().st_size:,} bytes)")
    return fixed_path
