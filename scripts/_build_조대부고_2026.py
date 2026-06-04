"""
조대부고 2026년 1학기 1차 a형 v1 빌드 + 가치 측정.

입력: samples/2026/[2026_1_1_a_공수1_조대부고].pdf
출력: samples/11b_production/2026_1_1_a_공수1_조대부고_v1.hwpx
기록: log/cycle_16/조대부고_2026_v1_가치측정.md

특이사항:
  - 이미지 PDF (텍스트 레이어 없음), ~300dpi
  - 골드 HWPX 없음 — 골드 비교 불가
  - 2026년 a형 (11b는 b형)
  - 템플릿: 광주제일고 HWPX 차용 (header.xml 기준)
"""
from __future__ import annotations

import hashlib
import io
import shutil
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
SRC_2026  = ROOT / "samples" / "2026"
SRC_11B   = ROOT / "samples" / "11b"
PROD_DIR  = ROOT / "samples" / "11b_production"
CROP_ROOT = ROOT / "log" / "cycle_16" / "crops"
LOG_DIR   = ROOT / "log" / "cycle_16"

SOURCE    = "2026_1_1_a_공수1_조대부고"
CROP_DIR  = CROP_ROOT / "조대부고_2026"

# 템플릿: 광주제일고 HWPX 차용 (samples/2026에 임시 복사)
TEMPLATE_SRC = SRC_11B / "[2025_1_1_b_공수1_광주제일고].hwpx"
TEMPLATE_TMP = SRC_2026 / f"[{SOURCE}].hwpx"

# 최종 출력 경로 (_v5.hwpx → _v1.hwpx 리네임)
OUT_V5  = PROD_DIR / f"{SOURCE}_v5.hwpx"
OUT_V1  = PROD_DIR / f"{SOURCE}_v1.hwpx"


