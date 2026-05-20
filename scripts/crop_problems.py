"""
PDF 페이지를 문제 단위로 크롭.

1. PyMuPDF로 페이지를 고해상도 PNG 렌더링
2. Claude API로 문제 번호 위치(bounding box) 감지
3. 문제별 PNG 크롭 저장

사용법:
    python scripts/crop_problems.py <pdf_path> <page_num(1-based)> [--out <dir>]
"""
from __future__ import annotations
import sys, json, base64, argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

import fitz
import anthropic

DPI     = 300
SCALE   = DPI / 72
CLIENT  = anthropic.Anthropic()
MODEL   = "claude-opus-4-7"


# ── 1. 페이지 → PNG ───────────────────────────────────────────────────────────
def render_page(pdf_path: Path, page_idx: int) -> tuple[bytes, int, int]:
    """PDF 페이지를 PNG bytes + (width, height)로 반환."""
    doc  = fitz.open(str(pdf_path))
    page = doc[page_idx]
    mat  = fitz.Matrix(SCALE, SCALE)
    pix  = page.get_pixmap(matrix=mat)
    png  = pix.tobytes("png")
    doc.close()
    return png, pix.width, pix.height


# ── 2. Vision: 문제 번호 bbox 감지 ───────────────────────────────────────────
_DETECT_PROMPT = """\
이 이미지는 2컬럼 레이아웃의 수학 시험지 페이지입니다.

중요 규칙:
1. 상단 헤더(시험 제목, 학교명, 지시사항, 저작권 문구, 이름/반 기입란)는 완전히 무시하세요.
2. 학생 손글씨 풀이, 빨간 동그라미, 필기는 완전히 무시하세요.
3. 인쇄된 아라비아 숫자로 시작하는 문제(예: "1.", "2.", "3.")만 찾으세요.
4. 서술형이면 "서술형 1" 형식으로 표기하세요.

2컬럼 레이아웃:
- 왼쪽 컬럼(column="left"): 세로로 나열된 문제들
- 오른쪽 컬럼(column="right"): 세로로 나열된 문제들
- 각 컬럼에서 y_start는 문제 번호가 인쇄된 줄, y_end는 다음 문제 시작 직전

y 좌표 기준: 이미지 최상단=0, 최하단=이미지 높이

반드시 아래 JSON 형식으로만 응답하세요 (다른 텍스트 없이):
[
  {"num": "1", "column": "left",  "y_start": 250, "y_end": 600},
  {"num": "2", "column": "left",  "y_start": 600, "y_end": 1100},
  {"num": "3", "column": "right", "y_start": 250, "y_end": 550},
  ...
]"""


def detect_problems(png_bytes: bytes, width: int, height: int) -> list[dict]:
    """Claude vision으로 문제별 bbox 감지."""
    b64 = base64.standard_b64encode(png_bytes).decode()
    msg = CLIENT.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type":       "image",
                    "source":     {"type": "base64", "media_type": "image/png", "data": b64},
                },
                {"type": "text", "text": _DETECT_PROMPT},
            ],
        }],
    )
    raw = msg.content[0].text.strip()
    # JSON 블록 추출
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)


# ── 3. 크롭 & 저장 ────────────────────────────────────────────────────────────
def crop_problems(
    pdf_path: Path,
    page_idx: int,
    out_dir: Path,
) -> list[Path]:
    """문제별 PNG를 out_dir에 저장하고 경로 목록 반환."""
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"  [render] {pdf_path.name}  page {page_idx+1}  @{DPI}dpi")
    png_bytes, W, H = render_page(pdf_path, page_idx)
    print(f"  크기: {W}×{H}px")

    # 전체 페이지 PNG 저장
    full_png = out_dir / f"page{page_idx+1}_full.png"
    full_png.write_bytes(png_bytes)

    print(f"  [vision] 문제 bbox 감지 중...")
    problems = detect_problems(png_bytes, W, H)
    print(f"  감지된 문제: {[p['num'] for p in problems]}")

    # 컬럼 x 범위 자동 계산 (중앙선 = W/2)
    mid = W // 2
    col_x = {"left": (0, mid - 20), "right": (mid + 20, W)}

    saved: list[Path] = []
    for p in problems:
        num    = p["num"]
        col    = p.get("column", "left")
        y0     = max(0, int(p["y_start"]) - 20)   # 여백 20px
        y1     = min(H, int(p["y_end"])   + 20)
        x0, x1 = col_x.get(col, (0, W))

        # PyMuPDF로 해당 영역만 재렌더링
        doc  = fitz.open(str(pdf_path))
        page = doc[page_idx]
        # PDF 좌표계로 변환 (px / SCALE = pt)
        rect = fitz.Rect(x0/SCALE, y0/SCALE, x1/SCALE, y1/SCALE)
        mat  = fitz.Matrix(SCALE, SCALE)
        pix  = page.get_pixmap(matrix=mat, clip=rect)
        doc.close()

        out_png = out_dir / f"prob_{num.replace(' ', '_')}.png"
        pix.save(str(out_png))
        saved.append(out_png)
        print(f"    {num}번: col={col} y={y0}~{y1}  → {out_png.name}  ({pix.width}×{pix.height})")

    # bbox JSON 저장
    (out_dir / "bbox.json").write_text(
        json.dumps(problems, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return saved


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf",  help="PDF 경로")
    ap.add_argument("page", type=int, help="페이지 번호 (1-based)")
    ap.add_argument("--out", default=None, help="출력 폴더")
    args = ap.parse_args()

    pdf  = Path(args.pdf)
    pidx = args.page - 1
    out  = Path(args.out) if args.out else pdf.parent / f"{pdf.stem}_crops_p{args.page}"

    paths = crop_problems(pdf, pidx, out)
    print(f"\n저장 완료: {len(paths)}개 → {out}")


if __name__ == "__main__":
    main()
