"""
수학 그림 재생성 모듈 (D안 3차 패스).

흐름:
  1. 문제 크롭 PNG + figure_bbox(%) → 그림 영역만 크롭
  2. Claude Vision에 그림 분석 + matplotlib 코드 생성 요청
  3. 생성된 코드 실행 → PNG 저장
  4. 실패 시: 원본 크롭 이미지 그대로 사용 (fallback)

사용:
  from src.ocr.figure_reconstructor import reconstruct_figure
  png = reconstruct_figure(crop_png, [20, 40, 80, 90], "9", out_dir, client)
"""
from __future__ import annotations

import ast
import base64
import io
import re
import textwrap
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import anthropic

# ── 그림 크롭 ─────────────────────────────────────────────────────────────────

def _crop_figure(crop_png: Path, bbox_pct: list[float]) -> bytes:
    """
    문제 크롭 PNG에서 figure_bbox 영역만 추출.
    bbox_pct: [left%, top%, right%, bottom%]
    """
    from PIL import Image
    img = Image.open(crop_png)
    w, h = img.size
    l = int(bbox_pct[0] / 100 * w)
    t = int(bbox_pct[1] / 100 * h)
    r = int(bbox_pct[2] / 100 * w)
    b = int(bbox_pct[3] / 100 * h)
    # 최소 크기 보장
    r = max(r, l + 10)
    b = max(b, t + 10)
    fig = img.crop((l, t, r, b))
    buf = io.BytesIO()
    fig.save(buf, format='PNG')
    return buf.getvalue()


# ── 코드 생성 프롬프트 ─────────────────────────────────────────────────────────

_FIG_PROMPT = """\
이 이미지는 한국 수학 시험지 {num}번 문제의 그림(그래프, 도형, 좌표계 등)입니다.

이 그림을 Python matplotlib로 재현하는 코드를 작성해라.

요구사항:
1. `import matplotlib.pyplot as plt` 와 필요한 라이브러리만 사용
2. 코드 마지막에 반드시 `plt.savefig(OUTPUT_PATH, dpi=150, bbox_inches='tight')` 포함
   (OUTPUT_PATH 변수는 이미 정의됨 — 변경 금지)
3. `plt.show()` 금지
4. 축 레이블, 눈금, 텍스트 등 시험지 원본과 최대한 유사하게
5. 한글 폰트 필요 시: `plt.rcParams['font.family'] = 'Malgun Gothic'` 추가
6. 코드만 출력 (설명 없이)

```python
# matplotlib 코드
```
"""


def _generate_code(
    figure_bytes: bytes,
    problem_no: str,
    client: 'anthropic.Anthropic',
    model: str = "claude-sonnet-4-6",
) -> str:
    """Claude Vision으로 matplotlib 코드 생성."""
    b64 = base64.standard_b64encode(figure_bytes).decode()
    resp = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                {"type": "text",  "text": _FIG_PROMPT.replace('{num}', problem_no)},
            ],
        }],
    )
    raw = resp.content[0].text
    # ```python ... ``` 블록 추출
    m = re.search(r'```python\s*([\s\S]+?)```', raw)
    return m.group(1).strip() if m else raw.strip()


# ── 코드 실행 ─────────────────────────────────────────────────────────────────

def _is_safe_code(code: str) -> bool:
    """기초 안전성 검사 — import os, subprocess, open 등 위험 패턴 차단."""
    forbidden = ['subprocess', 'os.system', 'eval(', 'exec(', '__import__',
                 'open(', 'shutil', 'socket', 'urllib', 'requests']
    code_lower = code.lower()
    return not any(kw in code_lower for kw in forbidden)


def _execute_code(code: str, output_path: Path) -> bool:
    """
    matplotlib 코드 실행 후 output_path에 PNG 저장.
    반환: 성공 여부
    """
    if not _is_safe_code(code):
        print("    [figure] 코드 안전성 검사 실패 — 실행 건너뜀")
        return False

    # OUTPUT_PATH 변수 주입
    injected = f'OUTPUT_PATH = r"{output_path}"\n' + code

    try:
        # AST 파싱으로 문법 확인
        ast.parse(injected)
    except SyntaxError as e:
        print(f"    [figure] 코드 문법 오류: {e}")
        return False

    try:
        import matplotlib
        matplotlib.use('Agg')   # headless
        exec(compile(injected, '<figure_code>', 'exec'), {})  # noqa: S102
        return output_path.exists()
    except Exception as e:
        print(f"    [figure] 코드 실행 실패: {e}")
        return False


# ── 공개 API ─────────────────────────────────────────────────────────────────

def reconstruct_figure(
    crop_png: Path,
    figure_bbox: list[float],
    problem_no: str,
    output_dir: Path,
    client: 'anthropic.Anthropic',
    force: bool = False,
) -> Path:
    """
    그림 재생성 메인 함수.

    crop_png     : 문제 전체 크롭 PNG
    figure_bbox  : [left%, top%, right%, bottom%]
    problem_no   : "9", "서술형1" 등
    output_dir   : 생성된 PNG 저장 폴더
    client       : anthropic.Anthropic
    force        : True면 캐시 무시하고 재생성

    반환: 생성된 PNG 경로 (실패 시 원본 크롭 그대로 복사)
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_no = str(problem_no).replace('/', '_')
    out_png = output_dir / f"fig_{safe_no}.png"

    # 캐시 확인
    if out_png.exists() and not force:
        print(f"    [figure] {problem_no}번 캐시 사용: {out_png.name}")
        return out_png

    # 그림 영역 크롭
    try:
        fig_bytes = _crop_figure(crop_png, figure_bbox)
    except Exception as e:
        print(f"    [figure] {problem_no}번 크롭 실패({e}) — 원본 크롭 사용")
        import shutil
        shutil.copy2(crop_png, out_png)
        return out_png

    # matplotlib 코드 생성
    print(f"    [figure] {problem_no}번 코드 생성 중...", end=" ", flush=True)
    try:
        code = _generate_code(fig_bytes, str(problem_no), client)
        print(f"{len(code)}자")
    except Exception as e:
        print(f"실패({e}) — 원본 크롭 사용")
        out_png.write_bytes(fig_bytes)
        return out_png

    # 코드 실행
    print(f"    [figure] {problem_no}번 코드 실행 중...", end=" ", flush=True)
    ok = _execute_code(code, out_png)

    if ok:
        kb = out_png.stat().st_size // 1024
        print(f"완료 ({kb}KB)")
    else:
        print("실패 — 원본 크롭 사용")
        out_png.write_bytes(fig_bytes)

    # 코드 저장 (디버그용)
    code_path = output_dir / f"fig_{safe_no}.py"
    code_path.write_text(code, encoding='utf-8')

    return out_png


def reconstruct_all_figures(
    crop_dir: Path,
    vision_results: dict[int, 'VisionOCRResult'],
    output_dir: Path,
    client: 'anthropic.Anthropic',
    force: bool = False,
) -> dict[str, Path]:
    """
    그림 있는 모든 문제에 대해 재생성.
    반환: {item_no: png_path}
    """
    from src.ocr.vision_problem_ocr import VisionOCRResult  # noqa: F401

    figure_map: dict[str, Path] = {}
    for num, result in sorted(vision_results.items()):
        if not result.has_figure or not result.figure_bbox:
            continue
        crop_png = crop_dir / f"prob_{num}.png"
        if not crop_png.exists():
            print(f"    [figure] {num}번 크롭 없음 — 건너뜀")
            continue
        png = reconstruct_figure(
            crop_png, result.figure_bbox, str(num), output_dir, client, force=force
        )
        figure_map[str(num)] = png

    return figure_map