def main():
    print(f"{'='*60}")
    print(f"조대부고 2026 v1 빌드 + 가치 측정")
    print(f"{'='*60}\n")

    # 임시 템플릿 복사
    print(f"템플릿 복사: {TEMPLATE_SRC.name} → {TEMPLATE_TMP.name}")
    shutil.copy2(str(TEMPLATE_SRC), str(TEMPLATE_TMP))

    from src.pipeline.crop_ocr_builder import build_one_crop

    t0 = time.time()

    class Tee:
        def __init__(self, a, b): self.a, self.b = a, b
        def write(self, s): self.a.write(s); self.b.write(s)
        def flush(self): self.a.flush(); self.b.flush()

    buf = io.StringIO()
    hwpx_path = None
    verify = {}

    orig = sys.stdout
    sys.stdout = Tee(orig, buf)
    try:
        hwpx_path, verify = build_one_crop(
            source=SOURCE,
            src_dir=SRC_2026,
            prod_dir=PROD_DIR,
            crop_dir=CROP_DIR,
            log_stem="조대부고_2026",
        )
    finally:
        sys.stdout = orig
        # 임시 템플릿 삭제
        if TEMPLATE_TMP.exists():
            TEMPLATE_TMP.unlink()

    elapsed = time.time() - t0
    log_text = buf.getvalue()

    # _v5.hwpx → _v1.hwpx 리네임
    if OUT_V5.exists():
        if OUT_V1.exists():
            OUT_V1.unlink()
        OUT_V5.rename(OUT_V1)
        hwpx_path = OUT_V1
        print(f"\n리네임: {OUT_V5.name} → {OUT_V1.name}")

    # ── 측정 ─────────────────────────────────────────────────────
    import re, json

    v5_eq = v5_ch = v5_bin = 0
    sha = ""
    if hwpx_path and hwpx_path.exists():
        with zipfile.ZipFile(str(hwpx_path)) as zf:
            xml = zf.read("Contents/section0.xml").decode("utf-8", errors="ignore")
            names = zf.namelist()
        v5_eq  = xml.count("<hp:script>")
        v5_ch  = sum(xml.count(c) for c in "①②③④⑤")
        v5_bin = sum(1 for n in names if n.startswith("BinData/"))
        sha    = hashlib.sha1(hwpx_path.read_bytes()).hexdigest()[:16]

    # 플레이스홀더 ★ 수
    placeholder_count = len(re.findall(r'★', log_text))

    # 문제 수
    obj_count = verify.get("obj_count", "?")
    subj_count = verify.get("subj_count", "?")
    marker_actual = verify.get("hwpx_choice_count", "?")
    struct_pass = "✓ PASS" in log_text

    # LLM 비용
    m = re.search(r'완료 \(\$([0-9.]+)\)', log_text)
    llm_cost = float(m.group(1)) if m else 0.0
    m = re.search(r'\[choices\] LLM 완료: \$([0-9.]+)', log_text)
    choices_cost = float(m.group(1)) if m else 0.0
    total_cost = llm_cost + choices_cost

    # 사전 적용
    m = re.search(r'\[corrections\] (\d+)건 적용', log_text)
    corrections = int(m.group(1)) if m else 0

    # 그림 표 수
    m = re.search(r'조건표: (\d+)개, 보기표: (\d+)개', log_text)
    cond_t = int(m.group(1)) if m else 0
    bogi_t = int(m.group(2)) if m else 0

    print(f"\n{'='*60}")
    print("가치 측정 결과")
    print(f"{'='*60}")
    print(f"  문제: 객관식 {obj_count}개 / 서술형 {subj_count}개")
    print(f"  선택지 마커: {marker_actual}개")
    print(f"  수식(hp:script): {v5_eq}개")
    print(f"  BinData(그림): {v5_bin}개")
    print(f"  HWPX 구조: {struct_pass}")
    print(f"  사전 교정: {corrections}건")
    print(f"  조건박스: {cond_t}개 / 보기박스: {bogi_t}개")
    print(f"  비용(Anthropic): ${total_cost:.4f}")
    print(f"  빌드 시간: {elapsed:.0f}초")
    print(f"  sha: {sha}")

    # ── 보고서 ─────────────────────────────────────────────────────
    report_path = LOG_DIR / "조대부고_2026_v1_가치측정.md"
    import datetime
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    report = f"""# 조대부고 2026 v1 가치 측정

**빌드 일시**: {now}
**목적**: 새 PDF(2026년, a형) AKP 처리 가치 측정

---

## PDF 정보

| 항목 | 값 |
|------|----|
| 파일 | `[2026_1_1_a_공수1_조대부고].pdf` |
| 크기 | 9,464KB |
| 페이지 수 | 6 |
| PDF 종류 | **이미지 PDF** (텍스트 레이어 없음) |
| 평균 DPI | ~300dpi |
| 회전 | 0° (보정 불필요) |
| 품질 판정 | 🟡 보통 |
| 비고 | 2026년 a형 (기존 18쌍은 b형), 골드 HWPX 없음 |

---

## 자동 빌드 결과

| 항목 | 값 |
|------|----|
| 출력 파일 | `{hwpx_path.name if hwpx_path else 'N/A'}` |
| 파일 sha | `{sha}` |
| HWPX 구조 검증 | {struct_pass} |

### 문제 구조

| 항목 | 값 |
|------|----|
| 객관식 | {obj_count}개 |
| 서술형 | {subj_count}개 |
| 선택지 마커 ①~⑤ | {marker_actual}개 |

> 골드 HWPX 없으므로 기대값 비교 불가. 선택지 마커 수는 참고값.

### 수식 / 그림

| 항목 | 값 |
|------|----|
| hp:script 수 | {v5_eq}개 |
| BinData(그림) | {v5_bin}개 |
| ★ 플레이스홀더 | {placeholder_count}개 |

### 후처리

| 항목 | 값 |
|------|----|
| 사전 교정 적용 | {corrections}건 |
| 조건 박스 표 | {cond_t}건 |
| 보기 박스 표 | {bogi_t}건 |

---

## 비용

| 항목 | 비용 |
|------|------|
| LLM 후처리 (Anthropic) | ${llm_cost:.4f} |
| 선택지 정규화 (Anthropic) | ${choices_cost:.4f} |
| **합계** | **${total_cost:.4f}** |
| Mathpix OCR | API 계정 별도 확인 |
| bbox Vision | API 별도 확인 |
| 빌드 총 시간 | {elapsed:.0f}초 |

---

## 학원장 복붙 작업용

- 출력 위치: `samples/11b_production/{hwpx_path.name if hwpx_path else 'N/A'}`
- 한글에서 열기: 정상 여부 육안 확인 필요
- 골드 없으므로 내용 정확성은 학원장 직접 PDF 대조 검수 필요

### PDF ↔ HWPX 문제 번호 매핑

> bbox 감지 결과에서 자동 추출됨.
> 실제 매핑은 빌드 로그 참조.

---

## 메모

- a형 vs b형: 기존 18쌍 학교는 모두 b형. 이 파일은 a형으로 내용/난이도 다를 수 있음
- 이미지 PDF OCR 특성상 선택지 일부 누락 가능 → 빌드 후 검수 필요
- 그림 캡처: 파이프라인 범위 외, 학원장 직접 삽입
"""

    report_path.write_text(report, encoding="utf-8")
    print(f"\n보고서 저장: {report_path}")
    print(f"\n{'='*60}")
    print(f"조대부고 2026 v1 빌드 완료")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
