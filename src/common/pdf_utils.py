"""PDF 전처리 유틸리티."""
from __future__ import annotations

import io
import os
from collections import Counter
from pathlib import Path

# OSD orientation_conf 최소 신뢰도 — 이 미만이면 방향 미확정으로 본다.
# ★ 골드셋 실측(18쌍): 진짜 뒤집힌 페이지(광주여고 양면 뒷면)는 conf 15.6~17.6,
#   멀쩡한데 OSD가 오판한 거짓양성(동성고·명진고·문성고 p4 등)은 전부 <3.6.
#   문턱 1.0은 거짓양성을 그대로 통과시켜 멀쩡한 페이지를 뒤집어 OCR을 망쳤다
#   (문성고 51%→24%). 8.0으로 올려 둘을 분리한다. 거짓 수용(멀쩡한 페이지 회전)이
#   거짓 기각(안 돌림)보다 훨씬 치명적이므로 보수적으로 높게 잡는다.
_OSD_CONF_MIN = 8.0
# 잉크(어두운) 픽셀 비율이 이 미만이면 백지로 간주 (회전 무의미)
_BLANK_INK_RATIO = 0.004
# 보정 페이지 렌더 해상도·압축 — 무손실 300DPI는 6페이지에 40MB를 넘겨
# 후속 OCR이 청크 분할(문제 절단 위험)되므로 OCR 충분 수준으로 억제
_RENDER_DPI  = 250
_JPG_QUALITY = 85


def _set_tesseract_cmd(pytesseract) -> None:
    """Tesseract 바이너리 경로 설정 (image_extractor와 동일 규칙)."""
    tess_cmd = os.environ.get("TESSERACT_CMD", "")
    if tess_cmd:
        pytesseract.pytesseract.tesseract_cmd = tess_cmd
    elif os.name == "nt":
        default = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        if Path(default).exists():
            pytesseract.pytesseract.tesseract_cmd = default


_osd_warned = False


def _osd_tessdata_dir() -> str | None:
    """`osd.traineddata` 를 가진 tessdata 디렉터리를 찾는다.

    ★ 중요: `.env` 의 TESSDATA_PREFIX 가 프로젝트 로컬 `.tessdata`(equ 등만 담고
       osd 누락)를 가리키면 image_to_osd 가 실패해 회전 보정이 조용히 꺼진다.
       그 경우 시스템 Tesseract tessdata 로 폴백해 OSD 만은 살린다.
    못 찾으면 None — 호출자가 --tessdata-dir 없이 기본 검색에 맡긴다.
    """
    cands: list[Path] = []
    env = os.environ.get("TESSDATA_PREFIX", "")
    if env:
        cands.append(Path(env))
    # 시스템 Tesseract tessdata (cmd 경로 이웃 + Windows 기본)
    tess_cmd = os.environ.get("TESSERACT_CMD", "")
    if tess_cmd:
        cands.append(Path(tess_cmd).parent / "tessdata")
    if os.name == "nt":
        cands.append(Path(r"C:\Program Files\Tesseract-OCR\tessdata"))
    cands.append(Path("/usr/share/tesseract-ocr/4.00/tessdata"))  # Linux(Railway)
    cands.append(Path("/usr/share/tessdata"))
    for d in cands:
        try:
            if (d / "osd.traineddata").exists():
                return str(d)
        except OSError:
            continue
    return None


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
    # ★ TESSDATA_PREFIX(.env)가 osd 빠진 .tessdata를 가리키면 image_to_osd가
    #   실패해 회전 보정이 통째로 꺼진다. osd 가진 디렉터리를 찾아 OSD 호출
    #   동안만 TESSDATA_PREFIX를 갈아끼운다(--tessdata-dir는 공백·따옴표 파싱
    #   문제가 있어 환경변수 교체가 안전). 호출 후 원복.
    osd_dir = _osd_tessdata_dir()
    _prev_prefix = os.environ.get("TESSDATA_PREFIX")
    if osd_dir:
        os.environ["TESSDATA_PREFIX"] = osd_dir
    try:
        osd = pytesseract.image_to_osd(img, output_type=Output.DICT)
        rotate = int(osd.get("rotate", 0)) % 360
        conf = float(osd.get("orientation_conf", 0) or 0)
    except Exception as e:
        rotate, conf = -1, 0.0
        global _osd_warned
        if not _osd_warned:
            _osd_warned = True
            print(f"  [경고] Tesseract OSD 실패 — 회전 보정이 제한됩니다. "
                  f"osd.traineddata 확인 필요. ({str(e)[:90]})")
    finally:
        if osd_dir:
            if _prev_prefix is None:
                os.environ.pop("TESSDATA_PREFIX", None)
            else:
                os.environ["TESSDATA_PREFIX"] = _prev_prefix

    if conf >= _OSD_CONF_MIN and rotate in (0, 90, 180, 270):
        return ("osd", rotate)

    # OSD 실패/저신뢰: 가로면 누웠을 가능성 높음 → 보간 신호, 세로면 정방향 가정
    if W > H:
        return ("uncertain", None)
    return ("osd", 0)


