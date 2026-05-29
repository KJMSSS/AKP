"""
광주여고 v1(Cycle 16 crop OCR 첫 빌드) 빌드 + 측정.

출력:
  samples/11b_production/2025_1_1_b_공수1_광주여고_v5.hwpx
  log/cycle_16/광주여고_baseline_철학결정_데이터.md
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import time
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")

ROOT      = Path(__file__).resolve().parent.parent
SRC_DIR   = ROOT / "samples" / "11b"
PROD_DIR  = ROOT / "samples" / "11b_production"
CROP_ROOT = ROOT / "log" / "cycle_16" / "crops"
LOG_DIR   = ROOT / "log" / "cycle_16"

SCHOOL   = "광주여고"
SOURCE   = f"2025_1_1_b_공수1_{SCHOOL}"
CROP_DIR = CROP_ROOT / SCHOOL

GOLD_MANIFEST = ROOT / "data" / "gold_manifest" / f"{SCHOOL}.json"


# ── 측정 헬퍼 ─────────────────────────────────────────────────────────────────

def _hwpx_stats(hwpx_path: Path) -> dict:
    """v5 HWPX에서 수식·선택지·그림 수 추출."""
    with zipfile.ZipFile(str(hwpx_path)) as zf:
        xml = zf.read("Contents/section0.xml").decode("utf-8", errors="ignore")
        names = zf.namelist()
    return {
        "hp_script_count": xml.count("<hp:script>"),
        "choice_marker_count": sum(xml.count(c) for c in "①②③④⑤"),
        "bindata_count": sum(1 for n in names if n.startswith("BinData/")),
        "file_sha": hashlib.sha1(hwpx_path.read_bytes()).hexdigest()[:16],
        "file_kb": hwpx_path.stat().st_size // 1024,
    }


def _extract_costs(log_text: str) -> dict:
    """빌드 로그에서 비용 추출."""
    costs = {}
    # LLM 후처리 비용
    m = re.search(r'완료 \(\$([0-9.]+)\)', log_text)
    if m:
        costs["llm_usd"] = float(m.group(1))
    # 선택지 정규화 비용
    m = re.search(r'\[choices\] LLM 완료: \$([0-9.]+)', log_text)
    if m:
        costs["choices_usd"] = float(m.group(1))
    # bbox Vision 비용 (없으면 0 — bbox는 Claude Vision)
    costs.setdefault("llm_usd", 0.0)
    costs.setdefault("choices_usd", 0.0)
    return costs


def _extract_parse_result(log_text: str) -> dict:
    """빌드 로그에서 파싱 결과 추출."""
    result = {}
    m = re.search(r'객관식 (\d+)개, 서술형 (\d+)개', log_text)
    if m:
        result["obj_count"] = int(m.group(1))
        result["subj_count"] = int(m.group(2))
    m = re.search(r'조건표: (\d+)개, 보기표: (\d+)개', log_text)
    if m:
        result["cond_tables"] = int(m.group(1))
        result["bogi_tables"] = int(m.group(2))
    # 구조 검증
    result["struct_pass"] = "✓ PASS" in log_text
    # 골드 검증
    m = re.search(r'결과: (PASS ✓|FAIL ✗)', log_text)
    if m:
        result["gold_pass"] = "PASS" in m.group(1)
    m = re.search(r'hwpx 선택지 마커: (\d+) / 기대: (\d+)', log_text)
    if m:
        result["marker_actual"] = int(m.group(1))
        result["marker_expected"] = int(m.group(2))
    return result


# ── 빌드 실행 ──────────────────────────────────────────────────────────────────

def main():
    print(f"{'='*60}")
    print(f"광주여고 Cycle 16 v1 빌드 + 측정")
    print(f"{'='*60}\n")

    from src.pipeline.crop_ocr_builder import build_one_crop

    t0 = time.time()

    # 빌드 출력 캡처
    buf = io.StringIO()
    hwpx_path = None
    verify = {}

    try:
        # stdout을 양쪽으로 — 터미널 + 버퍼
        class Tee:
            def __init__(self, a, b): self.a, self.b = a, b
            def write(self, s): self.a.write(s); self.b.write(s)
            def flush(self): self.a.flush(); self.b.flush()

        orig_stdout = sys.stdout
        sys.stdout = Tee(orig_stdout, buf)
        try:
            hwpx_path, verify = build_one_crop(
                source=SOURCE,
                src_dir=SRC_DIR,
                prod_dir=PROD_DIR,
                crop_dir=CROP_DIR,
            )
        finally:
            sys.stdout = orig_stdout
    except Exception as e:
        import traceback
        print(f"\n[BUILD ERROR] {e}")
        traceback.print_exc()
        sys.exit(1)

    elapsed = time.time() - t0
    log_text = buf.getvalue()

    # ── 측정 ──────────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("측정 데이터 수집")
    print(f"{'='*60}")

    v5_stats = _hwpx_stats(hwpx_path) if hwpx_path else {}
    costs = _extract_costs(log_text)
    parse = _extract_parse_result(log_text)

    # 골드 manifest 로드
    gold = {}
    if GOLD_MANIFEST.exists():
        gold = json.loads(GOLD_MANIFEST.read_text(encoding="utf-8"))

    gold_eq  = gold.get("total_equations", 0)
    gold_ch  = gold.get("choice_marker_total", 0)
    gold_bin = gold.get("total_bin_files", 0)
    gold_pic = gold.get("total_pics", 0)
    gold_obj = gold.get("obj_count", 0)
    gold_sub = gold.get("subj_count", 0)

    v5_eq  = v5_stats.get("hp_script_count", 0)
    v5_ch  = v5_stats.get("choice_marker_count", 0)
    v5_bin = v5_stats.get("bindata_count", 0)

    eq_acc  = round(min(v5_eq, gold_eq) / gold_eq * 100, 1) if gold_eq else 0
    ch_acc  = round(min(v5_ch, gold_ch) / gold_ch * 100, 1) if gold_ch else 0
    pic_acc = round(min(v5_bin, gold_bin) / gold_bin * 100, 1) if gold_bin else 0

    total_cost = costs.get("llm_usd", 0) + costs.get("choices_usd", 0)

    # 화면 출력
    print(f"\n[문제 구조]")
    print(f"  객관식: {parse.get('obj_count','?')} / 골드 {gold_obj}  |  서술형: {parse.get('subj_count','?')} / 골드 {gold_sub}")
    print(f"\n[수식]")
    print(f"  v5={v5_eq}개 / 골드={gold_eq}개  → {eq_acc}%")
    print(f"\n[선택지 마커]")
    print(f"  v5={v5_ch}개 / 골드={gold_ch}개  → {ch_acc}%")
    print(f"\n[그림 BinData]")
    print(f"  v5={v5_bin}개 / 골드={gold_bin}개 (pic={gold_pic}) → {pic_acc}%")
    print(f"\n[검증]")
    print(f"  HWPX 구조: {'✓ PASS' if parse.get('struct_pass') else '✗ FAIL'}")
    print(f"  골드 정합: {'✓ PASS' if parse.get('gold_pass') else '✗ FAIL'} "
          f"({parse.get('marker_actual','?')}/{parse.get('marker_expected','?')})")
    print(f"\n[비용]")
    print(f"  LLM 후처리: ${costs.get('llm_usd',0):.4f}")
    print(f"  선택지 정규화: ${costs.get('choices_usd',0):.4f}")
    print(f"  합계: ${total_cost:.4f}  (Mathpix 별도 — API 계정 확인)")
    print(f"\n[파일]")
    print(f"  sha={v5_stats.get('file_sha','?')}  {v5_stats.get('file_kb','?')}KB  {elapsed:.0f}s")

    # ── 보고서 작성 ───────────────────────────────────────────────────────────
    report_path = LOG_DIR / "광주여고_baseline_철학결정_데이터.md"
    import datetime
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    report = f"""# 광주여고 Cycle 16 v1 baseline 측정 데이터

