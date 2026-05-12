import base64
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

_API_BASE = "https://api.mathpix.com/v3"
_FORMATS = ["text", "latex_styled", "data"]
_DATA_OPTIONS = {"include_latex": True, "include_table_html": True}


class MathpixError(Exception):
    pass


@dataclass
class OcrBlock:
    kind: str    # "text" | "formula_display" | "table"
    content: str # LaTeX for formulas, plain text otherwise, HTML for tables


@dataclass
class OcrResult:
    raw: dict[str, Any]
    blocks: list[OcrBlock] = field(default_factory=list)

    @classmethod
    def from_response(cls, data: dict[str, Any]) -> "OcrResult":
        result = cls(raw=data)

        # data["data"] 가 list 인지 dict 인지 실제 응답 보고 분기
        # (list 형태가 확인되면 이 로직을 교체할 예정)
        data_field = data.get("data")
        if isinstance(data_field, dict):
            lines = data_field.get("lines", [])
        elif isinstance(data_field, list):
            lines = data_field          # list 자체가 line 배열인 경우
        else:
            lines = []

        if lines:
            for line in lines:
                if isinstance(line, dict):
                    result.blocks.extend(_parse_line(line))
        else:
            text = data.get("text", "").strip()
            if text:
                result.blocks.append(OcrBlock(kind="text", content=text))
        return result


def _parse_line(line: dict[str, Any]) -> list[OcrBlock]:
    kind = line.get("type", "text")
    if kind == "math":
        latex = line.get("latex", "")
        return [OcrBlock(kind="formula_display", content=latex)] if latex else []
    if kind == "table":
        html = line.get("html", "")
        return [OcrBlock(kind="table", content=html)] if html else []
    text = line.get("text", "").strip()
    return [OcrBlock(kind="text", content=text)] if text else []


class MathpixClient:
    def __init__(self) -> None:
        self.app_id = os.getenv("MATHPIX_APP_ID")
        self.app_key = os.getenv("MATHPIX_APP_KEY")
        if not self.app_id or not self.app_key:
            raise MathpixError(
                "MATHPIX_APP_ID and MATHPIX_APP_KEY must be set in .env"
            )

    @property
    def _auth(self) -> dict[str, str]:
        return {"app_id": self.app_id, "app_key": self.app_key}

    # ── 이미지 OCR ──────────────────────────────────────────────

    def ocr_image(self, image_path: Path, retries: int = 3) -> OcrResult:
        data = self._raw_ocr_image(image_path, retries=retries)
        return OcrResult.from_response(data)

    def raw_ocr_image(self, image_path: Path, retries: int = 3) -> dict[str, Any]:
        """파싱 없이 Mathpix 원본 JSON을 그대로 반환한다 (디버그용)."""
        return self._raw_ocr_image(image_path, retries=retries)

    def _raw_ocr_image(self, image_path: Path, retries: int = 3) -> dict[str, Any]:
        suffix = image_path.suffix.lower().lstrip(".")
        if suffix == "jpg":
            suffix = "jpeg"
        b64 = base64.b64encode(image_path.read_bytes()).decode()
        payload = {
            "src": f"data:image/{suffix};base64,{b64}",
            "formats": _FORMATS,
            "data_options": _DATA_OPTIONS,
        }
        return self._post_json("/text", payload, retries=retries)

    # ── PDF OCR (비동기 폴링 방식) ──────────────────────────────

    def submit_pdf(self, pdf_path: Path) -> str:
        """PDF를 Mathpix에 제출하고 pdf_id를 반환한다."""
        options = {
            "conversion_formats": {"md": True},
            "math_inline_delimiters": ["$", "$"],
            "math_display_delimiters": ["$$", "$$"],
        }
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{_API_BASE}/pdf",
                headers=self._auth,
                files={"file": (pdf_path.name, pdf_path.read_bytes(), "application/pdf")},
                data={"options_json": json.dumps(options)},
            )
        _raise_for_status(resp)
        return resp.json()["pdf_id"]

    def poll_pdf(
        self, pdf_id: str, interval: float = 3.0, timeout: float = 300.0
    ) -> dict[str, Any]:
        """처리 완료까지 폴링하고 최종 응답을 반환한다."""
        deadline = time.monotonic() + timeout
        with httpx.Client(timeout=30.0) as client:
            while time.monotonic() < deadline:
                resp = client.get(f"{_API_BASE}/pdf/{pdf_id}", headers=self._auth)
                _raise_for_status(resp)
                data = resp.json()
                status = data.get("status")
                if status == "completed":
                    return data
                if status == "error":
                    raise MathpixError(f"Mathpix PDF 처리 실패: {data.get('error')}")
                time.sleep(interval)
        raise MathpixError(f"PDF 처리 타임아웃 {timeout}s (pdf_id={pdf_id})")

    def ocr_pdf(self, pdf_path: Path) -> dict[str, Any]:
        """submit_pdf + poll_pdf를 순서대로 실행하는 편의 메서드."""
        pdf_id = self.submit_pdf(pdf_path)
        return self.poll_pdf(pdf_id)

    # ── 내부 헬퍼 ───────────────────────────────────────────────

    def _post_json(
        self, path: str, payload: dict[str, Any], retries: int = 3
    ) -> dict[str, Any]:
        headers = {**self._auth, "Content-Type": "application/json"}
        for attempt in range(retries):
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(f"{_API_BASE}{path}", headers=headers, json=payload)
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            _raise_for_status(resp)
            return resp.json()
        raise MathpixError(f"Rate limit: {retries}회 재시도 후 실패")


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        raise MathpixError(
            f"Mathpix API {resp.status_code}: {resp.text[:300]}"
        )
