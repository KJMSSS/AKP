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
        hpf_xml = src_zf.read("Contents/content.hpf").decode("utf-8")

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

        # content.hpf에 BinData 항목 등록
        ext = image_path.suffix.lower().lstrip(".")
        mime = {"png": "image/png", "jpg": "image/jpg", "jpeg": "image/jpg", "bmp": "image/bmp"}.get(ext, "image/png")
        new_item = f'<opf:item id="{bin_stem}" href="BinData/{bin_name}" media-type="{mime}" isEmbeded="1"/>'
        hpf_new = hpf_xml.replace("</opf:manifest>", f"{new_item}</opf:manifest>")

        # section0.xml / content.hpf는 직접 교체 — 원본 복사 건너뜀으로 중복 방지
        _OVERWRITE = {"Contents/section0.xml", "Contents/content.hpf"}
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as dst_zf:
            for item in src_zf.infolist():
                if item.filename not in _OVERWRITE:
                    dst_zf.writestr(item, src_zf.read(item.filename))
            dst_zf.writestr(f"BinData/{bin_name}", image_path.read_bytes())
            dst_zf.writestr("Contents/section0.xml", xml_new.encode("utf-8"))
            dst_zf.writestr("Contents/content.hpf", hpf_new.encode("utf-8"))

    shutil.move(str(tmp_path), str(out_path))
    return out_path


def insert_figure_placeholder(
    hwpx_path: Path,
    item_no: str,
    image_path: Path,
    max_width_hpc: int = 45000,
    ppr: int = 8,
    cpr: int = 0,
    out_path: Path | None = None,
) -> Path:
    """
    【★ 그림:N번】 플레이스홀더 단락을 이미지 단락으로 교체.

    max_width_hpc: 최대 폭 (HWP 단위). 이미지가 크면 이 폭으로 비율 유지 축소.
    """
    from PIL import Image as PILImage

    img = PILImage.open(image_path)
    px_w, px_h = img.size
    # 150dpi 기준: 1pt = 150/72 px → HWP = pt * 100
    dpi = 150
    w_hpc = int(px_w * 72 / dpi * 100)
    h_hpc = int(px_h * 72 / dpi * 100)
    # 폭 초과 시 비율 축소
    if w_hpc > max_width_hpc:
        ratio = max_width_hpc / w_hpc
        w_hpc = max_width_hpc
        h_hpc = int(h_hpc * ratio)

    out_path = out_path or hwpx_path
    tmp_path = hwpx_path.with_suffix(".fig_tmp.hwpx")

    with zipfile.ZipFile(hwpx_path, "r") as src_zf:
        bin_num, bin_stem = _next_bin_id(src_zf)
        bin_name = f"{bin_stem}{image_path.suffix.lower()}"
        xml = src_zf.read("Contents/section0.xml").decode("utf-8")
        hpf_xml = src_zf.read("Contents/content.hpf").decode("utf-8")

        existing_ids = [int(m) for m in re.findall(r'<hp:p id="(\d+)"', xml)]
        new_pid = (max(existing_ids) + 1) if existing_ids else 300
        existing_zo = [int(m) for m in re.findall(r'zOrder="(\d+)"', xml)]
        new_zo = (max(existing_zo) + 1) if existing_zo else 300

        pic_xml = _make_pic_xml(bin_stem, w_hpc, h_hpc, oid=new_pid + 1000000, zo=new_zo)
        bl = round(h_hpc * 0.85)
        pic_para = _PIC_PARA.format(
            pid=new_pid, ppr=ppr, cpr=cpr,
            pic_xml=pic_xml, h=h_hpc, bl=bl, tw=w_hpc,
        )

        # 【★ 그림:N번】 플레이스홀더 단락 찾아 교체
        placeholder_text = f"【★ 그림:{item_no}번】"
        # 한글 t 태그 내에서 찾음
        pattern = re.compile(
            r'<hp:p\b[^>]*>(?:(?!</hp:p>)[\s\S])*'
            + re.escape(placeholder_text)
            + r'(?:(?!</hp:p>)[\s\S])*</hp:p>',
        )
        xml_new, n = pattern.subn(pic_para, xml, count=1)
        if n == 0:
            print(f"  [그림] 경고: {item_no}번 플레이스홀더 미발견 — 삽입 건너뜀")
        else:
            print(f"  [그림] {item_no}번 → {bin_name} ({w_hpc//100:.0f}×{h_hpc//100:.0f}pt)")

        ext = image_path.suffix.lower().lstrip(".")
        mime = {"png": "image/png", "jpg": "image/jpg", "jpeg": "image/jpg"}.get(ext, "image/png")
        new_item = f'<opf:item id="{bin_stem}" href="BinData/{bin_name}" media-type="{mime}" isEmbeded="1"/>'
        hpf_new = hpf_xml.replace("</opf:manifest>", f"{new_item}</opf:manifest>")

        _OVERWRITE = {"Contents/section0.xml", "Contents/content.hpf"}
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as dst_zf:
            for item in src_zf.infolist():
                if item.filename not in _OVERWRITE:
                    dst_zf.writestr(item, src_zf.read(item.filename))
            dst_zf.writestr(f"BinData/{bin_name}", image_path.read_bytes())
            dst_zf.writestr("Contents/section0.xml", xml_new.encode("utf-8"))
            dst_zf.writestr("Contents/content.hpf", hpf_new.encode("utf-8"))

    shutil.move(str(tmp_path), str(out_path))
    return out_path


