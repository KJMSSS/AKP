"""풀이본 마커(쫑/쭌/DJ/훈) PDF는 OCR에서 스킵 — 회귀 테스트 (무네트워크·무과금).

정형화본+손풀이라 OCR 무의미 → convert()가 0단계에서 None 반환(과금·출력 0).
--clean-handwriting 명시 시엔 의도적 처리로 보고 스킵하지 않는다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import scripts.text.pdf_to_text as p2t


def test_marker_detection():
    assert p2t._hw_marker_in("(강남)[2024_1_1_a_수상_단대부고(쫑)15번.pdf") == "쫑"
    assert p2t._hw_marker_in("...모의평가 2회(24인성고)(훈).pdf") == "훈"
    assert p2t._hw_marker_in("(광주)[2024_1_1_a_수상_전대사대부고]DJ.pdf") == "DJ"
    assert p2t._hw_marker_in("...주관식 250901 심화 쫑쭌.pdf") in ("쫑", "쭌")
    # 깨끗한 원본/정답본은 마커 없음 → 정상 OCR 대상
    assert p2t._hw_marker_in("(광주)[2024_1_1_a_수상_경신여고][원본].pdf") is None
    assert p2t._hw_marker_in("(광주)[2024_2_1_a_확통_경신여고] 깨끗.pdf") is None


def test_convert_skips_marker_pdf():
    # 마커 PDF는 OCR 없이 즉시 스킵 → None. (가드가 파일 접근 전 반환하므로 실파일 불필요)
    out = p2t.convert(Path("(강남)[2024_1_1_a_수상_영동고(쫑).pdf"))
    assert out is None


def test_clean_name_not_skipped_passes_guard():
    # 마커 없는 파일은 스킵 안 됨 → 가드 통과 후 실제 단계 진입(없는 파일이라 예외).
    # 예외 발생 = 스킵(None 반환) 아님을 입증.
    with pytest.raises(Exception):
        p2t.convert(Path("nonexistent_경신여고_원본.pdf"))


def test_clean_handwriting_overrides_skip():
    # --clean-handwriting 명시 시 마커가 있어도 스킵하지 않고 처리 진입(없는 파일 → 예외).
    with pytest.raises(Exception):
        p2t.convert(Path("nonexistent_단대부고(쫑).pdf"), clean_handwriting=True)
