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

_DATA_DIR  = Path(os.environ.get("DATA_DIR", Path(__file__).parent / "data"))
TOKEN_FILE = _DATA_DIR / "gdrive_token.json"


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
        return None
    try:
        data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None

    refresh_token = data.get("refresh_token", "")
    if not refresh_token:
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
    return None


def _find_or_create_folder(token: str, name: str, parent_id: str) -> str:
    headers = {"Authorization": f"Bearer {token}"}
    q = (
        f"name='{name}' and '{parent_id}' in parents "
        f"and mimeType='application/vnd.google-apps.folder' and trashed=false"
    )
    r = httpx.get(
        _FILES_URL,
        headers=headers,
        params={"q": q, "fields": "files(id)"},
        timeout=10,
    )
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


def upload_hwpx(hwpx_path: Path, year: str, subject: str) -> str | None:
    """
    HWPX를 AKP/{year}/{subject}/ 에 업로드.
    성공 시 Drive 파일 ID, 실패·미설정 시 None.
    """
    token = _get_access_token()
    if not token:
        return None

    try:
        year_id    = _find_or_create_folder(token, year,    _AKP_FOLDER_ID)
        subject_id = _find_or_create_folder(token, subject, year_id)

        meta    = json.dumps({"name": hwpx_path.name, "parents": [subject_id]}).encode()
        content = hwpx_path.read_bytes()
        boundary = b"----GDriveBoundary"

        body = (
            b"--" + boundary + b"\r\n"
            b"Content-Type: application/json; charset=UTF-8\r\n\r\n"
            + meta + b"\r\n"
            b"--" + boundary + b"\r\n"
            b"Content-Type: application/zip\r\n\r\n"
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
            timeout=60,
        )
        if r.status_code in (200, 201):
            return r.json().get("id")
        return None

    except Exception:
        return None
