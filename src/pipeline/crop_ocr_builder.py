"""
크롭 OCR 기반 v5 빌드 파이프라인.

흐름:
  PDF → bbox 감지 → 문제별 크롭 → Mathpix OCR
      → OCR 분리·병합 → raw.md → parse → normalize → LLM → fallback → hwpx → 표 삽입 → 검증
"""
from __future__ import annotations

import io
import re
import zipfile
from collections import defaultdict
from contextlib import redirect_stdout
from pathlib import Path

import fitz

from src.pipeline.bbox_detector import BBoxDetector, THUMB_DPI

from src.text_only.problem_segmenter import parse_problems, rebuild_markdown
from src.ocr.choice_normalizer import normalize_choices
from src.text_only.ocr_fallback import apply_fallback
from src.text_only.text_builder import build_from_markdown
from src.ocr.llm_postprocess import postprocess_markdown
from src.common.hwpx_table_inserter import replace_condition_tables, replace_boilerplate_tables
from src.common.ocr.mathpix_client import MathpixClient

CROP_DPI   = 300
CROP_SCALE = CROP_DPI / 72
THUMB_SCALE = THUMB_DPI / 72
TOP_MARGIN  = 15    # y_top 위쪽 여백 (300dpi px) — 헤더 침범 최소화
BOT_MARGIN  = 80    # y_bottom 아래 여백
BOT_EXTEND  = 200   # 마지막 문제 y_bottom 추가 확장 최대
SUBJ_OFFSET = 100

_PROB_PAT = re.compile(r'(?m)^(\d{1,2})[.．]\s+')


# ── 크롭 ──────────────────────────────────────────────────────────────────────

def _crop_problem(pdf_path: Path, page_idx: int, bbox: dict) -> bytes:
    doc  = fitz.open(str(pdf_path))
    page = doc[page_idx]
    mat  = fitz.Matrix(CROP_SCALE, CROP_SCALE)
    pix_full = page.get_pixmap(matrix=mat)
    W, H = pix_full.width, pix_full.height
    mid  = W // 2

    y_top    = max(0, int(bbox["y_top"]    / THUMB_SCALE * CROP_SCALE) - TOP_MARGIN)
    y_bottom = min(H, int(bbox["y_bottom"] / THUMB_SCALE * CROP_SCALE) + BOT_MARGIN)

    col = bbox.get("col", "left")
    x0, x1 = (0, mid - 20) if col == "left" else (mid + 20, W)

    rect = fitz.Rect(x0 / CROP_SCALE, y_top / CROP_SCALE, x1 / CROP_SCALE, y_bottom / CROP_SCALE)
    pix  = page.get_pixmap(matrix=mat, clip=rect)
    doc.close()
    return pix.tobytes("png")


# ── bbox 보정 ─────────────────────────────────────────────────────────────────

def _adjust_bboxes(bboxes: dict[int, dict], pdf_path: Path) -> dict[int, dict]:
    """
    같은 페이지·컬럼 내 연속 문제 간 y_bottom 확장 및 y_top 당기기.
    - N번 y_bottom → N+1번 y_top - 5
    - N+1번 y_top  → N번 원래 y_bottom (감지된 값)
    - 마지막 문제  → 감지된 y_bottom + BOT_EXTEND (페이지 끝까지 아님)
    """
    doc = fitz.open(str(pdf_path))
    page_h: dict[int, int] = {}
    mat = fitz.Matrix(THUMB_DPI / 72, THUMB_DPI / 72)
    for i in range(doc.page_count):
        pix = doc[i].get_pixmap(matrix=mat)
        page_h[i] = pix.height
    doc.close()

    groups: dict[tuple, list[int]] = defaultdict(list)
    for num, b in bboxes.items():
        groups[(b["page"], b["col"])].append(num)

    orig  = {k: dict(v) for k, v in bboxes.items()}  # 원래 감지값 (불변)
    adj   = {k: dict(v) for k, v in bboxes.items()}

    for (pg, col), nums in groups.items():
        nums_s = sorted(nums)
        h = page_h.get(pg, 1100)

        for i, num in enumerate(nums_s):
            if i + 1 < len(nums_s):
                nxt = nums_s[i + 1]
                nxt_orig_y_top = orig[nxt]["y_top"]

                # 현재 문제 y_bottom → 다음 문제 감지 y_top - 5
                if adj[num]["y_bottom"] < nxt_orig_y_top - 5:
                    adj[num]["y_bottom"] = nxt_orig_y_top - 5

                # 다음 문제 y_top → 현재 문제 원래 y_bottom (문제 본문 포함)
                curr_orig_bottom = orig[num]["y_bottom"]
                if adj[nxt]["y_top"] > curr_orig_bottom:
                    adj[nxt]["y_top"] = curr_orig_bottom
            else:
                # 마지막 문제: 보수적으로 y_bottom + BOT_EXTEND
                ext = min(adj[num]["y_bottom"] + BOT_EXTEND, h - 10)
                adj[num]["y_bottom"] = max(adj[num]["y_bottom"], ext)

    return adj


