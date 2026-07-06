"""
그림 AI 재작도 — 크롭 영역을 matplotlib 코드로 정밀 재현(②번 폴백).

흐름: 크롭 이미지 -> Claude(figure_to_matplotlib) -> matplotlib 코드 -> 샌드박스 렌더 -> PNG.
수학 그림은 생성형 이미지가 부정확하므로 '구조를 코드로 복원'해 다시 그린다.

보안: 모델이 만든 코드를 제한된 서브프로세스에서만 실행한다.
  - 위험 토큰(import os/sys/subprocess, open, eval, exec, __import__, socket 등) 차단
  - 고정 프리앰블(Agg backend, np/plt/math만)에서 실행, 타임아웃, 네트워크 코드 차단
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

from .vision_claude import figure_to_matplotlib, verify_redraw

_FORBIDDEN = re.compile(
    r"\b(import|__import__|eval|exec|open|compile|globals|locals|getattr|setattr|"
    r"os|sys|subprocess|socket|shutil|pathlib|requests|urllib|input|breakpoint)\b"
    r"|__\w+__|\bopen\s*\(")

_PREAMBLE = (
    "import matplotlib\n"
    "matplotlib.use('Agg')\n"
    "import matplotlib.pyplot as plt\n"
    "plt.rcParams['font.family'] = ['AppleGothic', 'Apple SD Gothic Neo', 'DejaVu Sans']\n"
    "plt.rcParams['axes.unicode_minus'] = False\n"   # 한글 라벨 □□ 깨짐 방지
    "import numpy as np\n"
    "import math\n"
    "from matplotlib.patches import Circle, Polygon, Rectangle, Arc, FancyArrow\n"
)


class RedrawError(RuntimeError):
    pass


def _sanitize(code: str) -> str:
    # 모델이 import 를 넣었으면 줄 제거(프리앰블이 제공)
    lines = [ln for ln in code.splitlines()
             if not re.match(r"\s*(import|from)\s+", ln)
             and "plt.show" not in ln and "savefig" not in ln]
    cleaned = "\n".join(lines)
    bad = _FORBIDDEN.search(cleaned)
    if bad:
        raise RedrawError(f"안전하지 않은 코드 토큰: {bad.group(0)!r}")
    return cleaned


def render_matplotlib(code: str, out_path: str, *, timeout: int = 20) -> str:
    body = _sanitize(code)
    script = (
        _PREAMBLE
        + body
        + f"\nplt.savefig(r'''{out_path}''', dpi=200, bbox_inches='tight')\n"
        + "plt.close('all')\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(script)
        runner = f.name
    try:
        r = subprocess.run([sys.executable, runner], capture_output=True,
                           text=True, timeout=timeout)
    finally:
        Path(runner).unlink(missing_ok=True)
    if r.returncode != 0 or not Path(out_path).exists():
        raise RedrawError(f"렌더 실패: {r.stderr[-400:]}")
    return out_path


def redraw_figure(image_path: str, out_path: str, *, model: str | None = None,
                  return_code: bool = False):
    """크롭 그림 -> AI matplotlib 재작도 PNG. (실패 시 RedrawError)"""
    code = figure_to_matplotlib(image_path, model=model)
    render_matplotlib(code, out_path)
    return (out_path, code) if return_code else out_path


def redraw_figure_verified(image_path: str, out_path: str, *, model: str | None = None,
                           max_retries: int = 3, min_score: int = 80):
    """재작도 + VLM 검증 루프('에러 시 VLM 최종 완성').

    크롭 → matplotlib 코드 생성 → 렌더 → Claude 비전이 원본과 비교 채점.
    렌더 실패나 충실도 미달이면 '차이점 피드백'을 주고 코드를 재생성(최대 max_retries회).
    통과 시 (out_path, verdict) 반환, 최종 실패 시 RedrawError → 상위가 인페인팅 크롭으로 폴백.
    """
    code = figure_to_matplotlib(image_path, model=model)
    verdict = {"score": 0, "ok": False, "issues": ["미실행"]}
    for attempt in range(max_retries + 1):
        try:
            render_matplotlib(code, out_path)
        except RedrawError as e:
            if attempt >= max_retries:
                raise
            code = figure_to_matplotlib(image_path, model=model, prev_code=code,
                                        issues=[f"렌더 에러: {str(e)[:200]}"])
            continue
        verdict = verify_redraw(image_path, out_path, model=model)
        if verdict.get("ok") and verdict.get("score", 0) >= min_score:
            return out_path, verdict
        if attempt < max_retries:   # 충실도 미달 → 차이점 피드백으로 재생성
            code = figure_to_matplotlib(image_path, model=model, prev_code=code,
                                        issues=verdict.get("issues", []))
    # 모든 시도 후 검증 미달이어도 '재작도'를 채택한다 — 크롭 폴백(낙서·잘림)보다
    # 깔끔한 재작도가 사용자 의도에 맞다. 부정확분은 fidelity 플래그로 검수에 넘긴다.
    # (렌더 자체가 한 번도 성공 못 한 경우만 위 except 에서 RedrawError 로 빠진다.)
    return out_path, verdict


# ── 구조 스펙 렌더러: 그림→JSON→고정 렌더(환각 없음 + 한글 폰트 보장) ──────────
def _render_conic(ax, h, np):
    kind = h.get("kind"); cx, cy = h.get("cx", 0), h.get("cy", 0)
    a, b = float(h.get("a", 1) or 1), float(h.get("b", 1) or 1)
    if kind == "hyperbola":
        t = np.linspace(-2.4, 2.4, 200)
        if h.get("axis", "x") == "x":          # x²/a² − y²/b² = 1
            ax.plot(cx + a*np.cosh(t), cy + b*np.sinh(t), "k-", lw=1.4)
            ax.plot(cx - a*np.cosh(t), cy + b*np.sinh(t), "k-", lw=1.4)
        else:                                   # y²/a² − x²/b² = 1
            ax.plot(cx + b*np.sinh(t), cy + a*np.cosh(t), "k-", lw=1.4)
            ax.plot(cx + b*np.sinh(t), cy - a*np.cosh(t), "k-", lw=1.4)
    elif kind == "parabola":
        if h.get("axis", "x") == "x":           # y = a(x−cx)² + cy
            x = np.linspace(cx - abs(b or 3), cx + abs(b or 3), 200)
            ax.plot(x, a*(x - cx)**2 + cy, "k-", lw=1.4)
        else:                                   # x = a(y−cy)² + cx
            y = np.linspace(cy - abs(b or 3), cy + abs(b or 3), 200)
            ax.plot(a*(y - cy)**2 + cx, y, "k-", lw=1.4)


def spec_to_matplotlib(spec: dict, out_path: str) -> str:
    """구조 스펙(JSON) → 수학적으로 정확한 matplotlib 렌더.

    Claude 코드 실행이 아니라 '고정 렌더러'라 환각이 없고, 한글 폰트가 보장된다.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams["font.family"] = ["AppleGothic", "Apple SD Gothic Neo", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(6, 7))

    def ls(s): return "--" if s == "dashed" else "-"

    for e in spec.get("ellipses", []):
        t = np.linspace(0, 2*np.pi, 400)
        ax.plot(e["cx"] + e["a"]*np.cos(t), e["cy"] + e["b"]*np.sin(t), color="k", lw=1.3, ls=ls(e.get("style")))
    for c in spec.get("circles", []):
        t = np.linspace(0, 2*np.pi, 300)
        ax.plot(c["cx"] + c["r"]*np.cos(t), c["cy"] + c["r"]*np.sin(t), color="k", lw=1.3, ls=ls(c.get("style")))
    for h in spec.get("conics", []):
        _render_conic(ax, h, np)
    for pl in spec.get("polylines", []):
        pts = np.array(pl["points"], dtype=float)
        if pl.get("closed") and len(pts) > 2:
            pts = np.vstack([pts, pts[0]])
        ax.plot(pts[:, 0], pts[:, 1], color="k", lw=1.6, ls=ls(pl.get("style")))
    for s in spec.get("segments", []):
        if s.get("arrow"):
            ax.annotate("", xy=(s["x2"], s["y2"]), xytext=(s["x1"], s["y1"]),
                        arrowprops=dict(arrowstyle="->", color="k", lw=1.2))
        else:
            ax.plot([s["x1"], s["x2"]], [s["y1"], s["y2"]], color="k", lw=1.3, ls=ls(s.get("style")))
    for p in spec.get("points", []):
        mk = "*" if p.get("marker") == "*" else "o"
        ax.plot(p["x"], p["y"], mk, color="k", ms=11 if mk == "*" else 6)
        if p.get("label"):
            ax.annotate(str(p["label"]), (p["x"], p["y"]), textcoords="offset points", xytext=(7, 7), fontsize=13)
    for tx in spec.get("texts", []):
        ax.text(tx["x"], tx["y"], str(tx.get("text", "")), fontsize=12, ha="center", va="center")
    for d in spec.get("dims", []):
        ax.annotate("", xy=(d["x2"], d["y2"]), xytext=(d["x1"], d["y1"]),
                    arrowprops=dict(arrowstyle="<->", color="k", lw=1.0))
        ax.text((d["x1"] + d["x2"])/2, (d["y1"] + d["y2"])/2, str(d.get("text", "")), fontsize=11, ha="center", va="bottom")

    if spec.get("axes", {}).get("show"):
        ax.axhline(0, color="k", lw=0.8); ax.axvline(0, color="k", lw=0.8)
    ax.set_aspect("equal", adjustable="datalim")
    ax.axis("off")
    fig.savefig(out_path, dpi=200, bbox_inches="tight"); plt.close("all")
    return out_path


def redraw_via_spec(image_path: str, out_path: str, *, model: str | None = None):
    """그림 → 구조 스펙 추출 → 고정 렌더(환각 없음·한글 보장). (path, spec) 반환."""
    from .vision_claude import figure_to_spec
    spec = figure_to_spec(image_path, model=model)
    spec_to_matplotlib(spec, out_path)
    return out_path, spec
