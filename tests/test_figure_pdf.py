"""figure_pdf: 디지털 PDF 구조 기반 그림·표 크롭 테스트 (exam-engine 이식 2026-07-19).

VLM 눈대중 bbox 를 대체한 결정적 추출기의 순수 함수 단위 테스트.
(원본 PDF 통합 테스트는 exam-engine 저장소에 있음 — 여기는 함수 계약만 검증.)
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from pipeline.figure_pdf import _merge, _is_body_text, _overlap_ratio  # noqa: E402


def test_merge_joins_adjacent_within_gap():
    # gap 6 이내로 인접한 두 사각형은 하나로 병합
    out = _merge([(0, 0, 10, 10), (14, 0, 24, 10)], gap=6)
    assert len(out) == 1
    assert out[0] == [0, 0, 24, 10]


def test_merge_keeps_far_apart():
    # gap 밖(멀리 떨어짐)이면 분리 유지 — 지문 사이 데이터표가 안 뭉치는 핵심
    out = _merge([(0, 0, 10, 10), (50, 0, 60, 10)], gap=6)
    assert len(out) == 2


def test_is_body_text_boki_and_choices():
    # '<보 기>' 상자·선택지 줄은 native(본문) → 크롭 제외 대상
    assert _is_body_text("<보 기>", "")
    assert _is_body_text("① ㄱ  ② ㄴ  ③ ㄱ, ㄷ", "")


def test_is_body_text_matches_problem_body():
    body = re.sub(r"\s+", "", "다음은 이차함수의 그래프에 대한 설명이다")
    assert _is_body_text("다음은 이차함수의 그래프에 대한 설명이다.", body)


def test_is_body_text_excludes_figure_labels():
    # 그림 라벨·데이터표 셀은 본문에 없어 body 아님 → 크롭 대상
    body = re.sub(r"\s+", "", "이에 대한 설명으로 옳은 것만을 고른 것은")
    assert not _is_body_text("비커 I II III IV", body)
    assert not _is_body_text("도수분포", body)


def test_overlap_ratio():
    assert _overlap_ratio((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert _overlap_ratio((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
