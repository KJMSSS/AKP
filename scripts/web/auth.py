"""세션 기반 인증 헬퍼 — app.py 와 engine_api.py 가 공유한다.

세션 미들웨어(SessionMiddleware)는 app.py 에서 앱 레벨로 설치되고,
여기 헬퍼들은 request.session 만 읽으므로 순환 임포트가 없다.
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from scripts.web.users import is_admin, is_allowed


def current_email(request: Request) -> str | None:
    return request.session.get("email")


def require_login(request: Request) -> str:
    email = current_email(request)
    if not email:
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    if not is_allowed(email):
        raise HTTPException(status_code=403, detail="접근 권한이 없습니다. 관리자에게 문의하세요.")
    return email


def require_admin(request: Request) -> str:
    email = require_login(request)
    if not is_admin(email):
        raise HTTPException(status_code=403, detail="관리자만 접근 가능합니다.")
    return email
