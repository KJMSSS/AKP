import base64
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

_API_BASE = "https://api.mathpix.com/v3"

# pdf_id 영구 캐시 — 같은 PDF 재실행 시 재과금 방지
_PDF_ID_CACHE_DIR = Path(__file__).resolve().parent.parent.parent.parent / ".mathpix_cache"
_PDF_ID_CACHE_PATH = _PDF_ID_CACHE_DIR / "pdf_ids.json"


def _pdf_sha256(pdf_path: Path) -> str:
    h = hashlib.sha256()
    h.update(pdf_path.read_bytes())
    return h.hexdigest()


def load_pdf_id_cache() -> dict[str, str]:
    if not _PDF_ID_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(_PDF_ID_CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_pdf_id_to_cache(pdf_path: Path, pdf_id: str) -> None:
    cache = load_pdf_id_cache()
    key = _pdf_sha256(pdf_path)
    cache[key] = pdf_id
    _PDF_ID_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _PDF_ID_CACHE_PATH.write_text(
        json.dumps(cache, indent=2), encoding="utf-8"
    )


def lookup_pdf_id(pdf_path: Path) -> str | None:
    return load_pdf_id_cache().get(_pdf_sha256(pdf_path))
_FORMATS = ["text", "latex_styled", "data", "mmd"]
_DATA_OPTIONS = {"include_latex": True, "include_table_html": True}

# 인라인: \( ... \)  |  디스플레이: \[ ... \]  (이미지 OCR)
_INLINE_RE  = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
_DISPLAY_RE = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)

# PDF 마크다운: $...$ / $$...$$ 구분자
_DOLLAR_DISPLAY_RE = re.compile(r'\$\$(.+?)\$\$', re.DOTALL)
_DOLLAR_INLINE_RE  = re.compile(r'(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)', re.DOTALL)


class MathpixError(Exception):
    pass


@dataclass
class OcrBlock:
    kind: str    # "text" | "formula_inline" | "formula_display" | "table"
    content: str


@dataclass
class OcrResult:
    raw: dict[str, Any]
    blocks: list[OcrBlock]         = field(default_factory=list)
    raw_text: str                  = ""
    mmd: str                       = ""          # Mathpix Markdown (있을 경우)
    confidence: float              = 0.0
    image_width: int               = 0
    image_height: int              = 0
    is_printed: bool               = False
    is_handwritten: bool           = False
    raw_data: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_response(cls, data: dict[str, Any]) -> "OcrResult":
        raw_data = data.get("data")
        result = cls(
            raw            = data,
            raw_text       = data.get("text", ""),
            mmd            = data.get("mmd", ""),
            confidence     = float(data.get("confidence", 0.0)),
            image_width    = int(data.get("image_width", 0)),
            image_height   = int(data.get("image_height", 0)),
            is_printed     = bool(data.get("is_printed", False)),
            is_handwritten = bool(data.get("is_handwritten", False)),
            raw_data       = raw_data if isinstance(raw_data, list) else [],
        )
        text = data.get("text", "").strip()
        if text:
            result.blocks = _parse_text(text)
        return result


def _parse_dollar_math(text: str) -> list[OcrBlock]:
    """
    PDF 마크다운($$...$$, $...$)을 수식/텍스트 블록으로 분해한다.
    $$...$$ → formula_display, $...$ → formula_inline
    """
    tokens: list[tuple[re.Match, str]] = sorted(
        [*((_m, "formula_display") for _m in _DOLLAR_DISPLAY_RE.finditer(text)),
         *((_m, "formula_inline")  for _m in _DOLLAR_INLINE_RE.finditer(text))],
        key=lambda t: t[0].start(),
    )

    blocks: list[OcrBlock] = []
    pos = 0
    for match, kind in tokens:
        if match.start() < pos:
            continue
        before = text[pos:match.start()].strip()
        if before:
            blocks.append(OcrBlock(kind="text", content=before))
        latex = match.group(1).strip()
        if latex:
            blocks.append(OcrBlock(kind=kind, content=latex))
        pos = match.end()

    tail = text[pos:].strip()
    if tail:
        blocks.append(OcrBlock(kind="text", content=tail))
    return blocks


