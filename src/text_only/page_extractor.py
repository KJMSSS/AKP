"""
page_extractor — Mathpix MD / HWPX 페이지 단위 추출 도구.

Mathpix MD의 "페이지" 경계: CDN 이미지 URL의 -N.jpg 패턴.
  예) https://cdn.mathpix.com/cropped/xxxx-1.jpg  → page 1
      https://cdn.mathpix.com/cropped/xxxx-2.jpg  → page 2

HWPX 페이지 분리: 문제 번호 기반 분할 (HWP 렌더 없이 page-break 추적 불가).
  회귀 검증에서는 "문제 번호 N 이후" 단락의 수식 스크립트 집합을 비교.
"""
import re
import zipfile
from pathlib import Path

# CDN 이미지 URL에서 페이지 번호 추출
_CDN_PAGE_RE = re.compile(
    r'!\[\]\(https://cdn\.mathpix\.com/cropped/[^)]*?-(\d+)\.jpg[^)]*\)'
)

# Mathpix 문항 경계
_ITEM_NO_RE = re.compile(r'^(\d{1,2})[.．]', re.MULTILINE)


# ── Mathpix MD 페이지 추출 ──────────────────────────────────────────

def get_md_page_range(md: str) -> dict[int, tuple[int, int]]:
    """
    MD에서 각 CDN 페이지 번호의 첫 등장 위치를 기반으로
    페이지 범위 {page_n: (start_offset, end_offset)} 반환.

    page 1 = MD 시작 ~ page 2 CDN 이미지 직전.
    마지막 페이지 end = len(md).
    """
    # 각 페이지 번호의 첫 번째 CDN 이미지 위치
    first_seen: dict[int, int] = {}
    for m in _CDN_PAGE_RE.finditer(md):
        n = int(m.group(1))
        if n not in first_seen:
            first_seen[n] = m.start()

    if not first_seen:
        return {1: (0, len(md))}

    sorted_pages = sorted(first_seen.items())
    ranges: dict[int, tuple[int, int]] = {}

    # page 1 starts at 0 (before first CDN image)
    # page N starts at the first CDN image of page N
    page_nums = [p for p, _ in sorted_pages]
    page_starts = [pos for _, pos in sorted_pages]

    # page 1 begins at MD start (header before any CDN image)
    all_starts = [0] + page_starts
    all_pages = [1] + page_nums

    for i, (pg, start) in enumerate(zip(all_pages, all_starts)):
        end = all_starts[i + 1] if i + 1 < len(all_starts) else len(md)
        ranges[pg] = (start, end)

    return ranges


def get_md_page(md: str, page: int) -> str:
    """MD에서 page N에 해당하는 텍스트 반환."""
    ranges = get_md_page_range(md)
    if page not in ranges:
        return ""
    start, end = ranges[page]
    return md[start:end]


def get_md_pages_from(md: str, start_page: int) -> str:
    """MD에서 start_page 이후 전체 텍스트 반환 (회귀 비교용)."""
    ranges = get_md_page_range(md)
    if not ranges:
        return md
    min_page = min(ranges.keys())
    if start_page <= min_page:
        return md

    target_pages = [p for p in sorted(ranges.keys()) if p >= start_page]
    if not target_pages:
        return ""

    start, _ = ranges[target_pages[0]]
    return md[start:]


def list_md_items_by_page(md: str) -> dict[int, list[str]]:
    """페이지별 문항 번호 목록 반환: {page_n: ['1', '3', '4', ...]}"""
    ranges = get_md_page_range(md)
    result: dict[int, list[str]] = {}
    for page, (start, end) in ranges.items():
        segment = md[start:end]
        result[page] = _ITEM_NO_RE.findall(segment)
    return result


# ── HWPX 수식 추출 ─────────────────────────────────────────────────

def get_hwpx_xml(hwpx_path: Path) -> str:
    """HWPX에서 section0.xml 내용 반환."""
    with zipfile.ZipFile(hwpx_path) as zf:
        return zf.read("Contents/section0.xml").decode("utf-8")


def get_hwpx_scripts(hwpx_path: Path) -> list[str]:
    """HWPX에서 모든 hp:script 내용 리스트 반환."""
    xml = get_hwpx_xml(hwpx_path)
    return re.findall(r'<hp:script>([^<]+)</hp:script>', xml)


def compare_scripts(
    hwpx_a: Path,
    hwpx_b: Path,
    start_idx: int = 0,
) -> list[dict]:
    """
    두 HWPX의 수식 스크립트를 인덱스 start_idx 이후부터 비교.
    차이가 있는 항목만 반환: [{"idx": N, "a": "...", "b": "..."}]
    """
    scripts_a = get_hwpx_scripts(hwpx_a)[start_idx:]
    scripts_b = get_hwpx_scripts(hwpx_b)[start_idx:]
    diffs = []
    for i, (sa, sb) in enumerate(zip(scripts_a, scripts_b), start=start_idx):
        if sa != sb:
            diffs.append({"idx": i, "a": sa, "b": sb})
    if len(scripts_a) != len(scripts_b):
        diffs.append({
            "idx": -1,
            "a": f"총 {len(scripts_a)+start_idx}개",
            "b": f"총 {len(scripts_b)+start_idx}개",
        })
    return diffs


def diff_hwpx_pages(
    hwpx_v_old: Path,
    hwpx_v_new: Path,
    page1_script_count: int,
) -> dict:
    """
    page1_script_count 이후(page 2~N) 수식이 동일한지 비교.
    반환: {"page2n_identical": bool, "diffs": [...]}
    """
    diffs = compare_scripts(hwpx_v_old, hwpx_v_new, start_idx=page1_script_count)
    return {
        "page2n_identical": len(diffs) == 0,
        "diffs": diffs,
    }
