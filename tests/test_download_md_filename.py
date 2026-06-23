"""`.md` 다운로드 Content-Disposition 회귀 테스트 (무과금).

한글 파일명을 raw 헤더에 박으면 uvicorn이 latin-1로 인코딩하다 500
(UnicodeEncodeError)이 났다. `_attachment_disposition`이 RFC 5987로 인코딩해
헤더가 latin-1 안전한지 검증한다.
"""
from __future__ import annotations

from urllib.parse import unquote

from scripts.web.app import _attachment_disposition

_KOR = "(광주)[2025_2_2_b_확통_금호고][조건부확률~통계적추정].md"


def test_korean_filename_is_latin1_safe():
    cd = _attachment_disposition(_KOR, "job123.md")
    # uvicorn이 하는 인코딩 — 여기서 안 터져야 500이 안 난다 (핵심 회귀 가드)
    cd.encode("latin-1")
    assert "filename*=UTF-8''" in cd
    # filename* 에 한글 전체 이름이 복원 가능해야 함
    star = cd.split("filename*=UTF-8''", 1)[1]
    assert unquote(star) == _KOR


def test_ascii_filename_passthrough():
    cd = _attachment_disposition("exam_01.md", "job123.md")
    cd.encode("latin-1")
    assert 'filename="exam_01.md"' in cd


def test_all_nonascii_uses_fallback():
    cd = _attachment_disposition("한글.md", "job123.md")
    cd.encode("latin-1")
    # ascii 폴백이 헤더에 존재 (구형 클라이언트용)
    assert 'filename="' in cd
    assert "filename*=UTF-8''" in cd
