"""
HWPX 빌더 — 템플릿 치환 방식

전략: 기존 .hwpx(워드초벌)를 ZIP으로 열고,
section0.xml 안의 hp:script 내용만 교체해서 새 파일을 저장한다.
나머지 모든 구조(스타일, 단락, 메타데이터)는 그대로 보존.
"""
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.hwpx.latex_to_hwp import convert as latex_to_hwp
from src.ocr.mathpix_client import OcrResult

# section0.xml 경로 (HWPX 표준 위치)
_SECTION = "Contents/section0.xml"

# 빈 hp:script: 내용 없음 또는 공백만
_EMPTY_SCRIPT_RE = re.compile(r'<hp:script(?:\s*/|>\s*</hp:script)>')

# 채워진 hp:script 포함: <hp:script>...</hp:script>
_ANY_SCRIPT_RE = re.compile(r'<hp:script>(.*?)</hp:script>', re.DOTALL)


# ── 공개 데이터 클래스 ────────────────────────────────────────────

@dataclass
class FillResult:
    output_path: Path
    filled: int          # 교체된 hp:script 수
    total_scripts: int   # 템플릿의 전체 hp:script 수
    total_formulas: int  # OcrResult의 formula 블록 수
    skipped: int         # 공식 부족으로 못 채운 빈 슬롯 수


# ── 내부 헬퍼 ────────────────────────────────────────────────────

def _extract_zip(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path, "r") as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def _pack_zip(path: Path, files: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)


def _formula_blocks(result: OcrResult) -> list[str]:
    """OcrResult에서 수식 블록만 추출해 HWP script로 변환한다."""
    hwp_scripts: list[str] = []
    for block in result.blocks:
        if block.kind in ("formula_inline", "formula_display"):
            hwp_scripts.append(latex_to_hwp(block.content))
    return hwp_scripts


def _fill_empty(xml: str, formulas: list[str]) -> tuple[str, int, int]:
    """
    빈 hp:script를 순서대로 채운다.
    반환: (수정된 xml, 채워진 수, 못 채운 수)
    """
    idx = 0
    skipped = 0
    total_empty = len(_EMPTY_SCRIPT_RE.findall(xml))

    def replacer(_m: re.Match) -> str:
        nonlocal idx, skipped
        if idx < len(formulas):
            script = formulas[idx]
            idx += 1
            return f'<hp:script>{_xml_escape(script)}</hp:script>'
        skipped += 1
        return f'<hp:script/>'

    result = _EMPTY_SCRIPT_RE.sub(replacer, xml)
    return result, idx, skipped


_SELF_CLOSE_SCRIPT_RE = re.compile(r'<hp:script\s*/>')

def _fill_all(xml: str, formulas: list[str]) -> tuple[str, int, int]:
    """
    모든 hp:script를 순서대로 교체한다 (기존 내용 덮어쓰기).
    반환: (수정된 xml, 채워진 수, 못 채운 수)
    """
    # self-closing <hp:script/> → <hp:script></hp:script> 로 정규화
    xml = _SELF_CLOSE_SCRIPT_RE.sub('<hp:script></hp:script>', xml)

    idx = 0
    skipped = 0

    def replacer(_m: re.Match) -> str:
        nonlocal idx, skipped
        if idx < len(formulas):
            script = formulas[idx]
            idx += 1
            return f'<hp:script>{_xml_escape(script)}</hp:script>'
        skipped += 1
        return _m.group()

    result = _ANY_SCRIPT_RE.sub(replacer, xml)
    return result, idx, skipped


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))


def _count_scripts(xml: str) -> int:
    return len(_ANY_SCRIPT_RE.findall(xml)) + len(_EMPTY_SCRIPT_RE.findall(xml))


# ── 공개 API ─────────────────────────────────────────────────────

def fill_template(
    template_path: Path,
    ocr_result: OcrResult,
    output_path: Path,
    *,
    replace_all: bool = False,
) -> FillResult:
    """
    워드초벌 .hwpx 템플릿의 hp:script를 OCR 수식으로 채운다.

    Args:
        template_path: 기존 .hwpx 파일 (구조 보존용 템플릿)
        ocr_result:    Mathpix OCR 결과 (formula 블록 사용)
        output_path:   저장할 .hwpx 경로
        replace_all:   False(기본) → 빈 script만 채움
                       True → 모든 script 순서대로 덮어쓰기

    Returns:
        FillResult (채운 수, 전체 수 등 요약)
    """
    files = _extract_zip(template_path)

    if _SECTION not in files:
        raise ValueError(f"템플릿에 {_SECTION}이 없습니다: {template_path}")

    xml = files[_SECTION].decode("utf-8")
    formulas = _formula_blocks(ocr_result)
    total_scripts = _count_scripts(xml)

    if replace_all:
        xml_new, filled, skipped = _fill_all(xml, formulas)
    else:
        xml_new, filled, skipped = _fill_empty(xml, formulas)

    files[_SECTION] = xml_new.encode("utf-8")
    _pack_zip(output_path, files)

    return FillResult(
        output_path   = output_path,
        filled        = filled,
        total_scripts = total_scripts,
        total_formulas= len(formulas),
        skipped       = skipped,
    )


def count_empty_scripts(hwpx_path: Path) -> int:
    """템플릿의 빈 hp:script 수를 반환한다 (채울 슬롯 확인용)."""
    files = _extract_zip(hwpx_path)
    xml = files.get(_SECTION, b"").decode("utf-8")
    return len(_EMPTY_SCRIPT_RE.findall(xml))


def count_all_scripts(hwpx_path: Path) -> int:
    """템플릿의 전체 hp:script 수를 반환한다."""
    files = _extract_zip(hwpx_path)
    xml = files.get(_SECTION, b"").decode("utf-8")
    return _count_scripts(xml)
