"""
HWPX 그림 삽입 — BinData/ 폴더에 이미지 추가 후 section XML에 그림 참조 삽입.

replace_placeholder_with_image():
  【★ 본문 손상 — 원본 PDF의 N번 참조】 단락을 실제 이미지 단락으로 교체.

crop_figure_from_pdf():
  PyMuPDF로 PDF 특정 페이지의 Mathpix 좌표 영역을 PNG로 추출.
"""
from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

# ── hp:pic 전체 XML 템플릿 (gold HWPX 분석 기반) ──────────────────────────
# w, h: HWP 단위 (1pt = 100). treatAsChar=1 → 글자처럼 취급 (인라인)
_PIC_XML = (
    '<hp:pic id="{oid}" zOrder="{zo}" numberingType="PICTURE" '
    'textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" '
    'dropcapstyle="None" href="" groupLevel="0" instid="{inst}" reverse="0">'
    '<hp:offset x="0" y="0"/>'
    '<hp:orgSz width="{w}" height="{h}"/>'
    '<hp:curSz width="0" height="0"/>'
    '<hp:flip horizontal="0" vertical="0"/>'
    '<hp:rotationInfo angle="0" centerX="{cx}" centerY="{cy}" rotateimage="1"/>'
    '<hp:renderingInfo>'
    '<hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
    '<hc:scaMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
    '<hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
    '</hp:renderingInfo>'
    '<hc:img binaryItemIDRef="{bin_id}" bright="0" contrast="0" effect="REAL_PIC" alpha="0"/>'
    '<hp:imgRect>'
    '<hc:pt0 x="0" y="0"/><hc:pt1 x="{w}" y="0"/>'
    '<hc:pt2 x="{w}" y="{h}"/><hc:pt3 x="0" y="{h}"/>'
    '</hp:imgRect>'
    '<hp:imgClip left="0" right="{w}" top="0" bottom="{h}"/>'
    '<hp:inMargin left="0" right="0" top="0" bottom="0"/>'
    '<hp:imgDim dimwidth="{w}" dimheight="{h}"/>'
    '<hp:effects/>'
    '<hp:sz width="{w}" widthRelTo="ABSOLUTE" height="{h}" heightRelTo="ABSOLUTE" protect="0"/>'
    '<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
    'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="PARA" vertAlign="TOP" '
    'horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
    '<hp:outMargin left="0" right="0" top="0" bottom="0"/>'
    '<hp:shapeComment/>'
    '</hp:pic>'
)

# 이미지 단락 템플릿 (paraPrIDRef/charPrIDRef는 호출자가 지정)
_PIC_PARA = (
    '<hp:p id="{pid}" paraPrIDRef="{ppr}" styleIDRef="0" '
    'pageBreak="0" columnBreak="0" merged="0">'
    '<hp:run charPrIDRef="{cpr}">'
    '{pic_xml}'
    '<hp:t/>'
    '</hp:run>'
    '<hp:linesegarray>'
    '<hp:lineseg textpos="0" vertpos="0" vertsize="{h}" textheight="{h}" '
    'baseline="{bl}" spacing="720" horzpos="0" horzsize="{tw}" flags="393216"/>'
    '</hp:linesegarray>'
    '</hp:p>'
)

# 플레이스홀더 단락 패턴: 【★ 본문 손상 — 원본 PDF...N번...】
_PLACEHOLDER_RE = re.compile(
    r'<hp:p\b[^>]*>(?:(?!</hp:p>)[\s\S])*'
    r'【★ 본문 손상[^】]*?(\d{1,2})번[^】]*】'
    r'(?:(?!</hp:p>)[\s\S])*</hp:p>',
)


def _next_bin_id(zf: zipfile.ZipFile) -> tuple[int, str]:
    """BinData/ 내 최대 BIN 번호 + 1과 접두어 반환."""
    ids = []
    for name in zf.namelist():
        m = re.search(r"BIN(\d+)\.", name, re.IGNORECASE)
        if m:
            ids.append(int(m.group(1)))
    n = (max(ids) + 1) if ids else 1
    return n, f"BIN{n:04d}"


def _make_pic_xml(bin_id: str, w: int, h: int, oid: int, zo: int) -> str:
    return _PIC_XML.format(
        oid=oid, zo=zo, inst=oid + 1000000,
        w=w, h=h, cx=w // 2, cy=h // 2,
        bin_id=bin_id,
    )