**빌드 일시**: {now}
**목적**: 철학 결정 데이터 — PDF 품질·OCR 정확도 측정 기준점

---

## 골드 기준값 (samples/11b/광주여고.hwpx)

| 항목 | 값 |
|------|----|
| 객관식 | {gold_obj}개 |
| 서술형 | {gold_sub}개 |
| 수식 (hp:script) | {gold_eq}개 |
| 선택지 마커 총수 | {gold_ch}개 |
| 그림 BinData | {gold_bin}개 (pic {gold_pic}개) |

---

## v1 빌드 결과

### 문제 구조

| 항목 | v5 | 골드 |
|------|----|------|
| 객관식 | {parse.get('obj_count','?')} | {gold_obj} |
| 서술형 | {parse.get('subj_count','?')} | {gold_sub} |

### 수식 정확도

| 항목 | v5 | 골드 | 정확도 |
|------|----|------|--------|
| hp:script 수 | {v5_eq} | {gold_eq} | **{eq_acc}%** |

> 주의: hp:script 수 비교는 "얼마나 많이 수식을 뽑았나"이며, 수식 내용 정확도는 아님.

### 보기(선택지) 정확도

| 항목 | v5 | 골드 | 정확도 |
|------|----|------|--------|
| 선택지 마커 ①~⑤ | {v5_ch} | {gold_ch} | **{ch_acc}%** |

