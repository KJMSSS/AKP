"""
HWPX 그림 삽입 — BinData/ 폴더에 이미지 추가 후 section XML에 그림 참조 삽입.

HWPX ZIP 구조:
  BinData/BIN0001.png  (이미지 파일)
  Contents/section0.xml (문서 본문)

section XML 삽입 위치: 지정 문항 번호 단락 바로 뒤의 빈 단락에 그림 삽입.

주의:
  - 기존 BinData ID 최대값 + 1로 새 이미지 ID 할당
  - HWPX 스펙상 width/height는 EMU (1pt = 12700 EMU) 또는 HWP 단위
  - 이 모듈은 단순 "그림 삽입 자리 확보" 수준 (정밀 레이아웃은 수동)
"""
from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path


_BIN_RE  = re.compile(r"BIN(\d{4,})\.", re.IGNORECASE)
_ITEM_NO_XML_RE = re.compile(r"<hp:t[^>]*>(\d{1,2})[.．]")

# 그림 삽입용 최소 XML 스니펫 (단락 안에 그림 오브젝트)
_PICTURE_PARA_TEMPLATE = """\
<hp:p>
  <hp:pPr>
    <hp:pStyle styleIDRef="0"/>
    <hp:lineSpacing type="leading" value="160"/>
  </hp:pPr>
  <hp:run>
    <hp:secPr/>
    <hp:picture>
      <hp:sz width="{width_hpc}" height="{height_hpc}"/>
      <hp:imgRef binItemIDRef="{bin_id}"/>
    </hp:picture>
  </hp:run>
</hp:p>"""


def _next_bin_id(zf: zipfile.ZipFile) -> int:
    """BinData/ 내 최대 BIN 번호 + 1."""
    ids = [
        int(m.group(1))
        for name in zf.namelist()
        for m in [_BIN_RE.search(name)]
        if m
    ]
    return (max(ids) + 1) if ids else 1


def _bin_name(bin_id: int, suffix: str) -> str:
    return f"BIN{bin_id:04d}{suffix}"


def insert_image(
    hwpx_path: Path,
    image_path: Path,
    item_no: str,
    width_pt: float = 200.0,
    height_pt: float = 150.0,
    out_path: Path | None = None,
) -> Path:
    """
    HWPX에 그림을 삽입하고 새 HWPX를 저장.

    item_no: 삽입 위치 기준 문항 번호 ("3" 등)
    width_pt / height_pt: 그림 크기 (포인트)
    out_path: None이면 원본 파일 덮어쓰기

    반환: 저장된 HWPX 경로
    """
    out_path = out_path or hwpx_path
    tmp_path = hwpx_path.with_suffix(".tmp.hwpx")

    # 1pt = 100 HPC (HWP coordinate) — HWP 단위계
    width_hpc  = int(width_pt  * 100)
    height_hpc = int(height_pt * 100)

    with zipfile.ZipFile(hwpx_path, "r") as src_zf:
        bin_id  = _next_bin_id(src_zf)
        bin_name = _bin_name(bin_id, image_path.suffix.lower())
        xml      = src_zf.read("Contents/section0.xml").decode("utf-8")

        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as dst_zf:
            # 기존 파일 복사
            for item in src_zf.infolist():
                dst_zf.writestr(item, src_zf.read(item.filename))

            # 이미지 추가
            dst_zf.writestr(f"BinData/{bin_name}", image_path.read_bytes())

            # XML 수정: 해당 문항 번호 단락 뒤에 그림 단락 삽입
            picture_xml = _PICTURE_PARA_TEMPLATE.format(
                width_hpc=width_hpc,
                height_hpc=height_hpc,
                bin_id=bin_id,
            )
            xml_modified = _insert_after_item(xml, item_no, picture_xml)
            dst_zf.writestr("Contents/section0.xml", xml_modified.encode("utf-8"))

    shutil.move(str(tmp_path), str(out_path))
    return out_path


def _insert_after_item(xml: str, item_no: str, picture_xml: str) -> str:
    """
    item_no 번호가 있는 단락 바로 뒤에 picture_xml을 삽입.
    단락 경계: </hp:p> 태그.
    """
    pattern = re.compile(
        r"(<hp:t[^>]*>" + re.escape(item_no) + r"[.．][^<]*</hp:t>[\s\S]*?</hp:p>)"
    )
    m = pattern.search(xml)
    if not m:
        # 위치를 못 찾으면 section 끝에 추가
        xml = xml.replace("</hh:section>", picture_xml + "\n</hh:section>")
        return xml

    insert_pos = m.end()
    return xml[:insert_pos] + "\n" + picture_xml + xml[insert_pos:]
