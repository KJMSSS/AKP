"""
PDF 페이지 회전 보정.

스캔 방식에 따라 짝수 페이지가 뒤집힌 PDF를 보정한다.
원본 파일은 절대 수정하지 않고 새 파일로 저장.
"""
from pathlib import Path

import pikepdf


def fix_alternating_180(
    input_pdf: str | Path,
    output_pdf: str | Path,
    swap_pairs: list[tuple[int, int]] | None = None,
) -> dict:
    """짝수 페이지(1-indexed)를 180° 회전하고, 필요 시 지정 페이지 쌍을 교환.

    Args:
        swap_pairs: [(a, b), ...] 형태의 1-indexed 페이지 번호 쌍. 회전 후 순서 교환.

    Returns:
        {
            "total_pages": int,
            "flipped_pages": list[int],
            "swapped_pairs": list[tuple[int,int]],
            "input": str,
            "output": str,
        }
    """
    input_path  = Path(input_pdf)
    output_path = Path(output_pdf)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    flipped: list[int] = []
    with pikepdf.open(input_path) as pdf:
        # 짝수 페이지 180° 회전
        for i, page in enumerate(pdf.pages, 1):
            if i % 2 == 0:
                page.rotate(180, relative=True)
                flipped.append(i)

        # 페이지 순서 교환 (1-indexed → 0-indexed 변환)
        swapped: list[tuple[int, int]] = []
        if swap_pairs:
            for a, b in swap_pairs:
                a0, b0 = a - 1, b - 1
                pdf.pages[a0], pdf.pages[b0] = pdf.pages[b0], pdf.pages[a0]
                swapped.append((a, b))

        pdf.save(output_path)
        total = len(pdf.pages)

    return {
        "total_pages": total,
        "flipped_pages": flipped,
        "swapped_pairs": swapped,
        "input":  str(input_path),
        "output": str(output_path),
    }


def fix_all_pages(
    input_pdf: str | Path,
    output_pdf: str | Path,
    angle: int,
) -> dict:
    """모든 페이지를 동일 각도로 회전.

    Args:
        angle: 회전 각도 (양수=반시계, 음수=시계 방향). 예: -90 = 우90°(시계방향).
    """
    input_path  = Path(input_pdf)
    output_path = Path(output_pdf)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with pikepdf.open(input_path) as pdf:
        for page in pdf.pages:
            page.rotate(angle, relative=True)
        pdf.save(output_path)
        total = len(pdf.pages)

    return {
        "total_pages": total,
        "angle": angle,
        "input":  str(input_path),
        "output": str(output_path),
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python pdf_rotation.py <input.pdf> <output.pdf>")
        sys.exit(1)
    result = fix_alternating_180(sys.argv[1], sys.argv[2])
    print(f"완료: 총 {result['total_pages']}페이지")
    print(f"회전 적용: {result['flipped_pages']}")
    print(f"출력: {result['output']}")
