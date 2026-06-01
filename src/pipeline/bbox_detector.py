"""
Claude Vision으로 시험지 PDF의 문제별 bbox를 감지.

반환 구조:
  {
    problem_number: {
      "page": int,          # 0-indexed
      "y_top": float,       # 썸네일 픽셀
      "y_bottom": float,
      "col": "left"|"right",
      "type": "objective"|"subjective",
    },
    ...
  }
서술형은 number=101, 102, ... (내부 표현)
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import anthropic
import fitz  # PyMuPDF

THUMB_DPI = 100
_SUBJ_OFFSET = 100  # 서술형 1 → 101

_DETECT_PROMPT = """\
이 이미지는 수학 시험지의 {page_label}입니다.
이미지 크기: {w}×{h}px
{already_note}
찾을 것: 이 페이지에 인쇄된 모든 문제
- 객관식: "N." 또는 "N．"로 시작하는 문제 (N은 1~30 정수)
- 서술형: "서술형 N" 또는 "[서술형N]" 또는 "서술형" 글자가 포함된 문제 → type을 "subjective"로

무시할 것:
- 페이지 상단 헤더 (시험 제목, 학교명, 지시사항, 배점표, 수험번호 기입란)
- 학생 손글씨, 빨간 표시, 동그라미
- 저작권 문구, 페이지 번호

bbox 기준:
- y_top: 문제 번호 텍스트의 상단 픽셀 (문제 본문 첫 줄)
- y_bottom: 해당 문제의 마지막 선택지/내용 하단 픽셀
- col: 좌측 컬럼이면 "left", 우측이면 "right"

JSON만 출력 (설명 없이):
{{
  "problems": [
    {{"type": "objective", "number": N, "y_top": 픽셀, "y_bottom": 픽셀, "col": "left"}},
    {{"type": "subjective", "number": N, "y_top": 픽셀, "y_bottom": 픽셀, "col": "left"}}
  ]
}}

