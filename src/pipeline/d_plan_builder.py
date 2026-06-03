"""
D안 풀코스 빌드 파이프라인.

흐름:
  크롭 PNG
    → [1차] Claude Vision OCR (텍스트 + 그림 bbox 동시)
    → [2차] Mathpix OCR (수식 재확인)
    → 병합 (규칙 기반 + 선택적 LLM)
    → raw.md 조합
    → [3차] 그림 재생성 (matplotlib PNG)
    → 파싱 → 선택지 정규화 → LLM 후처리
    → fallback + HWPX 빌드
    → 표 삽입 → 그림 삽입 → 검증
"""
from __future__ import annotations

import io
import json
import os
import re
import zipfile
from collections import defaultdict
from contextlib import redirect_stdout
from pathlib import Path

import fitz

from src.pipeline.bbox_detector import BBoxDetector, THUMB_DPI
from src.pipeline.crop_ocr_builder import (
    _crop_problem, _adjust_bboxes, _split_and_merge_ocr,
    _build_raw_md, _verify, _print_verify,
    CROP_DPI, CROP_SCALE, SUBJ_OFFSET,
)
from src.ocr.vision_problem_ocr import ocr_problem_crop, VisionOCRResult
from src.common.ocr.mathpix_client import MathpixClient
from src.ocr.ocr_merger import merge_all
from src.ocr.figure_reconstructor import reconstruct_all_figures
from src.text_only.problem_segmenter import parse_problems, rebuild_markdown
from src.ocr.choice_normalizer import normalize_choices
from src.text_only.ocr_fallback import apply_fallback
from src.text_only.text_builder import build_from_markdown
from src.ocr.llm_postprocess import postprocess_markdown
from src.common.hwpx_table_inserter import replace_condition_tables, replace_boilerplate_tables
from src.common.hwpx_namespace_fixer import fix_hwpx_namespaces
from src.common.hwpx_validator import validate_hwpx as _hwpx_struct_validate
from src.common.hwpx_image_inserter import insert_figure_placeholder


# ── 캐시 경로 헬퍼 ────────────────────────────────────────────────────────────

def _vision_cache_path(ocr_dir: Path, num: int) -> Path:
    return ocr_dir / f"prob_{num}_vision.json"

def _merged_cache_path(ocr_dir: Path, num: int) -> Path:
    return ocr_dir / f"prob_{num}_merged.md"


# ── Step 2A: Claude Vision 1차 OCR ───────────────────────────────────────────

def _run_vision_ocr(
    crop_dir: Path,
    ocr_dir: Path,
    bboxes: dict[int, dict],
    api_key: str,
    force: bool = False,
) -> dict[int, VisionOCRResult]:
    """Vision 1차 OCR. 캐시 있으면 재사용."""
    from src.ocr.vision_problem_ocr import VisionOCRResult
    results: dict[int, VisionOCRResult] = {}

    for num in sorted(bboxes):
        cache = _vision_cache_path(ocr_dir, num)
        crop_png = crop_dir / f"prob_{num}.png"

        if cache.exists() and not force:
            data = json.loads(cache.read_text(encoding='utf-8'))
            results[num] = VisionOCRResult(**data)
            label = f"서술형{num - SUBJ_OFFSET}" if num >= SUBJ_OFFSET else f"{num}번"
            fig_tag = "그림O" if results[num].has_figure else "그림X"
            print(f"  {label}: 캐시 {fig_tag}")
            continue

        if not crop_png.exists():
            print(f"  {num}번: 크롭 없음 — 건너뜀")
            continue

        label = f"서술형{num - SUBJ_OFFSET}" if num >= SUBJ_OFFSET else f"{num}번"
        print(f"  {label}: Vision OCR...", end=" ", flush=True)
        result = ocr_problem_crop(crop_png, str(num), api_key=api_key)
        results[num] = result

        fig_tag = "그림O" if result.has_figure else "그림X"
        preview = result.text[:40].replace('\n', ' ')
        print(f"{fig_tag}  {preview}")

        # 캐시 저장
        cache.write_text(json.dumps({
            "problem_no": result.problem_no,
            "text": result.text,
            "has_figure": result.has_figure,
            "figure_bbox": result.figure_bbox,
            "raw_response": result.raw_response,
        }, ensure_ascii=False), encoding='utf-8')

    return results