# ── OCR 분리·병합 ─────────────────────────────────────────────────────────────

def _split_and_merge_ocr(ocr_results: dict[int, str]) -> dict[int, str]:
    """
    OCR 텍스트에 다른 문제 번호가 혼입된 경우를 분리하여 올바른 번호에 할당.
    예: 1번 OCR에 '2. 동성고등학교...' 포함 → 2번 fragments에 추가.
    """
    known = set(ocr_results.keys())
    fragments: dict[int, list[str]] = {n: [] for n in known}
    has_header: set[int] = set()  # 번호 헤더("N. ...") 이미 할당된 번호

    for src_num, text in sorted(ocr_results.items()):
        matches = list(_PROB_PAT.finditer(text))

        if not matches:
            # 번호 없음 → 원래 번호에 그대로 (선택지 등 보충)
            fragments[src_num].append(text.strip())
            continue

        for i, m in enumerate(matches):
            found_num = int(m.group(1))
            start = m.start()
            end   = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            chunk = text[start:end].strip()

            # 알려진 번호에만 할당
            target = found_num if found_num in known else src_num

            if not chunk:
                continue

            # 번호 헤더 블록: 이미 해당 번호에 헤더가 있으면 무시 (중복 혼입)
            if target in has_header and target != src_num:
                continue
            has_header.add(target)
            fragments[target].append(chunk)

    # 조각 합치기 (중복 제거)
    result: dict[int, str] = {}
    for num in sorted(known):
        frags = fragments.get(num, [])
        if not frags:
            result[num] = ocr_results[num]
            continue

        seen:  set[str] = set()
        parts: list[str] = []
        for f in frags:
            if f not in seen:
                seen.add(f)
                parts.append(f)
        result[num] = "\n".join(parts)

    return result


# ── raw.md 조합 ───────────────────────────────────────────────────────────────

def _build_raw_md(ocr_results: dict[int, str]) -> str:
    lines = []
    for num in sorted(ocr_results):
        text = ocr_results[num].strip()
        if not text:
            continue

        if num >= SUBJ_OFFSET:
            subj_n = num - SUBJ_OFFSET
            if not re.match(r'^\s*서술형\s*\d', text):
                text = f"서술형 {subj_n}. {text}"
        else:
            if not re.match(rf'^\s*{num}[.．]', text):
                text = f"{num}. {text}"

        lines.append(text)
        lines.append("")

    return "\n".join(lines)


# ── 자동 검증 ─────────────────────────────────────────────────────────────────

def _verify(segments, out_hwpx: Path) -> dict:
    """
    빌드 결과를 자동 검증.
    Returns: {
        "missing_numbers": [...],   # 누락 번호
        "bad_choices": {...},       # 선택지 부족 {번호: count}
        "duplicate_numbers": [...], # 중복 번호
        "hwpx_choice_count": N,     # hwpx 내 ①②③④⑤ 마커 수
        "pass": bool,
    }
    """
    obj_segs  = [s for s in segments if not s.is_subjective]
    subj_segs = [s for s in segments if s.is_subjective]

    # 번호 목록
    obj_nums  = [s.number for s in obj_segs]
    seen: dict[int, int] = {}
    for n in obj_nums:
        seen[n] = seen.get(n, 0) + 1
    dups = [n for n, cnt in seen.items() if cnt > 1]

    max_obj = max(obj_nums, default=0)
    missing = [n for n in range(1, max_obj + 1) if n not in seen]

    bad_choices = {
        s.number: len(s.choices)
        for s in obj_segs
        if s.number not in dups and len(s.choices) != 5
    }

    # hwpx 내 선택지 마커 수
    choice_count = 0
    try:
        with zipfile.ZipFile(str(out_hwpx)) as zf:
            xml = zf.read("Contents/section0.xml").decode("utf-8", errors="ignore")
        choice_count = sum(xml.count(c) for c in "①②③④⑤")
    except Exception:
        pass

    expected_choices = max_obj * 5
    ok = (
        not missing
        and not dups
        and not bad_choices
        and choice_count == expected_choices
    )

    return {
        "missing_numbers": missing,
        "duplicate_numbers": dups,
        "bad_choices": bad_choices,
        "obj_count": len(set(obj_nums)),
        "subj_count": len(subj_segs),
        "hwpx_choice_count": choice_count,
        "expected_choice_count": expected_choices,
        "pass": ok,
    }


def _print_verify(v: dict):
    print("\n=== 자동 검증 ===")
    status = "PASS ✓" if v["pass"] else "FAIL ✗"
    print(f"  결과: {status}")
    print(f"  객관식 {v['obj_count']}개 | 서술형 {v['subj_count']}개")
    print(f"  hwpx 선택지 마커: {v['hwpx_choice_count']} / 기대: {v['expected_choice_count']}")
    if v["missing_numbers"]:
        print(f"  [경고] 누락 번호: {v['missing_numbers']}")
    if v["duplicate_numbers"]:
        print(f"  [경고] 중복 번호: {v['duplicate_numbers']}")
    if v["bad_choices"]:
        print(f"  [경고] 선택지 부족: {v['bad_choices']}")


