"""
클로바 OCR wrapper — 네이버 CLOVA OCR API (General Document).

반환 구조:
  ClovaResult.fields: list[ClovaField]  (inferText + boundingPoly)
  ClovaResult.korean_text(): 한글이 포함된 필드만 이어 붙인 텍스트

비용: 네이버 클라우드 플랫폼 OCR 과금 (페이지 단위)
로깅: log/cycle_15h/clova/ 에 요청/응답 JSON 저장
"""
from __future__ import annotations

import base64
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "log" / "cycle_15h" / "clova"
_KOR_MIN_RATIO = 0.3  # 텍스트 중 한글 비율이 이 이상이면 "한글 영역"으로 분류


@dataclass
class BBox:
    """좌상단→우상단→우하단→좌하단 꼭짓점 (px)."""
    vertices: list[dict[str, float]]  # [{"x":..,"y":..}, ...]

    @property
    def x_min(self) -> float:
        return min(v["x"] for v in self.vertices)

    @property
    def y_min(self) -> float:
        return min(v["y"] for v in self.vertices)

    @property
    def x_max(self) -> float:
        return max(v["x"] for v in self.vertices)

    @property
    def y_max(self) -> float:
        return max(v["y"] for v in self.vertices)


@dataclass
class ClovaField:
    text: str
    bbox: BBox
    confidence: float = 0.0
    is_korean: bool = False


@dataclass
class ClovaResult:
    fields: list[ClovaField] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    cost_pages: int = 1

    def korean_fields(self) -> list[ClovaField]:
        return [f for f in self.fields if f.is_korean]

    def korean_text(self) -> str:
        return " ".join(f.text for f in self.korean_fields())

    def all_text(self) -> str:
        return " ".join(f.text for f in self.fields)


def _is_korean(text: str) -> bool:
    if not text:
        return False
    kor = sum(1 for c in text if "가" <= c <= "힣")
    return kor / len(text) >= _KOR_MIN_RATIO


def _parse_response(data: dict[str, Any]) -> ClovaResult:
    fields: list[ClovaField] = []
    images = data.get("images", [])
    for img in images:
        for field_data in img.get("fields", []):
            text = field_data.get("inferText", "")
            poly = field_data.get("boundingPoly", {})
            vertices = poly.get("vertices", [])
            bbox = BBox(vertices=vertices)
            confidence = float(field_data.get("inferConfidence", 0.0))
            fields.append(ClovaField(
                text=text,
                bbox=bbox,
                confidence=confidence,
                is_korean=_is_korean(text),
            ))
    return ClovaResult(fields=fields, raw=data)


class ClovaDisabledError(RuntimeError):
    """CLOVA_DISABLED=1 환경변수로 비활성화된 경우."""


def ocr_image_bytes(
    image_bytes: bytes,
    image_format: str = "png",
    log_stem: str = "",
) -> ClovaResult:
    """이미지 바이트를 클로바 OCR로 처리하고 결과를 반환."""
    if os.environ.get("CLOVA_DISABLED", "").strip() in ("1", "true", "yes"):
        raise ClovaDisabledError("CLOVA_DISABLED=1 — 클로바 OCR 비활성화됨")

    invoke_url = os.environ.get("CLOVA_OCR_INVOKE_URL", "")
    secret = os.environ.get("CLOVA_OCR_SECRET", "")
    if not invoke_url or not secret:
        raise RuntimeError("CLOVA_OCR_INVOKE_URL, CLOVA_OCR_SECRET 환경변수 필요")
    # http:// → https:// 자동 변환 (CLOVA OCR은 HTTPS 필요)
    if invoke_url.startswith("http://"):
        invoke_url = "https://" + invoke_url[len("http://"):]

    payload = {
        "version": "V2",
        "requestId": str(uuid.uuid4()),
        "timestamp": int(time.time() * 1000),
        "images": [
            {
                "format": image_format,
                "name": log_stem or "image",
                "data": base64.b64encode(image_bytes).decode(),
            }
        ],
    }

    with httpx.Client(timeout=60.0) as client:
        resp = client.post(
            invoke_url,
            headers={"X-OCR-SECRET": secret, "Content-Type": "application/json"},
            json=payload,
        )

    if resp.status_code >= 400:
        raise RuntimeError(f"Clova OCR {resp.status_code}: {resp.text[:300]}")

    data = resp.json()

    if log_stem:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = _LOG_DIR / f"{log_stem}_{int(time.time())}.json"
        log_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    return _parse_response(data)


def ocr_image_file(image_path: Path, log_stem: str = "") -> ClovaResult:
    """이미지 파일을 클로바 OCR로 처리."""
    fmt = image_path.suffix.lower().lstrip(".")
    if fmt == "jpg":
        fmt = "jpeg"
    return ocr_image_bytes(
        image_path.read_bytes(),
        image_format=fmt,
        log_stem=log_stem or image_path.stem,
    )
