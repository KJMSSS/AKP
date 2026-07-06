"""
인제스트: PDF/이미지 -> 정규화된 페이지 이미지(고해상도) + 메타.

- PDF: poppler 의 pdftoppm 으로 페이지별 PNG 렌더(기본 300dpi). 추가 파이썬 의존성 불필요.
  (pymupdf 가 있으면 그쪽을 우선 사용 — 더 빠름)
- 이미지: 그대로 사용(필요 시 회전/리사이즈).
- 전처리(opencv 있으면): deskew(기울기 보정) + denoise. OCR 정확도 향상용.

반환: list[PageImage] (index, path, width, height).
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PageImage:
    index: int
    path: str
    width: int
    height: int


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _image_size(path: str) -> tuple[int, int]:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:
        return (0, 0)


def render_pdf(pdf_path: str, out_dir: str, dpi: int = 300) -> list[PageImage]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 1) pymupdf 우선
    try:
        import fitz  # type: ignore
        pages = []
        doc = fitz.open(pdf_path)
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        for i, page in enumerate(doc):
            pix = page.get_pixmap(matrix=mat)
            p = str(out / f"page-{i+1:03d}.png")
            pix.save(p)
            pages.append(PageImage(i, p, pix.width, pix.height))
        return pages
    except Exception:
        pass

    # 2) poppler pdftoppm 폴백
    if not _have("pdftoppm"):
        raise RuntimeError("PDF 렌더러가 없습니다. pymupdf 설치 또는 poppler(pdftoppm) 필요.")
    prefix = str(out / "page")
    subprocess.run(
        ["pdftoppm", "-r", str(dpi), "-png", pdf_path, prefix],
        check=True, capture_output=True,
    )
    pages = []
    for i, f in enumerate(sorted(out.glob("page-*.png"))):
        w, h = _image_size(str(f))
        pages.append(PageImage(i, str(f), w, h))
    return pages


def preprocess(image_path: str, *, deskew: bool = True, denoise: bool = True) -> str:
    """opencv 가 있으면 기울기 보정 + 노이즈 제거 후 저장(덮어쓰기). 없으면 원본 유지."""
    try:
        import cv2
        import numpy as np
    except Exception:
        return image_path

    img = cv2.imread(image_path)
    if img is None:
        return image_path
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    if deskew:
        thr = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        coords = np.column_stack(np.where(thr > 0))
        if len(coords) > 100:
            angle = cv2.minAreaRect(coords)[-1]
            angle = -(90 + angle) if angle < -45 else -angle
            if abs(angle) > 0.3:  # 미세 기울기만 보정
                h, w = img.shape[:2]
                m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
                img = cv2.warpAffine(img, m, (w, h),
                                     flags=cv2.INTER_CUBIC,
                                     borderMode=cv2.BORDER_REPLICATE)

    if denoise:
        img = cv2.fastNlMeansDenoisingColored(img, None, 5, 5, 7, 21)

    cv2.imwrite(image_path, img)
    return image_path


def ingest(path: str, *, dpi: int = 300, do_preprocess: bool = False,
           work_dir: str | None = None) -> list[PageImage]:
    work = work_dir or tempfile.mkdtemp(prefix="examconv_")
    Path(work).mkdir(parents=True, exist_ok=True)
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        pages = render_pdf(path, work, dpi=dpi)
    else:
        dst = str(Path(work) / p.name)
        if Path(path).resolve() != Path(dst).resolve():
            shutil.copy(path, dst)
        w, h = _image_size(dst)
        pages = [PageImage(0, dst, w, h)]

    if do_preprocess:
        for pg in pages:
            preprocess(pg.path)
            pg.width, pg.height = _image_size(pg.path)
    return pages


if __name__ == "__main__":
    import sys, json
    src = sys.argv[1]
    pages = ingest(src, do_preprocess=False)
    print(json.dumps([pg.__dict__ for pg in pages], ensure_ascii=False, indent=2))