문제가 없으면: {{"problems": []}}"""


def _encode_image(path: Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode()


def _parse_response(text: str) -> list[dict]:
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        obj = json.loads(text[start:end])
        return obj.get("problems", [])
    except Exception:
        return []


def _normalize_number(prob: dict) -> int:
    """서술형은 100+N으로 변환."""
    if prob.get("type") == "subjective":
        return _SUBJ_OFFSET + int(prob["number"])
    return int(prob["number"])


class BBoxDetector:
    def __init__(self, model: str = "claude-opus-4-7"):
        self._client = anthropic.Anthropic()
        self._model = model

    def _detect_page(
        self,
        thumb_path: Path,
        page_idx: int,
        total_pages: int,
        already_found: set[int] | None = None,
    ) -> list[dict]:
        """한 페이지 bbox 감지 → raw problem list."""
        try:
            from PIL import Image
            with Image.open(thumb_path) as im:
                w, h = im.size
        except Exception:
            w, h = 0, 0

        label = f"페이지 {page_idx + 1}/{total_pages}"

        if already_found:
            obj_nums = sorted(n for n in already_found if n < _SUBJ_OFFSET)
            already_note = (
                f"이미 다른 페이지에서 찾은 객관식 번호: {obj_nums}\n"
                "→ 위 번호들은 이 페이지에 보이더라도 보고하지 마세요.\n"
                "→ 서술형 문제는 위 목록과 무관하므로 반드시 찾아주세요.\n\n"
            )
        else:
            already_note = ""

        b64 = _encode_image(thumb_path)
        payload = {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": b64,
                    },
                },
                {
                    "type": "text",
                    "text": _DETECT_PROMPT.format(
                        page_label=label, w=w, h=h,
                        already_note=already_note,
                    ),
                },
            ],
        }
        import time
        for attempt in range(3):
            try:
                msg = self._client.messages.create(
                    model=self._model,
                    max_tokens=1024,
                    messages=[payload],
                )
                break
            except Exception as e:
                if attempt < 2 and ("500" in str(e) or "529" in str(e)):
                    print(f"    [retry {attempt+1}] API 오류: {e}")
                    time.sleep(5 * (attempt + 1))
                else:
                    raise
        raw = msg.content[0].text.strip()
        return _parse_response(raw)

    def detect_all(
        self,
        pdf_path: Path,
        thumb_dir: Path,
        expected_obj: int | None = None,
        expected_subj: int | None = None,
        verbose: bool = True,
    ) -> dict[int, dict]:
        """
        PDF 전체 페이지에서 문제별 bbox 감지.

        Returns:
            {problem_number: {page, y_top, y_bottom, col, type}}
        """
        thumb_dir.mkdir(parents=True, exist_ok=True)
        doc = fitz.open(str(pdf_path))
        total = doc.page_count
        mat = fitz.Matrix(THUMB_DPI / 72, THUMB_DPI / 72)

        # 1. 썸네일 생성
        thumbs: list[Path] = []
        for i in range(total):
            p = thumb_dir / f"pg{i + 1}_thumb.png"
            if not p.exists():
                pix = doc[i].get_pixmap(matrix=mat)
                pix.save(str(p))
            thumbs.append(p)
        doc.close()

        if verbose:
            print(f"  썸네일 {total}장 준비")

        # 2. 페이지별 bbox 감지 (이미 찾은 번호를 다음 페이지에 전달)
        found: dict[int, dict] = {}
        for i, thumb in enumerate(thumbs):
            already = set(found.keys())
            probs = self._detect_page(thumb, i, total, already_found=already)
            for p in probs:
                try:
                    num = _normalize_number(p)
                except (KeyError, ValueError):
                    continue
                if num not in found:  # 중복 감지 시 첫 번째 우선
                    found[num] = {
                        "page": i,
                        "y_top": p["y_top"],
                        "y_bottom": p["y_bottom"],
                        "col": p.get("col", "left"),
                        "type": p.get("type", "objective"),
                    }
            if verbose:
                nums = [_normalize_number(p) for p in probs if "number" in p]
                print(f"  page{i + 1}: {sorted(nums)}")

        # 3. 누락 번호 재시도
        found = self._retry_missing(
            found, thumbs, total, expected_obj, expected_subj, verbose
        )

        return found

    def _retry_missing(
        self,
        found: dict[int, dict],
        thumbs: list[Path],
        total: int,
        expected_obj: int | None,
        expected_subj: int | None,
        verbose: bool,
    ) -> dict[int, dict]:
        """누락 번호를 인접 페이지에서 재시도."""
        obj_nums = sorted(n for n in found if n < _SUBJ_OFFSET)
        subj_nums = sorted(n - _SUBJ_OFFSET for n in found if n >= _SUBJ_OFFSET)

        # 기대 번호 추정
        if expected_obj is None:
            expected_obj = max(obj_nums) if obj_nums else 0
        if expected_subj is None:
            expected_subj = max(subj_nums) if subj_nums else 0

        missing_obj = [n for n in range(1, expected_obj + 1) if n not in found]
        missing_subj = [
            n + _SUBJ_OFFSET
            for n in range(1, expected_subj + 1)
            if (n + _SUBJ_OFFSET) not in found
        ]
        missing = missing_obj + missing_subj

        if not missing:
            return found

        if verbose:
            print(f"  [재시도] 누락 번호: {missing}")

        # 누락 번호별로 후보 페이지 결정 (앞뒤 번호의 페이지 ±1)
        pages_to_retry: set[int] = set()
        for num in missing:
            # 앞 번호의 페이지
            prev = max((n for n in found if n < num), default=None)
            nxt = min((n for n in found if n > num), default=None)
            if prev is not None:
                pages_to_retry.add(found[prev]["page"])
                pages_to_retry.add(min(found[prev]["page"] + 1, total - 1))
            if nxt is not None:
                pages_to_retry.add(found[nxt]["page"])
                pages_to_retry.add(max(found[nxt]["page"] - 1, 0))

        for pg in sorted(pages_to_retry):
            probs = self._detect_page(thumbs[pg], pg, total)
            for p in probs:
                try:
                    num = _normalize_number(p)
                except (KeyError, ValueError):
                    continue
                if num in missing and num not in found:
                    found[num] = {
                        "page": pg,
                        "y_top": p["y_top"],
                        "y_bottom": p["y_bottom"],
                        "col": p.get("col", "left"),
                        "type": p.get("type", "objective"),
                    }
                    if verbose:
                        print(f"    재시도 성공: {num}번 (page{pg + 1})")

        # 최종 누락 경고
        still_missing = [n for n in missing if n not in found]
        if still_missing and verbose:
            print(f"  [경고] 최종 감지 실패: {still_missing}")

        return found