def strip_figure_placeholders(
    hwpx_path: Path,
    item_nos: list[str],
    out_path: Path | None = None,
) -> list[str]:
    """【★ 그림:N번】 마커 텍스트 제거 — skipped 결정(그림 없음) 반영.

    마커 문자열만 지우고 단락은 보존한다 (본문이 합쳐진 단락 안전).
    제거된 마커의 문제 번호 목록을 반환.
    """
    if not item_nos:
        return []
    out_path = out_path or hwpx_path
    tmp_path = hwpx_path.with_suffix(".strip_tmp.hwpx")

    with zipfile.ZipFile(hwpx_path, "r") as src_zf:
        xml = src_zf.read("Contents/section0.xml").decode("utf-8")
        removed: list[str] = []
        for no in item_nos:
            marker = f"【★ 그림:{no}번】"
            if marker in xml:
                xml = xml.replace(marker, "")
                removed.append(no)
        if not removed:
            return []
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as dst_zf:
            for item in src_zf.infolist():
                if item.filename != "Contents/section0.xml":
                    dst_zf.writestr(item, src_zf.read(item.filename))
            dst_zf.writestr("Contents/section0.xml", xml.encode("utf-8"))

    shutil.move(str(tmp_path), str(out_path))
    return removed


def apply_figure_decisions(hwpx_path: Path, items: dict) -> dict:
    """그림 검수 큐 결정을 빌드 직후 HWPX에 반영.

    전제: 【★ 그림:N번】 플레이스홀더가 살아있는 HWPX (build_from_markdown 직후).
    items: figure_queue items.json의 items dict ({prob_no: {status, *_path, ...}})

    - manual_selected → manual_path 삽입
    - auto_selected / pending → auto_path 삽입 (없으면 플레이스홀더 유지)
    - skipped → 마커 제거 (그림 없음 처리)

    반환: {"manual": n, "auto": n, "skipped": n, "missing": n,
           "no_placeholder": n, "failed": n,
           "applied_nos": [...], "skipped_nos": [...]} 반영 통계.
    applied_nos/skipped_nos는 실제 반영에 성공한 문제 번호 —
    호출자가 applied_at 마킹에 사용한다.
    """
    counts: dict = {
        "manual": 0, "auto": 0, "skipped": 0,
        "missing": 0, "no_placeholder": 0, "failed": 0,
        "applied_nos": [], "skipped_nos": [],
    }

    with zipfile.ZipFile(hwpx_path, "r") as zf:
        xml = zf.read("Contents/section0.xml").decode("utf-8")

    skip_nos: list[str] = []
    for prob_no in sorted(items, key=lambda x: int(x) if x.isdigit() else 999):
        entry = items[prob_no]
        status = entry.get("status", "pending")

        if status == "skipped":
            skip_nos.append(prob_no)
            continue

        if f"【★ 그림:{prob_no}번】" not in xml:
            counts["no_placeholder"] += 1
            print(f"  [그림반영] {prob_no}번 플레이스홀더 없음 — 건너뜀")
            continue

        if status == "manual_selected":
            raw = entry.get("manual_path")
        else:  # auto_selected / pending — 자동 결과 최선 적용 (첫 빌드와 동일 동작)
            raw = entry.get("auto_path")

        if raw and Path(raw).exists():
            # 항목별 격리 — 손상 이미지 1건이 나머지 반영을 막지 않도록
            try:
                insert_figure_placeholder(hwpx_path, prob_no, Path(raw))
                counts["manual" if status == "manual_selected" else "auto"] += 1
                counts["applied_nos"].append(prob_no)
            except Exception as e:
                counts["failed"] += 1
                hwpx_path.with_suffix(".fig_tmp.hwpx").unlink(missing_ok=True)
                print(f"  [그림반영] {prob_no}번 삽입 실패 — 건너뜀: {e}")
        else:
            counts["missing"] += 1
            print(f"  [그림반영] {prob_no}번 이미지 파일 없음({status}) — 플레이스홀더 유지")

    counts["skipped_nos"] = strip_figure_placeholders(hwpx_path, skip_nos)
    counts["skipped"] = len(counts["skipped_nos"])
    return counts


def crop_figure_from_pdf(
    pdf_path: Path,
    page_1idx: int,
    out_path: Path,
    pt_rect: tuple[float, float, float, float] | None = None,
    render_dpi: int = 300,
    rotate_deg: int = 0,
) -> Path:
    """
    PDF 페이지에서 그림 영역을 PNG로 추출.

    pt_rect: (x0, y0, x1, y1) in PDF pt 단위 (PyMuPDF 표시 좌표계)
    rotate_deg: 추출 후 회전 각도 (0, 90, 180, 270). 뒤집힌 페이지 보정용.
    """
    try:
        import fitz
    except ImportError:
        raise ImportError("PyMuPDF(fitz) 필요: pip install pymupdf")

    doc = fitz.open(str(pdf_path))
    page = doc[page_1idx - 1]
    pr = page.rect

    if pt_rect is not None:
        x0, y0, x1, y1 = pt_rect
    else:
        x0, y0, x1, y1 = pr.x0, pr.y0, pr.x1, pr.y1

    x0 = max(0.0, x0)
    y0 = max(0.0, y0)
    x1 = min(pr.width,  x1)
    y1 = min(pr.height, y1)

    clip = fitz.Rect(x0, y0, x1, y1)
    mat  = fitz.Matrix(render_dpi / 72, render_dpi / 72)
    pix  = page.get_pixmap(matrix=mat, clip=clip)

    if rotate_deg != 0:
        try:
            import PIL.Image, io
            img = PIL.Image.open(io.BytesIO(pix.tobytes("png")))
            img.rotate(rotate_deg, expand=True).save(str(out_path))
        except ImportError:
            pix.save(str(out_path))
            print("  [pic] Pillow 미설치 — 회전 건너뜀")
    else:
        pix.save(str(out_path))

    doc.close()
    return out_path
