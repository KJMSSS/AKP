"""
Mathpix OCR 연동 — 수식/본문 텍스트 인식.

문서: https://docs.mathpix.com  (v3/text 엔드포인트)
키(env): MATHPIX_APP_ID, MATHPIX_APP_KEY

입력 이미지(문제 영역 크롭 권장)를 보내면 LaTeX/MMD/구조화 데이터를 받는다.
- formats=["text","data","latex_styled"] 로 본문 + 수식 + 라인 데이터 확보
- include_line_data 로 줄 단위 위치/종류(수식 vs 텍스트) 확보 -> run 분해에 사용

* 키 없이 호출하면 명시적 에러(MathpixError). 라이브 검증은 키 필요.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass

ENDPOINT = "https://api.mathpix.com/v3/text"


class MathpixError(RuntimeError):
    pass


@dataclass
class MathpixResult:
    text: str            # Mathpix Markdown (수식은 \( \) / \[ \])
    latex_styled: str    # 단일 수식일 때 유용
    line_data: list      # 줄 단위 {type, text, region...}
    raw: dict


def _credentials() -> tuple[str, str]:
    app_id = os.environ.get("MATHPIX_APP_ID")
    app_key = os.environ.get("MATHPIX_APP_KEY")
    if not app_id or not app_key:
        raise MathpixError(
            "Mathpix 키가 없습니다. 환경변수 MATHPIX_APP_ID, MATHPIX_APP_KEY 설정 필요.")
    return app_id, app_key


def _encode(image_path: str) -> str:
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    ext = os.path.splitext(image_path)[1].lstrip(".").lower() or "png"
    mime = "jpeg" if ext in ("jpg", "jpeg") else ext
    return f"data:image/{mime};base64,{b64}"


def ocr_image(image_path: str, *, timeout: int = 30) -> MathpixResult:
    import requests  # 지연 import (오프라인 코어가 requests 에 의존하지 않도록)

    app_id, app_key = _credentials()
    payload = {
        "src": _encode(image_path),
        "formats": ["text", "data", "latex_styled"],
        "data_options": {"include_latex": True, "include_asciimath": False,
                          "include_table_html": True},
        "include_line_data": True,
        "rm_spaces": True,
    }
    headers = {"app_id": app_id, "app_key": app_key, "Content-type": "application/json"}
    resp = requests.post(ENDPOINT, json=payload, headers=headers, timeout=timeout)
    if resp.status_code != 200:
        raise MathpixError(f"Mathpix HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    if data.get("error"):
        raise MathpixError(f"Mathpix error: {data['error']}")
    return MathpixResult(
        text=data.get("text", ""),
        latex_styled=data.get("latex_styled", ""),
        line_data=data.get("line_data", []),
        raw=data,
    )


# --- Mathpix Markdown -> run 리스트(텍스트/수식 분해) -------------------------
def mmd_to_runs(mmd: str) -> list[dict]:
    """Mathpix Markdown 의 인라인 수식 \\( ... \\) / \\[ ... \\] 을 eqn run 으로 분해.

    반환 run: {"type":"text","text":...} 또는 {"type":"eqn","latex":...}
    (latex 는 이후 latex_to_hwp 로 한글 수식 스크립트 변환)
    """
    import re
    runs: list[dict] = []
    # \( inline \)  또는  \[ display \]  또는  $...$
    pattern = re.compile(r"\\\((.+?)\\\)|\\\[(.+?)\\\]|\$(.+?)\$", re.S)
    pos = 0
    for m in pattern.finditer(mmd):
        if m.start() > pos:
            txt = mmd[pos:m.start()]
            if txt.strip():
                runs.append({"type": "text", "text": txt})
        latex = m.group(1) or m.group(2) or m.group(3) or ""
        runs.append({"type": "eqn", "latex": latex.strip()})
        pos = m.end()
    if pos < len(mmd):
        tail = mmd[pos:]
        if tail.strip():
            runs.append({"type": "text", "text": tail})
    return runs


# --- Mathpix 결과 -> 표 추출 -------------------------------------------------
def extract_tables(result: "MathpixResult") -> list[list[list[dict]]]:
    """Mathpix 결과에서 표들을 table.py rows 형태로 추출.

    우선순위: (1) data[].value 의 <table> HTML(include_table_html=True 로 요청),
    (2) text(MMD) 에 직접 박힌 <table>, (3) 폴백으로 MMD 파이프 표(| a | b |).
    각 표는 [[{text,colspan,rowspan}...]...] 이고, table.TableBuilder.build() 에
    그대로 넘겨 hp:tbl 로 변환한다.
    """
    from .table import html_table_to_rows, markdown_table_to_rows

    tables: list[list[list[dict]]] = []
    for d in (result.raw.get("data") or []):
        val = (d.get("value") or "")
        if "<table" in val.lower():
            rows = html_table_to_rows(val)
            if rows:
                tables.append(rows)
    if not tables and "<table" in (result.text or "").lower():
        rows = html_table_to_rows(result.text)
        if rows:
            tables.append(rows)
    if not tables and result.text:
        rows = markdown_table_to_rows(result.text)
        if rows:
            tables.append(rows)
    return tables
