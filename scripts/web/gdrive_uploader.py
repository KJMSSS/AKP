"""
Google Drive 자동 업로드.

인증 방식: 기존 Google OAuth refresh_token 재사용.
 - 처음 로그인 시 Drive 동의 → refresh_token 저장 (DATA_DIR/gdrive_token.json)
 - 이후 변환 완료 때마다 AKP/{year}/{subject}/ 폴더에 HWPX 저장

의존성: httpx (이미 requirements.txt에 있음)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

_TOKEN_URL  = "https://oauth2.googleapis.com/token"
_FILES_URL  = "https://www.googleapis.com/drive/v3/files"
_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"

# Google Drive 내 AKP 루트 폴더 ID
_AKP_FOLDER_ID = "1WVnRJ3RzORiTc2NdStzKdAv4M-PYHByq"

# 경로 단일 출처(store.DATA_DIR) — 볼륨마운트 > DATA_DIR > scripts/web/data.
from scripts.web.store import DATA_DIR as _DATA_DIR  # noqa: E402

TOKEN_FILE = _DATA_DIR / "gdrive_token.json"

# 마지막 업로드/토큰 실패 사유 — 실패를 조용히 삼키면 UI 가 "매트릭스 밖 변환" 같은
# 엉뚱한 안내를 띄운다(2026-07-11 실사고: Drive API 비활성 403 을 None 으로 삼킴).
_LAST_ERROR: str | None = None


def last_error() -> str | None:
    return _LAST_ERROR


def _set_error(msg: str | None) -> None:
    global _LAST_ERROR
    _LAST_ERROR = msg
    if msg:
        print(f"  [Drive] {msg}", flush=True)


def _friendly_api_error(resp: httpx.Response) -> str:
    """Google API 오류 응답 → 사람이 조치할 수 있는 한 줄 메시지."""
    try:
        err = resp.json().get("error", {})
    except Exception:  # noqa: BLE001
        err = {}
    msg = err.get("message", "") if isinstance(err, dict) else ""
    if "has not been used in project" in msg or "SERVICE_DISABLED" in str(err):
        # 활성화 URL 은 메시지 안에 들어있음 — 그대로 노출해 바로 조치 가능하게
        import re
        m = re.search(r"https://console\.developers\.google\.com\S+", msg)
        url = m.group(0).rstrip(".") if m else "Google Cloud 콘솔"
        return f"Google Cloud 프로젝트에서 Drive API가 비활성화됨 — {url} 에서 사용 설정 필요"
    if resp.status_code == 404:
        return ("AKP 루트 폴더에 접근 불가(404) — drive.file 권한은 '이 앱이 만든 파일'만 "
                "접근 가능. 폴더 ID 확인 또는 관리자 Drive 재인증 필요")
    return f"Drive API HTTP {resp.status_code}: {msg[:150] or resp.text[:150]}"


def save_refresh_token(refresh_token: str) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(
        json.dumps({"refresh_token": refresh_token}, ensure_ascii=False),
        encoding="utf-8",
    )


def is_configured() -> bool:
    return TOKEN_FILE.exists()


def _get_access_token() -> str | None:
    if not TOKEN_FILE.exists():
        _set_error("Drive 미연동 — 관리자(학원장)가 /auth/gdrive 에서 재인증 필요")
        return None
    try:
        data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except Exception:
        _set_error("gdrive_token.json 손상 — 관리자 Drive 재인증 필요")
        return None

    refresh_token = data.get("refresh_token", "")
    if not refresh_token:
        _set_error("refresh_token 없음 — 관리자 Drive 재인증 필요")
        return None

    resp = httpx.post(
        _TOKEN_URL,
        data={
            "grant_type":    "refresh_token",
            "refresh_token": refresh_token,
            "client_id":     os.environ.get("GOOGLE_CLIENT_ID", ""),
            "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        },
        timeout=10,
    )
    if resp.status_code == 200:
        return resp.json().get("access_token")
    _set_error(f"토큰 갱신 실패(HTTP {resp.status_code}) — 관리자 Drive 재인증 필요"
               f": {resp.text[:120]}")
    return None


def _find_or_create_folder(token: str, name: str, parent_id: str) -> str:
    headers = {"Authorization": f"Bearer {token}"}
    # Drive 쿼리 문법상 ' 와 \ 는 이스케이프 필요 — 폴더명(과목/연도)에 포함되면 쿼리가 깨진다
    safe_name = name.replace("\\", "\\\\").replace("'", "\\'")
    q = (
        f"name='{safe_name}' and '{parent_id}' in parents "
        f"and mimeType='application/vnd.google-apps.folder' and trashed=false"
    )
    r = httpx.get(
        _FILES_URL,
        headers=headers,
        params={"q": q, "fields": "files(id)"},
        timeout=10,
    )
    if r.status_code != 200:
        # 403(SERVICE_DISABLED) 등을 빈 목록으로 오해하면 밑의 생성 시도까지 실패가
        # 연쇄되고 원인이 묻힌다 — 즉시 사유를 남기고 중단.
        raise RuntimeError(_friendly_api_error(r))
    files = r.json().get("files", [])
    if files:
        return files[0]["id"]

    r = httpx.post(
        _FILES_URL,
        headers=headers,
        json={
            "name":     name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents":  [parent_id],
        },
        timeout=10,
    )
    if r.status_code not in (200, 201):
        raise RuntimeError(_friendly_api_error(r))
    return r.json()["id"]


def delete_file(file_id: str) -> bool:
    """Drive 파일 삭제. 성공 시 True."""
    token = _get_access_token()
    if not token:
        return False
    try:
        r = httpx.delete(
            f"{_FILES_URL}/{file_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        return r.status_code == 204
    except Exception:
        return False


def upload_file(
    file_path: Path,
    year: str,
    subject: str,
    content_type: str = "application/octet-stream",
) -> str | None:
    """
    임의 파일을 AKP/{year}/{subject}/ 에 업로드.
    성공 시 Drive 파일 ID, 실패·미설정 시 None(사유는 last_error()).
    """
    token = _get_access_token()
    if not token:
        return None
    _set_error(None)

    try:
        year_id    = _find_or_create_folder(token, year,    _AKP_FOLDER_ID)
        subject_id = _find_or_create_folder(token, subject, year_id)

        meta    = json.dumps({"name": file_path.name, "parents": [subject_id]}).encode()
        content = file_path.read_bytes()
        boundary = b"----GDriveBoundary"

        body = (
            b"--" + boundary + b"\r\n"
            b"Content-Type: application/json; charset=UTF-8\r\n\r\n"
            + meta + b"\r\n"
            b"--" + boundary + b"\r\n"
            b"Content-Type: " + content_type.encode() + b"\r\n\r\n"
            + content + b"\r\n"
            b"--" + boundary + b"--"
        )

        r = httpx.post(
            _UPLOAD_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type":  f"multipart/related; boundary={boundary.decode()}",
            },
            params={"uploadType": "multipart"},
            content=body,
            timeout=120,
        )
        if r.status_code in (200, 201):
            return r.json().get("id")
        _set_error(_friendly_api_error(r))
        return None

    except RuntimeError as e:          # _find_or_create_folder 가 남긴 사유
        _set_error(str(e))
        return None
    except Exception as e:  # noqa: BLE001 — 네트워크 등, 사유만 남기고 빌드는 계속
        _set_error(f"업로드 예외: {e.__class__.__name__}: {e}")
        return None


def upload_hwpx(hwpx_path: Path, year: str, subject: str) -> str | None:
    """HWPX를 AKP/{year}/{subject}/ 에 업로드. 성공 시 Drive 파일 ID."""
    return upload_file(hwpx_path, year, subject, "application/zip")


def upload_pdf(pdf_path: Path, year: str, subject: str) -> str | None:
    """(회전 보정된) PDF를 AKP/{year}/{subject}/ 에 업로드. 성공 시 Drive 파일 ID."""
    return upload_file(pdf_path, year, subject, "application/pdf")


def probe() -> dict:
    """Drive 연동 실사 점검 — 토큰 갱신 + AKP 루트 폴더 하위 조회까지 실제 호출.

    /api/drive/status?probe=1 에서 사용. 업로드 없이 '지금 업로드가 되는 상태인가'를
    판정한다(토큰 유효 + Drive API 활성 + AKP 폴더 접근 3가지 모두 확인).
    """
    _set_error(None)
    token = _get_access_token()
    if not token:
        return {"ok": False, "error": _LAST_ERROR or "토큰 발급 실패"}
    try:
        r = httpx.get(
            _FILES_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={"q": f"'{_AKP_FOLDER_ID}' in parents and trashed=false",
                    "fields": "files(id)", "pageSize": 1},
            timeout=10,
        )
        if r.status_code != 200:
            return {"ok": False, "error": _friendly_api_error(r)}
        return {"ok": True, "error": None}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{e.__class__.__name__}: {e}"}
