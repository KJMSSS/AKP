"""typer 엔드포인트 빈-출력 가드 회귀 테스트 (app.py `_typer_loss_reason`).

배경: `/api/pipeline/{key}/typer/generate` 가 형식 미인식 시 빈/퇴화 2단을
성공으로 등록하던 문제(수피아여고류)를 차단한다. 가드는 1단(src) 대비
2단(cand) 의 ① 수식·그림 수 감소 ② 본문 텍스트 50% 미만 ③ HWPX 구조
검증 실패 중 하나라도 걸리면 한국어 사유를 반환(=등록 거부), 아니면 None.

수식·그림·본문 손실 분기는 validate_hwpx 이전에 반환되므로 합성 zip 으로
결정론적으로 검증한다. None(통과)·검증실패 분기는 validate_hwpx 를
monkeypatch 해 가드의 판정 로직만 단위 검증한다(정상 2단 None 은 실데이터로도
확인됨).
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import scripts.web.app as appmod


def _mk_hwpx(path: Path, *, n_eq: int = 0, n_pic: int = 0, body: str = "") -> Path:
    sec = (
        "<hp:sec xmlns:hp='http://x'>"
        f"<hp:t>{body}</hp:t>"
        + "<hp:equation/>" * n_eq
        + "<hp:pic/>" * n_pic
        + "</hp:sec>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Contents/section0.xml", sec)
    return path


# ── 거부 분기 (validate 이전 반환 → 합성 zip 결정론) ─────────────────────────

def test_guard_rejects_equation_loss(tmp_path):
    """수식 수가 줄면(2단이 수식 누락) 거부."""
    src = _mk_hwpx(tmp_path / "s.hwpx", n_eq=5, body="가" * 200)
    cand = _mk_hwpx(tmp_path / "c.hwpx", n_eq=0, body="가" * 200)
    reason = appmod._typer_loss_reason(src, cand)
    assert reason and "내용 손실" in reason and "5→0" in reason


def test_guard_rejects_picture_loss(tmp_path):
    """그림 수가 줄면 거부 (수식은 동일)."""
    src = _mk_hwpx(tmp_path / "s.hwpx", n_pic=3, body="가" * 200)
    cand = _mk_hwpx(tmp_path / "c.hwpx", n_pic=1, body="가" * 200)
    reason = appmod._typer_loss_reason(src, cand)
    assert reason and "내용 손실" in reason and "3→1" in reason


def test_guard_rejects_body_loss_pure_text(tmp_path):
    """수식·그림 0개인 순수 텍스트 시험지: 본문 50% 미만이면 거부.

    이 분기가 없으면 _finalize_2dan 식 수식·그림 가드만으론 빈 본문을 못 잡는다.
    """
    src = _mk_hwpx(tmp_path / "s.hwpx", body="문" * 1000)
    cand = _mk_hwpx(tmp_path / "c.hwpx", body="문" * 100)   # 10% 만 남음
    reason = appmod._typer_loss_reason(src, cand)
    assert reason and "본문 유실" in reason


# ── 통과(None) · 검증실패 분기 (validate_hwpx monkeypatch) ───────────────────

def test_guard_passes_when_content_preserved(tmp_path, monkeypatch):
    """수식·그림 유지 + 본문 50% 이상 + 구조 검증 통과 → None(등록 허용)."""
    monkeypatch.setattr(appmod, "validate_hwpx", lambda _p: [])
    src = _mk_hwpx(tmp_path / "s.hwpx", n_eq=3, n_pic=1, body="가" * 1000)
    # 2단은 메타표 라벨이 붙어 본문이 늘 수도 있음 — 동일/증가도 통과
    cand = _mk_hwpx(tmp_path / "c.hwpx", n_eq=3, n_pic=1, body="가" * 1100)
    assert appmod._typer_loss_reason(src, cand) is None


def test_guard_rejects_on_validation_failure(tmp_path, monkeypatch):
    """내용은 보존됐으나 HWPX 구조 검증이 실패하면 사유 반환."""
    monkeypatch.setattr(appmod, "validate_hwpx", lambda _p: ["section0.xml 파싱 오류"])
    src = _mk_hwpx(tmp_path / "s.hwpx", n_eq=2, body="가" * 500)
    cand = _mk_hwpx(tmp_path / "c.hwpx", n_eq=2, body="가" * 500)
    reason = appmod._typer_loss_reason(src, cand)
    assert reason and "HWPX 검증 실패" in reason
