"""HWPX 검수 마커 빨강 강조.

【★ 확인 필요】·【★ 본문 손상 …】 등 OCR이 '사람이 PDF 보고 채울 곳'으로
남긴 마커를 출력 HWPX에서 빨간 글자로 칠해 타이피스트가 놓치지 않게 한다.

- 헤더에 빨강 charPr(검정 charPr id=0 복제 + textColor=#FF0000) 1개 추가
- 본문 run의 hp:t 안 마커를 빨강 run으로 분리 (run 단위 색상 적용)
"""
from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

_MARKER_RE = re.compile(r'(【★[^】]*】)')
# run-open + 마커 포함 첫 hp:t (run 뒤에 수식 등 다른 콘텐츠가 와도 매칭)
_RUN_T_RE = re.compile(r'<hp:run charPrIDRef="(\d+)"><hp:t>([^<]*【★[^<]*)</hp:t>')


def highlight_markers(hwpx_path: Path, out_path: Path | None = None) -> int:
    """검수 마커를 빨강으로 강조. 강조한 마커 수 반환 (없으면 0, 파일 미변경)."""
    hwpx_path = Path(hwpx_path)
    out_path = Path(out_path) if out_path else hwpx_path

    with zipfile.ZipFile(hwpx_path, "r") as zf:
        section = zf.read("Contents/section0.xml").decode("utf-8")
        if "【★" not in section:
            return 0
        header = zf.read("Contents/header.xml").decode("utf-8")
        others = {
            n: zf.read(n) for n in zf.namelist()
            if n not in ("Contents/section0.xml", "Contents/header.xml")
        }

    # ── 빨강 charPr 생성 (검정 charPr id=0 복제) ──────────────────────────
    cm = re.search(r'<hh:charPr id="0".*?</hh:charPr>', header, re.DOTALL)
    if not cm:
        return 0
    ids = [int(x) for x in re.findall(r'<hh:charPr id="(\d+)"', header)]
    red_id = (max(ids) + 1) if ids else 100
    red_charpr = (cm.group(0)
                  .replace('id="0"', f'id="{red_id}"', 1)
                  .replace('textColor="#000000"', 'textColor="#FF0000"', 1))
    if 'textColor="#FF0000"' not in red_charpr:  # id=0이 검정이 아니면 강제 주입
        red_charpr = re.sub(r'(<hh:charPr id="\d+")', r'\1 textColor="#FF0000"',
                            red_charpr, count=1)
    header = header.replace("</hh:charProperties>", red_charpr + "</hh:charProperties>", 1)
    header = re.sub(
        r'(<hh:charProperties\b[^>]*\bitemCnt=")(\d+)(")',
        lambda m: f'{m.group(1)}{int(m.group(2)) + 1}{m.group(3)}',
        header, count=1)

    # ── 본문: 마커를 빨강 run으로 분리 ────────────────────────────────────
    count = [0]

    def _split(mr: re.Match) -> str:
        cpr = mr.group(1)
        text = mr.group(2)
        if "【★" not in text:
            return mr.group(0)
        pieces = []
        for seg in _MARKER_RE.split(text):
            if seg == "":
                continue
            if _MARKER_RE.fullmatch(seg):
                count[0] += 1
                pieces.append(f'<hp:run charPrIDRef="{red_id}"><hp:t>{seg}</hp:t></hp:run>')
            else:
                pieces.append(f'<hp:run charPrIDRef="{cpr}"><hp:t>{seg}</hp:t></hp:run>')
        # 원래 run의 나머지(수식 등 </hp:t> 뒤 콘텐츠)를 담을 빈 open run (원래 색 복원)
        pieces.append(f'<hp:run charPrIDRef="{cpr}"><hp:t></hp:t>')
        return "".join(pieces)

    section = _RUN_T_RE.sub(_split, section)
    if count[0] == 0:
        return 0

    tmp = hwpx_path.with_suffix(".hl_tmp.hwpx")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zo:
        for n, b in others.items():
            zo.writestr(n, b)
        zo.writestr("Contents/header.xml", header.encode("utf-8"))
        zo.writestr("Contents/section0.xml", section.encode("utf-8"))
    shutil.move(str(tmp), str(out_path))
    return count[0]