### 그림 캡처율

| 항목 | v5 | 골드 | 캡처율 |
|------|----|------|--------|
| BinData 파일 수 | {v5_bin} | {gold_bin} | **{pic_acc}%** |

### 검증 결과

| 항목 | 결과 |
|------|------|
| HWPX 구조 (validate_hwpx) | {'✓ PASS' if parse.get('struct_pass') else '✗ FAIL'} |
| 골드 manifest 정합 | {'✓ PASS' if parse.get('gold_pass') else f"✗ FAIL ({parse.get('marker_actual','?')}/{parse.get('marker_expected','?')})"} |
| 조건 박스 표 | {parse.get('cond_tables', 0)}건 |
| 보기 박스 표 | {parse.get('bogi_tables', 0)}건 |

---

## 비용

| 항목 | 비용 |
|------|------|
| LLM 후처리 (Anthropic) | ${costs.get('llm_usd',0):.4f} |
| 선택지 정규화 (Anthropic) | ${costs.get('choices_usd',0):.4f} |
| **Anthropic 합계** | **${total_cost:.4f}** |
| Mathpix OCR | API 계정에서 별도 확인 |
| bbox Vision (Claude) | bbox 감지 API 로그 별도 확인 |

---

## 파일 정보

| 항목 | 값 |
|------|----|
| 출력 파일 | `{hwpx_path.name if hwpx_path else 'N/A'}` |
| 파일 sha1 | `{v5_stats.get('file_sha','?')}` |
| 파일 크기 | {v5_stats.get('file_kb','?')}KB |
| 빌드 시간 | {elapsed:.0f}초 |

---

## PDF 품질 지표

> pdf_quality_analyzer 미구현 — 수동 판정

| 항목 | 판정 |
|------|------|
| PDF 종류 | 인쇄 PDF (벡터) / 사진 PDF — 확인 필요 |
| 평균 DPI | 측정 필요 |
| 기울어짐 | 측정 필요 |
| 노이즈 | 측정 필요 |

---

## 메모

- 광주여고는 `score_position: "없음"` (골드에 [N점] 배점 표기 없음)
  → raw.md에도 배점 없으면 파싱 정상, 있으면 파이프라인 처리 확인 필요
- 골드 그림 4개 — 12번 원기둥 문제 그림 등 포함 예상
- 다음 단계: 학원장 v5 열어서 수식·그림 육안 확인
"""

    report_path.write_text(report, encoding="utf-8")
    print(f"\n보고서 저장: {report_path}")
    print(f"\n{'='*60}")
    print(f"STAGE 1.3 광주여고 완료")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
