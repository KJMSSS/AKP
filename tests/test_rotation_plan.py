"""회전 보정 plan 계산 회귀 테스트 (무과금·결정적).

광주여고 사건에서 드러난 버그를 고정한다:
  - 양면 스캔은 메타 회전이 일정(270)이어도 뒷면이 180° 뒤집혀 있어
    base 만으로는 짝수 페이지가 거꾸로 남는다. (base + 내용 추가각)이 정답.
  - TESSDATA_PREFIX(.env)가 osd 빠진 .tessdata를 가리키면 OSD가 통째로
    실패해 보정이 조용히 꺼진다 → osd 가진 디렉터리 폴백 필요.
"""
from __future__ import annotations

from src.common.pdf_utils import _plan_rotations, _osd_tessdata_dir


def test_duplex_alternating_270():
    """양면 스캔: 메타 270 일정, 짝수만 내용 180 추가 → 짝수 최종 90."""
    raw = [
        ("osd", 0, 270), ("osd", 180, 270),
        ("osd", 0, 270), ("osd", 180, 270),
        ("osd", 0, 270), ("osd", 180, 270),
    ]
    plans = _plan_rotations(raw)
    assert plans == [
        ("bake", 270), ("bake", 90),
        ("bake", 270), ("bake", 90),
        ("bake", 270), ("bake", 90),
    ]


def test_pure_metadata_rotation():
    """메타만 270, 내용 정방향 → 전부 270 굽기."""
    raw = [("osd", 0, 270), ("osd", 0, 270)]
    assert _plan_rotations(raw) == [("bake", 270), ("bake", 270)]


def test_pure_content_rotation():
    """메타 0, 내용이 90 누움 → 90 굽기 (기존 동작 유지)."""
    raw = [("osd", 90, 0), ("osd", 0, 0)]
    assert _plan_rotations(raw) == [("bake", 90), ("copy", 0)]


def test_no_rotation_is_copy():
    """회전 전혀 없음 → 전부 copy(원본 보존)."""
    raw = [("osd", 0, 0), ("osd", 0, 0)]
    assert _plan_rotations(raw) == [("copy", 0), ("copy", 0)]


def test_blank_page_copies_base():
    """백지는 보정 의미 없음 → base 그대로 copy."""
    raw = [("blank", 0, 0), ("osd", 0, 270)]
    assert _plan_rotations(raw) == [("copy", 0), ("bake", 270)]


def test_uncertain_interpolates_mode_add():
    """가로 OSD 실패(uncertain)는 확정 추가각 최빈값으로 보간."""
    raw = [
        ("osd", 180, 270),       # 확정 추가각 180
        ("uncertain", None, 270),  # → 180 보간 → 최종 (270+180)%360 = 90
        ("osd", 180, 270),
    ]
    plans = _plan_rotations(raw)
    assert plans[1] == ("bake", 90)


def test_osd_tessdata_dir_has_osd_or_none():
    """폴백 디렉터리를 반환하면 반드시 osd.traineddata 를 가진 곳이어야 한다."""
    from pathlib import Path
    d = _osd_tessdata_dir()
    if d is not None:                       # Tesseract 미설치 CI면 None 허용
        assert (Path(d) / "osd.traineddata").exists()


def test_normalize_no_crash_on_landscape(tmp_path):
    """회귀: _plan_rotations 리팩터 후 로깅 줄이 사라진 fallback 변수를 참조해
    uncertain(가로 OSD 실패) 페이지가 있으면 NameError 크래시했다(1d5639e).
    가로 페이지가 든 합성 PDF로 normalize_pdf_rotation이 예외 없이 끝나는지 확인."""
    import fitz
    from src.common.pdf_utils import normalize_pdf_rotation

    doc = fitz.open()
    # 가로 페이지(W>H) + 잉크(백지 임계 초과, 텍스트 아님 → OSD 미확정 유도)
    page = doc.new_page(width=842, height=595)   # A4 가로
    for y in range(60, 540, 18):
        page.draw_rect(fitz.Rect(40, y, 800, y + 6), fill=(0, 0, 0))
    p2 = doc.new_page(width=595, height=842)      # 세로 백지
    src = tmp_path / "syn.pdf"
    doc.save(str(src)); doc.close()

    out = normalize_pdf_rotation(src)             # 크래시 없이 Path 반환이면 통과
    from pathlib import Path
    assert isinstance(out, Path) and out.exists()
