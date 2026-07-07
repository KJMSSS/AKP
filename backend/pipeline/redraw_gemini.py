"""
그림 재작도 — Nano Banana Pro(Gemini 3 Pro Image) image-to-image.

배경: 구조 스펙→matplotlib 방식은 스펙 추출이 완벽해야만 정확하고, 결과가
무미건조하다(교과서 그림체와 다름). 2026년 image-to-image 모델은 '원본을
레퍼런스로 주고 깨끗하게 다시 그려라' 가 가능하며, Nano Banana Pro(Gemini 3
Pro Image)는 다이어그램의 라벨/텍스트 정확도가 업계 최고 수준이라 시험지
도형 재작도에 가장 적합하다(사용자 실측 검증: 캡처→재작도 거의 동일).

키(env): GEMINI_API_KEY 또는 GOOGLE_API_KEY  (Google AI Studio 발급)
모델(env): GEMINI_IMAGE_MODEL (기본 gemini-3.1-flash-image) · GEMINI_PRO_IMAGE_MODEL ('다시 생성'용 Pro)

* REST API 직접 호출(requests). google-genai SDK 의존 없음(vision_claude 와 동일 스타일).

시험지 충실도 주의: image-gen 은 드물게 숫자/라벨을 바꿀 수 있다 → 프롬프트로
'모든 숫자·기호·라벨 100% 보존' 을 강하게 못 박고, 최종 확인은 검수 UI(사용자)가 한다.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

# v1beta REST 엔드포인트(camelCase JSON). 모델 id 는 경로에 들어간다.
_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
# 기본=저비용 고품질(Nano Banana 2, Image Arena 1위). 어려운 그림만 PRO 로 '다시 생성'.
DEFAULT_IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")
PRO_IMAGE_MODEL = os.environ.get("GEMINI_PRO_IMAGE_MODEL", "gemini-3-pro-image")

# 시험지 도형 재작도 지시(충실도 최우선). 그림만 출력하도록 강제.
_REDRAW_INSTRUCTION = (
    "첨부 이미지는 한국 수학/과학 시험지에서 잘라낸 도형·그래프·도식입니다.\n"
    "이것을 깨끗하게 '다시 그려' 주세요. 다음 규칙을 반드시 지키세요:\n"
    "1) 모든 숫자·수식·기호·한글/영문 라벨을 원본과 100% 동일하게 유지하세요. "
    "값을 바꾸거나 없는 글자를 지어내지 마세요.\n"
    "1-1) 특히 F와 F′, A와 A′, P와 P′ 처럼 프라임(′) 유무로만 다른 라벨을 절대 혼동하지 마세요. "
    "각 점의 라벨을 원본에 적힌 그대로(프라임 유무까지) 정확히 쓰고, 같은 라벨을 두 곳에 중복하지 마세요. "
    "예: 왼쪽 초점이 F′ 이고 오른쪽 초점이 F 이면 그대로 F′·F 로 구분해서 표기.\n"
    "1-2) 문제 조건 표시 라벨 — (가)/(나), (A)/(B), ㉠/㉡, ①/②, Ⓐ/Ⓑ 등 — 도 글자·형태 하나 "
    "바꾸지 마세요. 괄호 문자를 원문자로, 알파벳을 숫자로 바꾸는 것 금지 "
    "(예: (A) 를 ⑦ 로, (B) 를 (A) 로 바꾸면 시험 문제가 망가집니다). "
    "원본에 보이는 라벨을 괄호/원문자 모양 그대로 옮겨 적으세요.\n"
    "2) 사람이 손으로 쓰거나 그린 것은 전부 지우세요 — 연필·볼펜 필기, 손글씨 숫자·메모·풀이, "
    "손으로 친 밑줄·동그라미·체크·화살표·별표, 형광펜/색연필 표시 등. "
    "인쇄되어 있던 '원래 도형'만 남기고, 손글씨 흔적은 깨끗이 제거하세요.\n"
    "2-1) 잘라낸 이미지의 가장자리에 있는 '읽을 수 없는 자국·잘린 글자 조각·점선/점 찍힌 줄·"
    "이웃 문제에서 넘어온 글자 일부·풀이 낙서'는 도형의 일부가 아닙니다. 절대 옮겨 그리지 말고 "
    "그냥 빼세요. 흐릿하거나 읽을 수 없는 것을 임의의 글자로 '복원'하는 것은 금지입니다 — "
    "원본에서 또렷하게 읽히는 인쇄 글자만 쓰세요.\n"
    "3) 인쇄된 원래 도형의 선·점·화살표·각도·비율·위치는 그대로 보존하세요.\n"
    "4) 흰 배경에 검은 선의 깔끔한 교과서 스타일 흑백 라인 드로잉으로 그리세요.\n"
    "5) 원본에 없는 것은 어떤 것도 추가하지 마세요 — 도형·좌표축·격자·음영·설명은 물론, "
    "글자 나열 줄·범례·캡션·제목·워터마크도 금지입니다.\n"
    "6) 설명 문장 없이 '그림만' 출력하세요."
)

# 표 전용 재현 지시 — 격자선·실선·칸 구조 보존이 핵심(도형 프롬프트와 분리).
REDRAW_TABLE_INSTRUCTION = (
    "첨부 이미지는 한국 시험지에서 잘라낸 '표'입니다.\n"
    "이 표를 깨끗하게 다시 그려주세요. 규칙:\n"
    "1) 모든 칸의 글자·숫자·수식·기호를 원본과 100% 동일하게 유지하세요(값 변경·생성 금지).\n"
    "2) 표의 모든 격자선(가로·세로 실선)과 바깥 테두리를 또렷한 검은 실선으로 그리세요. "
    "원본의 행/열 개수와 셀 병합(합쳐진 칸) 구조를 그대로 유지하세요.\n"
    "3) 사람이 손으로 쓴 필기·낙서·밑줄·동그라미·형광펜 표시는 모두 지우세요.\n"
    "3-1) 이미지 가장자리의 읽을 수 없는 자국·잘린 글자 조각은 표의 일부가 아니므로 빼세요. "
    "읽을 수 없는 것을 임의의 글자로 복원하지 마세요.\n"
    "4) 흰 배경에 검은 글자·검은 실선의 깔끔한 인쇄 표 스타일로 그리세요.\n"
    "5) 원본에 없는 행/열/내용·글자 나열 줄·범례·캡션·워터마크를 추가하지 마세요.\n"
    "6) 설명 문장 없이 '표만' 출력하세요."
)

_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


class GeminiError(RuntimeError):
    pass


def _api_key() -> str:
    k = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not k:
        raise GeminiError(
            "GEMINI_API_KEY 가 없습니다. Google AI Studio(aistudio.google.com)에서 "
            "키를 발급받아 .env 에 GEMINI_API_KEY=... 로 추가하세요."
        )
    return k


def _mime_of(path: str) -> str:
    return _MIME.get(Path(path).suffix.lower(), "image/png")


def redraw_with_gemini(image_path: str, out_path: str, *, model: str | None = None,
                       instruction: str | None = None, feedback: list[str] | None = None,
                       timeout: int = 120) -> str:
    """크롭 그림 → Nano Banana Pro 로 깨끗하게 재작도한 PNG 저장. 실패 시 GeminiError.

    원본(낙서 포함 가능)을 레퍼런스로 그대로 주고, 모델이 낙서 제거 + 라인
    재작도를 한 번에 수행한다(LaMa 선처리 불필요). 응답에서 첫 이미지 파트를 저장.
    feedback: 직전 시도가 검증에서 반려된 사유 목록 — 재시도 지시문에 덧붙인다.
    """
    import requests

    src = Path(image_path)
    if not src.exists():
        raise GeminiError(f"입력 이미지 없음: {image_path}")
    b64 = base64.b64encode(src.read_bytes()).decode("ascii")

    text = instruction or _REDRAW_INSTRUCTION
    if feedback:
        text += ("\n\n[재시도 — 직전 결과가 검수에서 반려됨. 아래 문제를 반드시 고치세요]\n"
                 + "\n".join(f"- {x}" for x in feedback[:5]))

    mdl = model or DEFAULT_IMAGE_MODEL
    url = f"{_API_BASE}/{mdl}:generateContent"
    body = {
        "contents": [{
            "parts": [
                {"inlineData": {"mimeType": _mime_of(image_path), "data": b64}},
                {"text": text},
            ],
        }],
        # 이미지 출력 강제. (텍스트 동반 모델 호환 위해 TEXT 도 허용)
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    }
    headers = {"x-goog-api-key": _api_key(), "content-type": "application/json"}

    import time
    data = None
    last = None
    for attempt in range(4):     # 네트워크 오류 + 429(rate limit)/5xx 일시 오류 백오프 재시도
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=timeout)
        except requests.exceptions.RequestException as e:
            last = e
            if attempt < 3:
                print(f"[redraw_gemini] 연결 오류({e.__class__.__name__}) — {2 * (attempt + 1)}s 후 "
                      f"재시도 {attempt + 1}/3", flush=True)
                time.sleep(2 * (attempt + 1)); continue
            raise GeminiError(f"Gemini API 연결 실패(4회 재시도): {last}")
        if resp.status_code == 200:
            data = resp.json()
            break
        # 429/5xx 는 일시적(rate limit·서버) → 백오프 후 재시도. 그 외 4xx(키/모델/안전차단)는 즉시 보고.
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            print(f"[redraw_gemini] HTTP {resp.status_code} — {3 * (attempt + 1)}s 후 "
                  f"재시도 {attempt + 1}/3", flush=True)
            time.sleep(3 * (attempt + 1)); continue
        raise GeminiError(f"Gemini HTTP {resp.status_code}: {resp.text[:400]}")
    if data is None:
        raise GeminiError(f"Gemini 응답 없음(재시도 소진): {last}")

    img_b64 = _first_image_b64(data)
    if not img_b64:
        # 이미지가 안 왔으면(안전차단/텍스트만 응답) 디버깅용으로 사유 노출
        txt = _first_text(data)
        raise GeminiError(f"이미지 응답 없음. 모델 사유: {txt[:300] or data}")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_bytes(base64.b64decode(img_b64))
    # 모델이 '흰 배경' 지시를 무시하고 스캔 회색 톤을 따라 그리는 경우 보정(실사고).
    try:
        from .figure import whiten_flat_background
        whiten_flat_background(out_path)
    except Exception:  # noqa: BLE001 — 보정 실패해도 재작도 자체는 유효
        pass
    return out_path


def _first_image_b64(data: dict) -> str | None:
    for cand in data.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return inline["data"]
    return None


def _first_text(data: dict) -> str:
    out = []
    for cand in data.get("candidates", []):
        # 안전차단 사유(finishReason: SAFETY 등)도 함께 보고
        if cand.get("finishReason") and cand["finishReason"] not in ("STOP", "MAX_TOKENS"):
            out.append(f"[finishReason={cand['finishReason']}]")
        for part in cand.get("content", {}).get("parts", []):
            if part.get("text"):
                out.append(part["text"])
    if not out and data.get("promptFeedback", {}).get("blockReason"):
        out.append(f"[blockReason={data['promptFeedback']['blockReason']}]")
    return " ".join(out)