def replace_placeholder_with_image(
    hwpx_path: Path,
    item_no: str,
    image_path: Path,
    w_hpc: int,
    h_hpc: int,
    tw: int = 48189,
    ppr: int = 8,
    cpr: int = 0,
    out_path: Path | None = None,
) -> Path:
    """
    【★ 본문 손상 — 원본 PDF의 {item_no}번 참조】 단락을 이미지 단락으로 교체.

    w_hpc/h_hpc: HWP 단위 (1pt = 100)
    tw: 텍스트 폭 (HWP 단위, text_builder _TW 와 일치해야 함)
    """
    out_path = out_path or hwpx_path
    tmp_path = hwpx_path.with_suffix(".tmp.hwpx")

    with zipfile.ZipFile(hwpx_path, "r") as src_zf:
        bin_num, bin_stem = _next_bin_id(src_zf)
        bin_name = f"{bin_stem}{image_path.suffix.lower()}"
        xml = src_zf.read("Contents/section0.xml").decode("utf-8")

        # 새 ID들
        existing_ids = [int(m) for m in re.findall(r'<hp:p id="(\d+)"', xml)]
        new_pid = (max(existing_ids) + 1) if existing_ids else 300
        existing_zo = [int(m) for m in re.findall(r'zOrder="(\d+)"', xml)]
        new_zo = (max(existing_zo) + 1) if existing_zo else 300

        pic_xml = _make_pic_xml(bin_stem, w_hpc, h_hpc, oid=new_pid + 1000000, zo=new_zo)
        bl = round(h_hpc * 0.85)
        pic_para = _PIC_PARA.format(
            pid=new_pid, ppr=ppr, cpr=cpr,
            pic_xml=pic_xml,
            h=h_hpc, bl=bl, tw=tw,
        )

        # item_no번 플레이스홀더 찾아 교체
        pattern = re.compile(
            r'<hp:p\b[^>]*>(?:(?!</hp:p>)[\s\S])*'
            r'【★ 본문 손상[^】]*?' + re.escape(item_no) + r'번[^】]*】'
            r'(?:(?!</hp:p>)[\s\S])*</hp:p>',
        )
        xml_new, n = pattern.subn(pic_para, xml, count=1)
        if n == 0:
            print(f"  [pic] 경고: {item_no}번 플레이스홀더 미발견 — 삽입 건너뜀")
        else:
            print(f"  [pic] {item_no}번 → {bin_name} ({w_hpc}×{h_hpc} HWP)")

        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as dst_zf:
            for item in src_zf.infolist():
                dst_zf.writestr(item, src_zf.read(item.filename))
            dst_zf.writestr(f"BinData/{bin_name}", image_path.read_bytes())
            dst_zf.writestr("Contents/section0.xml", xml_new.encode("utf-8"))

    shutil.move(str(tmp_path), str(out_path))
    return out_path


def crop_figure_from_pdf(
    pdf_path: Path,
    page_1idx: int,
    mx: int,
    my: int,
    mw: int,
    mh: int,
    out_path: Path,
    render_dpi: int = 300,
    mathpix_dpi: int = 180,
    padding_pt: float = 4.0,
) -> Path:
    """
    PDF 특정 페이지에서 Mathpix 좌표 기반 영역을 PNG로 추출.

    mx/my/mw/mh: Mathpix URL의 top_left_x, top_left_y, width, height (픽셀)
    mathpix_dpi: Mathpix 처리 DPI (광주여고 PDF 기준 ~180)
    padding_pt: 여백 추가 (포인트 단위)
    """
    try:
        import fitz
    except ImportError:
        raise ImportError("PyMuPDF(fitz) 필요: pip install pymupdf")

    doc = fitz.open(str(pdf_path))
    page = doc[page_1idx - 1]
    pr = page.rect

    scale = 72.0 / mathpix_dpi
    x0 = max(0.0,       mx * scale - padding_pt)
    y0 = max(0.0,       my * scale - padding_pt)
    x1 = min(pr.width,  (mx + mw) * scale + padding_pt)
    y1 = min(pr.height, (my + mh) * scale + padding_pt)

    clip = fitz.Rect(x0, y0, x1, y1)
    mat  = fitz.Matrix(render_dpi / 72, render_dpi / 72)
    pix  = page.get_pixmap(matrix=mat, clip=clip)
    pix.save(str(out_path))
    doc.close()
    return out_path