def _parse_text(text: str) -> list[OcrBlock]:
    """
    Mathpix text 필드를 스캔해 텍스트·인라인수식·디스플레이수식 블록으로 분해한다.
    \\( ... \\) → formula_inline
    \\[ ... \\] → formula_display
    그 외       → text
    """
    # 두 패턴을 위치 순으로 정렬해 한 번에 처리
    tokens: list[tuple[re.Match, str]] = sorted(
        [*((_m, "formula_inline")  for _m in _INLINE_RE.finditer(text)),
         *((_m, "formula_display") for _m in _DISPLAY_RE.finditer(text))],
        key=lambda t: t[0].start(),
    )

    blocks: list[OcrBlock] = []
    pos = 0
    for match, kind in tokens:
        if match.start() < pos:     # 중첩 구간 skip
            continue
        before = text[pos:match.start()].strip()
        if before:
            blocks.append(OcrBlock(kind="text", content=before))
        latex = match.group(1).strip()
        if latex:
            blocks.append(OcrBlock(kind=kind, content=latex))
        pos = match.end()

    tail = text[pos:].strip()
    if tail:
        blocks.append(OcrBlock(kind="text", content=tail))
    return blocks


class MathpixClient:
    def __init__(self) -> None:
        self.app_id  = os.getenv("MATHPIX_APP_ID")
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
        return OcrResult.from_response(self._raw_ocr_image(image_path, retries))

    def raw_ocr_image(self, image_path: Path, retries: int = 3) -> dict[str, Any]:
        """파싱 없이 Mathpix 원본 JSON 반환 (디버그용)."""
        return self._raw_ocr_image(image_path, retries)

    def _raw_ocr_image(self, image_path: Path, retries: int = 3) -> dict[str, Any]:
        suffix = image_path.suffix.lower().lstrip(".")
        if suffix == "jpg":
            suffix = "jpeg"
        b64 = base64.b64encode(image_path.read_bytes()).decode()
        return self._post_json(
            "/text",
            {
                "src": f"data:image/{suffix};base64,{b64}",
                "formats": _FORMATS,
                "data_options": _DATA_OPTIONS,
            },
            retries=retries,
        )

    # ── PDF OCR ─────────────────────────────────────────────────

    def submit_pdf(self, pdf_path: Path) -> str:
        # \(...\) / \[...\] 구분자 → _parse_text와 호환
        options = {
            "conversion_formats": {"md": True},
            "math_inline_delimiters": ["\\(", "\\)"],
            "math_display_delimiters": ["\\[", "\\]"],
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
        self, pdf_id: str, interval: float = 3.0, timeout: float = 300.0,
        progress: bool = False,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        with httpx.Client(timeout=30.0) as client:
            while time.monotonic() < deadline:
                resp = client.get(f"{_API_BASE}/pdf/{pdf_id}", headers=self._auth)
                _raise_for_status(resp)
                data = resp.json()
                status = data.get("status")
                if progress:
                    pct = data.get("percent_done", 0)
                    print(f"\r  처리 중... {pct:.0f}%", end="", flush=True)
                if status == "completed":
                    if progress:
                        print()
                    return data
                if status == "error":
                    raise MathpixError(f"Mathpix PDF 처리 실패: {data.get('error')}")
                time.sleep(interval)
        raise MathpixError(f"PDF 처리 타임아웃 {timeout}s (pdf_id={pdf_id})")

    def fetch_pdf_markdown(self, pdf_id: str) -> str:
        """완료된 PDF OCR의 마크다운 결과를 가져온다."""
        with httpx.Client(timeout=60.0) as client:
            resp = client.get(f"{_API_BASE}/pdf/{pdf_id}.md", headers=self._auth)
        _raise_for_status(resp)
        return resp.text

    def ocr_pdf(self, pdf_path: Path) -> dict[str, Any]:
        return self.poll_pdf(self.submit_pdf(pdf_path))

    def ocr_pdf_to_result(
        self, pdf_path: Path, progress: bool = True
    ) -> "OcrResult":
        """PDF 전체를 OCR해 OcrResult로 반환한다."""
        pdf_id = self.submit_pdf(pdf_path)
        if progress:
            print(f"  제출 완료 (pdf_id={pdf_id})")
        self.poll_pdf(pdf_id, progress=progress)
        return self.fetch_pdf_to_result(pdf_id, source=str(pdf_path))

    def fetch_pdf_to_result(self, pdf_id: str, source: str = "") -> "OcrResult":
        """이미 완료된 PDF의 마크다운을 가져와 OcrResult로 변환한다."""
        md = self.fetch_pdf_markdown(pdf_id)
        blocks = _parse_dollar_math(md)
        result = OcrResult(raw={"pdf_id": pdf_id, "source": source}, blocks=blocks)
        return result

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
        raise MathpixError(f"Mathpix API {resp.status_code}: {resp.text[:300]}")