# ── 메인 파이프라인 ───────────────────────────────────────────────────────────

def build_one_crop(
    source: str,
    src_dir: Path,
    prod_dir: Path,
    crop_dir: Path,
    preprocess_fn=None,
    log_stem: str | None = None,
    verbose: bool = True,
) -> tuple[Path, dict]:
    """
    학교 하나를 크롭 OCR 파이프라인으로 빌드.
    Returns: (hwpx_path, verify_result)
    """
    pdf       = src_dir / f"[{source}].pdf"
    template  = src_dir / f"[{source}].hwpx"
    out_hwpx  = prod_dir / f"{source}_v5.hwpx"
    thumb_dir = crop_dir / "thumbs"
    ocr_dir   = crop_dir / "ocr"
    ocr_dir.mkdir(parents=True, exist_ok=True)

    if log_stem is None:
        log_stem = source.replace("2025_1_1_b_공수1_", "")

    sep = "=" * 55
    print(f"\n{sep}\n[{source}]\n{sep}")

    # Step 1: bbox 감지
    print("\n=== bbox 감지 ===")
    detector = BBoxDetector()
    bboxes   = detector.detect_all(pdf, thumb_dir, verbose=verbose)
    bboxes   = _adjust_bboxes(bboxes, pdf)
    print(f"  조정 후: {sorted(bboxes.keys())}")

    # Step 2: 크롭 + OCR
    print("\n=== 크롭 + OCR ===")
    mp = MathpixClient()
    ocr_results: dict[int, str] = {}

    for num in sorted(bboxes):
        crop_path = crop_dir / f"prob_{num}.png"
        ocr_path  = ocr_dir  / f"prob_{num}.md"

        if not crop_path.exists():
            img = _crop_problem(pdf, bboxes[num]["page"], bboxes[num])
            crop_path.write_bytes(img)

        if not ocr_path.exists():
            raw = mp.raw_ocr_image(crop_path)
            text = raw.get("mmd", "") or raw.get("text", "")
            ocr_path.write_text(text, encoding="utf-8")
        else:
            text = ocr_path.read_text(encoding="utf-8")

        ocr_results[num] = text
        if verbose:
            label = f"서술형{num - SUBJ_OFFSET}" if num >= SUBJ_OFFSET else f"{num}번"
            print(f"  {label}: {len(text)}자")

    # Step 3: OCR 분리·병합
    ocr_results = _split_and_merge_ocr(ocr_results)

    # Step 4: raw.md 조합
    print("\n=== raw.md 조합 ===")
    md_raw = _build_raw_md(ocr_results)
    # 공통 점수 형식 통일
    md_raw = re.sub(r'[【（(]([\d.]+)점[）)】]', r'[\1점]', md_raw)
    if preprocess_fn:
        md_raw = preprocess_fn(md_raw)
    raw_cache = crop_dir / "raw.md"
    raw_cache.write_text(md_raw, encoding="utf-8")
    print(f"  raw.md: {len(md_raw)}자 → {raw_cache}")

    # Step 5: 파싱
    print("\n=== 파싱 ===")
    header, segments = parse_problems(md_raw)
    obj_cnt  = sum(1 for s in segments if not s.is_subjective)
    subj_cnt = sum(1 for s in segments if s.is_subjective)
    print(f"  객관식 {obj_cnt}개, 서술형 {subj_cnt}개")
    for s in segments:
        tag = "서술형" if s.is_subjective else "객관식"
        print(f"    {tag} {s.number}번: 선택지={len(s.choices)}개")

    # Step 6: 선택지 정규화
    print("\n=== 선택지 정규화 ===")
    segments = normalize_choices(segments, log_stem=log_stem)

    # Step 7: rebuild + LLM 후처리
    md_rebuilt = rebuild_markdown(header, segments)
    print("\n=== LLM 후처리 ===")
    md_llm, llm_meta = postprocess_markdown(md_rebuilt, log_stem=log_stem)
    if llm_meta.get("skipped"):
        print(f"  스킵: {llm_meta.get('reason')}")
    else:
        print(f"  완료 (${llm_meta.get('cost_usd', 0):.4f})")

    # Step 8: fallback + HWPX 빌드
    print("\n=== fallback + HWPX 빌드 ===")
    buf = io.StringIO()
    with redirect_stdout(buf):
        md_proc = apply_fallback(md_llm, pdf)
        build_from_markdown(md_proc, out_hwpx, template)
    out = buf.getvalue()
    print(out[-400:] if out else "  (출력 없음)")

    # Step 9: 표 삽입
    print("\n=== 표 삽입 ===")
    n_cond = replace_condition_tables(out_hwpx)
    n_bogi = replace_boilerplate_tables(out_hwpx)
    print(f"  조건표: {n_cond}개, 보기표: {n_bogi}개")

    kb = out_hwpx.stat().st_size // 1024
    print(f"\n완료: {out_hwpx.name}  ({kb}KB)")

    # Step 10: 자동 검증
    verify = _verify(segments, out_hwpx)
    _print_verify(verify)

    return out_hwpx, verify