# ── Step 2B: Mathpix 2차 OCR ─────────────────────────────────────────────────

def _run_mathpix_ocr(
    crop_dir: Path,
    ocr_dir: Path,
    bboxes: dict[int, dict],
    mp: MathpixClient,
    force: bool = False,
) -> dict[int, str]:
    """Mathpix 2차 OCR. 기존 A안 캐시(prob_N.md)도 재사용."""
    results: dict[int, str] = {}

    for num in sorted(bboxes):
        # 기존 A안 캐시 (prob_N.md) 우선 사용
        old_cache = ocr_dir / f"prob_{num}.md"
        crop_png  = crop_dir / f"prob_{num}.png"

        if old_cache.exists() and not force:
            results[num] = old_cache.read_text(encoding='utf-8')
            label = f"서술형{num - SUBJ_OFFSET}" if num >= SUBJ_OFFSET else f"{num}번"
            print(f"  {label}: Mathpix 캐시")
            continue

        if not crop_png.exists():
            results[num] = ''
            continue

        label = f"서술형{num - SUBJ_OFFSET}" if num >= SUBJ_OFFSET else f"{num}번"
        print(f"  {label}: Mathpix OCR...", end=" ", flush=True)
        raw  = mp.raw_ocr_image(crop_png)
        text = raw.get("mmd", "") or raw.get("text", "")
        results[num] = text
        old_cache.write_text(text, encoding='utf-8')
        print(f"{len(text)}자")

    return results


# ── Step 3: 병합 ──────────────────────────────────────────────────────────────

def _run_merge(
    vision_results: dict[int, VisionOCRResult],
    mathpix_mmds: dict[int, str],
    ocr_dir: Path,
    client=None,
    force: bool = False,
) -> dict[int, str]:
    """Vision + Mathpix 병합. 캐시 있으면 재사용."""
    merged: dict[int, str] = {}
    vision_texts = {num: r.text for num, r in vision_results.items()}

    for num in sorted(vision_texts):
        cache = _merged_cache_path(ocr_dir, num)
        if cache.exists() and not force:
            merged[num] = cache.read_text(encoding='utf-8')
            label = f"서술형{num - SUBJ_OFFSET}" if num >= SUBJ_OFFSET else f"{num}번"
            print(f"  {label}: 병합 캐시")
            continue

        v_text = vision_texts.get(num, '')
        mp_mmd = mathpix_mmds.get(num, '')

        from src.ocr.ocr_merger import merge_vision_mathpix
        text = merge_vision_mathpix(v_text, mp_mmd, client=client)
        merged[num] = text
        cache.write_text(text, encoding='utf-8')

        label = f"서술형{num - SUBJ_OFFSET}" if num >= SUBJ_OFFSET else f"{num}번"
        print(f"  {label}: 병합 완료 ({len(text)}자)")

    return merged


# ── 메인 파이프라인 ───────────────────────────────────────────────────────────