def _plan_rotations(raw: list[tuple[str, int | None, int]]) -> list[tuple[str, int]]:
    """원시 분류 [(kind, 추가각, 메타 base)] → 페이지별 (action, 최종 회전각).

    핵심 규칙:
      최종 회전각 = (메타 base + 내용 추가각) % 360.
      ★ 양면 스캔은 메타가 일정(예: 270)이어도 뒷면이 물리적으로 180° 뒤집혀
        있어, base 만으로는 짝수 페이지가 거꾸로 남는다. 내용 추가각을 더해야
        정방향이 된다.
      uncertain(가로 OSD 실패) 페이지는 확정된 추가각의 최빈값으로 보간.
      action 은 'bake'(렌더해 굽기) 또는 'copy'(보정 불필요 — 원본 복사).
    """
    known = [a for k, a, _ in raw if k == "osd" and a in (90, 180, 270)]
    fallback = Counter(known).most_common(1)[0][0] if known else 0

    plans: list[tuple[str, int]] = []
    for kind, add, base in raw:
        if kind == "blank":
            plans.append(("copy", base))
        else:
            a = fallback if kind == "uncertain" else (add or 0)
            final = (base + a) % 360
            plans.append(("bake", final) if (base or a) else ("copy", base))
    return plans


def _insert_rendered_page(new_doc, pix, dpi: int = _RENDER_DPI) -> None:
    """렌더된 pixmap을 JPEG 압축 이미지 페이지로 삽입 (무손실 대비 ~1/15 크기)."""
    w_pt = pix.width * 72 / dpi
    h_pt = pix.height * 72 / dpi
    new_page = new_doc.new_page(width=w_pt, height=h_pt)
    try:
        stream = pix.tobytes("jpeg", jpg_quality=_JPG_QUALITY)
    except (TypeError, ValueError):
        # 구버전 PyMuPDF: jpg_quality 미지원 — 기본 품질로 폴백
        stream = pix.tobytes("jpeg")
    new_page.insert_image(new_page.rect, stream=stream)


def normalize_pdf_rotation(src_path: Path, use_content_detection: bool = True) -> Path:
    """
    PDF 페이지의 회전을 정상화한다.

    세 종류의 회전을 처리:
      1. **메타데이터 회전** (page.rotation != 0): JPEG 렌더로 구워낸다.
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

    # 1패스: 모든 페이지를 '메타 회전 적용 후' 내용(OSD)으로 분류.
    # ★ 핵심: 메타 회전이 있어도(예: 양면 스캔 270) 뒷면은 거꾸로일 수 있다.
    #   _classify_page_rotation은 메타 적용 렌더 기준이므로, 반환각은 메타 위에
    #   '추가로' 돌려야 할 각도다. 최종 회전 = (메타 base + 추가각) % 360.
    raw: list[tuple[str, int | None, int]] = []   # (kind, 추가각, 메타 base)
    for page in doc:
        base = page.rotation
        if use_content_detection:
            kind, add = _classify_page_rotation(page)
        else:
            kind, add = ("osd", 0)
        raw.append((kind, add, base))

    # 2패스: 원시 분류 → 페이지별 (action, 최종 회전각)
    plans = _plan_rotations(raw)

    needs_fix = any(act == "bake" for act, _ in plans)
    if not needs_fix:
        doc.close()
        return src_path

    fixes = [(i + 1, f) for i, (act, f) in enumerate(plans) if act == "bake"]
    interp_pages = [i + 1 for i, (k, _, _) in enumerate(raw) if k == "uncertain"]
    print(f"  [회전 감지] 보정 페이지(최종각): {fixes} → 보정 중...")
    if interp_pages:
        print(f"  [회전 보간] OSD 실패 가로 페이지 {interp_pages} → 최빈 추가각 {fallback}")

    new_doc = fitz.open()
    mat = fitz.Matrix(_RENDER_DPI / 72, _RENDER_DPI / 72)

    for i, page in enumerate(doc):
        act, final = plans[i]
        if act == "bake":
            # 최종 회전 강제 적용 후 렌더 (정방향으로 굽기)
            page.set_rotation(final)
            _insert_rendered_page(new_doc, page.get_pixmap(matrix=mat))
        else:
            # 보정 불필요 — 벡터 내용 그대로 복사 (품질 보존)
            new_doc.insert_pdf(doc, from_page=i, to_page=i)

    fixed_path = src_path.with_name(src_path.stem + "_rotfix.pdf")
    new_doc.save(str(fixed_path), garbage=4, deflate=True)
    new_doc.close()
    doc.close()

    print(f"  [회전 보정 완료] {fixed_path.name} ({fixed_path.stat().st_size:,} bytes)")
    return fixed_path