def build_one_d(
    source: str,
    src_dir: Path,
    prod_dir: Path,
    crop_dir: Path,
    preprocess_fn=None,
    log_stem: str | None = None,
    verbose: bool = True,
    force_vision: bool = False,
    force_mathpix: bool = False,
    force_merge: bool = False,
    force_figure: bool = False,
    no_figure_regen: bool = False,
    llm_merge: bool = True,
) -> tuple[Path, dict]:
    """
    D안 풀코스: Claude Vision 1차 + Mathpix 2차 + 병합 + 그림 재생성 → HWPX.

    force_*    : 각 단계 캐시 무시 재실행
    no_figure_regen : True면 그림 재생성 건너뛰고 원본 크롭 사용
    llm_merge  : True면 수식 개수 불일치 시 Haiku로 병합 시도
    """
    import anthropic

    api_key  = os.environ.get("ANTHROPIC_API_KEY", "")
    client   = anthropic.Anthropic(api_key=api_key)

    pdf      = src_dir / f"[{source}].pdf"
    template = src_dir / f"[{source}].hwpx"
    out_hwpx = prod_dir / f"{source}_vD.hwpx"
    thumb_dir = crop_dir / "thumbs"
    ocr_dir   = crop_dir / "ocr"
    fig_d_dir = crop_dir / "figures_d"
    ocr_dir.mkdir(parents=True, exist_ok=True)

    if log_stem is None:
        log_stem = source.replace("2025_1_1_b_공수1_", "")

    sep = "=" * 55
    print(f"\n{sep}\n[D안] [{source}]\n{sep}")

    # ── Step 1: bbox 감지 ─────────────────────────────────────────────────────
    print("\n=== Step 1: bbox 감지 ===")
    detector = BBoxDetector()
    bboxes   = detector.detect_all(pdf, thumb_dir, verbose=verbose)
    bboxes   = _adjust_bboxes(bboxes, pdf)
    print(f"  문제: {sorted(bboxes.keys())}")

    # 크롭 PNG 생성 (없는 것만)
    for num in sorted(bboxes):
        crop_path = crop_dir / f"prob_{num}.png"
        if not crop_path.exists():
            img = _crop_problem(pdf, bboxes[num]["page"], bboxes[num])
            crop_path.write_bytes(img)

    # ── Step 2A: Claude Vision 1차 OCR ───────────────────────────────────────
    print("\n=== Step 2A: Claude Vision 1차 OCR ===")
    vision_results = _run_vision_ocr(
        crop_dir, ocr_dir, bboxes, api_key, force=force_vision
    )

    # ── Step 2B: Mathpix 2차 OCR ─────────────────────────────────────────────
    print("\n=== Step 2B: Mathpix 2차 OCR ===")
    mp = MathpixClient()
    mathpix_mmds = _run_mathpix_ocr(
        crop_dir, ocr_dir, bboxes, mp, force=force_mathpix
    )

    # ── Step 3: 병합 ─────────────────────────────────────────────────────────
    print("\n=== Step 3: Vision + Mathpix 병합 ===")
    merge_client = client if llm_merge else None
    merged_texts = _run_merge(
        vision_results, mathpix_mmds, ocr_dir,
        client=merge_client, force=force_merge
    )

    # ── Step 4: raw.md 조합 ──────────────────────────────────────────────────
    print("\n=== Step 4: raw.md 조합 ===")
    raw_cache = crop_dir / "raw_d.md"
    if raw_cache.exists() and not force_merge:
        md_raw = raw_cache.read_text(encoding='utf-8')
        print(f"  raw_d.md 캐시: {len(md_raw)}자")
    else:
        md_raw = _build_raw_md(merged_texts)
        md_raw = re.sub(r'[【（(]([\d.]+)점[）)】]', r'[\1점]', md_raw)
        if preprocess_fn:
            md_raw = preprocess_fn(md_raw)
        raw_cache.write_text(md_raw, encoding='utf-8')
        print(f"  raw_d.md 생성: {len(md_raw)}자")

    # ── Step 4.5: 그림 재생성 ─────────────────────────────────────────────────
    print("\n=== Step 4.5: 그림 재생성 ===")
    figure_map: dict[str, Path] = {}
    has_fig_nums = [num for num, r in vision_results.items() if r.has_figure and r.figure_bbox]
    print(f"  그림 있는 문제: {has_fig_nums}")

    if has_fig_nums and not no_figure_regen:
        figure_map = reconstruct_all_figures(
            crop_dir, vision_results, fig_d_dir, client, force=force_figure
        )
        print(f"  재생성 완료: {list(figure_map.keys())}")
    elif has_fig_nums and no_figure_regen:
        # 재생성 건너뜀 — 원본 크롭에서 bbox 영역 추출
        from src.ocr.figure_reconstructor import _crop_figure
        from PIL import Image
        fig_d_dir.mkdir(parents=True, exist_ok=True)
        for num in has_fig_nums:
            r = vision_results[num]
            crop_png = crop_dir / f"prob_{num}.png"
            out_png  = fig_d_dir / f"fig_{num}.png"
            if crop_png.exists() and not out_png.exists():
                fig_bytes = _crop_figure(crop_png, r.figure_bbox)
                out_png.write_bytes(fig_bytes)
            if out_png.exists():
                figure_map[str(num)] = out_png
        print(f"  원본 크롭 사용: {list(figure_map.keys())}")

    # ── Step 5: 파싱 ─────────────────────────────────────────────────────────
    print("\n=== Step 5: 파싱 ===")
    header, segments = parse_problems(md_raw)
    obj_cnt  = sum(1 for s in segments if not s.is_subjective)
    subj_cnt = sum(1 for s in segments if s.is_subjective)
    print(f"  객관식 {obj_cnt}개, 서술형 {subj_cnt}개")

    # ── Step 6: 선택지 정규화 ────────────────────────────────────────────────
    print("\n=== Step 6: 선택지 정규화 ===")
    segments = normalize_choices(segments, log_stem=log_stem)

    # ── Step 7: rebuild + LLM 후처리 ─────────────────────────────────────────
    md_rebuilt = rebuild_markdown(header, segments, figure_items=set(figure_map.keys()) or None)
    print("\n=== Step 7: LLM 후처리 ===")
    md_llm, llm_meta = postprocess_markdown(md_rebuilt, log_stem=log_stem)
    if llm_meta.get("skipped"):
        print(f"  스킵: {llm_meta.get('reason')}")
    else:
        print(f"  완료 (${llm_meta.get('cost_usd', 0):.4f})")

    # ── Step 8: fallback + HWPX 빌드 ─────────────────────────────────────────
    print("\n=== Step 8: fallback + HWPX 빌드 ===")
    buf = io.StringIO()
    with redirect_stdout(buf):
        md_proc = apply_fallback(md_llm, pdf)
        build_from_markdown(md_proc, out_hwpx, template)
    out = buf.getvalue()
    print(out[-400:] if out else "  (출력 없음)")

    # ── Step 9: 표 삽입 ──────────────────────────────────────────────────────
    print("\n=== Step 9: 표 삽입 ===")
    n_cond = replace_condition_tables(out_hwpx)
    n_bogi = replace_boilerplate_tables(out_hwpx)
    print(f"  조건표: {n_cond}개, 보기표: {n_bogi}개")

    # ── Step 9.5: 그림 삽입 ──────────────────────────────────────────────────
    if figure_map:
        print("\n=== Step 9.5: 그림 삽입 ===")
        n_fig = 0
        for item_no in sorted(figure_map, key=lambda x: int(x) if x.isdigit() else 999):
            try:
                insert_figure_placeholder(out_hwpx, item_no, figure_map[item_no])
                n_fig += 1
                print(f"  {item_no}번 삽입 완료")
            except Exception as e:
                print(f"  [그림] {item_no}번 삽입 실패: {e}")
        print(f"  그림 {n_fig}개 삽입")

    # ── Step 10: HWPX 검증 ───────────────────────────────────────────────────
    fix_hwpx_namespaces(str(out_hwpx))
    print("\n=== Step 10: HWPX 구조 검증 ===")
    errs = _hwpx_struct_validate(str(out_hwpx))
    if errs:
        for e in errs:
            print(f"  ✗ {e}")
    else:
        print("  ✓ PASS")

    # ── Step 11: 자동 검증 ───────────────────────────────────────────────────
    gold_path = Path(__file__).resolve().parents[2] / "data" / "gold_manifest" / f"{log_stem}.json"
    verify = _verify(segments, out_hwpx, gold_path=gold_path)
    _print_verify(verify)

    kb = out_hwpx.stat().st_size // 1024
    print(f"\n완료: {out_hwpx.name}  ({kb}KB)")

    return out_hwpx, verify
