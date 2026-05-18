"""
Cycle 15h — 클로바 OCR + LLM 후처리 + 그림 캡처 빌드.
광주고 v16 (page 1 우선).

측정 목표:
  1. v15 발견 오자 → LLM/클로바 자동 교정 여부
  2. 그림 캡처 정확도
  3. 비용 실측
  4. 회귀: v16 page 2~N scripts = v15 page 2~N scripts

안전장치:
  - 비용 cap $5/일
  - 모든 API 호출 log/cycle_15h/ 저장
  - 광주고 v15 baseline 회귀 검증
"""
import hashlib
import io
import re
import sys
import time
import traceback
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from src.text_only.ocr_fallback import apply_fallback
from src.text_only.text_builder import build_from_markdown
from src.text_only.page_extractor import compare_scripts, get_md_page_range, get_md_page
from src.ocr.clova_ocr import ocr_image_bytes
from src.ocr.llm_postprocess import postprocess_markdown
from src.ocr.cost_guard import CostGuard, CostCapError
from src.common.image_extractor import extract_images

ROOT     = Path(__file__).resolve().parent.parent
SRC_DIR  = ROOT / "samples" / "11b"
PROD_DIR = ROOT / "samples" / "11b_production"
LOG_DIR  = ROOT / "log" / "cycle_15h"
IMG_DIR  = LOG_DIR / "images" / "광주고"

SOURCE   = "2025_1_1_b_공수1_광주고"
VER      = "v16"
PREV     = "v15"
V15_SHA  = "0c95a49f296b"

# page 1 스크립트 개수 (v15에서 확인된 값)
# v15에서 문항 1~4가 page1에 있고, 그 수식 스크립트가 약 30~50개
PAGE1_SCRIPT_COUNT = 30  # 추후 정밀 카운트로 업데이트


def _xml_sha(hwpx_path: Path) -> str:
    with zipfile.ZipFile(hwpx_path) as zf:
        data = zf.read("Contents/section0.xml")
    return hashlib.sha256(data).hexdigest()[:12]


def _render_page1_png(pdf_path: Path) -> bytes | None:
    """PDF page 1을 PNG 바이트로 렌더링 (클로바 OCR용)."""
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
        doc.close()
        return pix.tobytes("png")
    except Exception as e:
        print(f"  [경고] PDF 렌더링 실패: {e}")
        return None


guard = CostGuard(cap_usd=5.0)
safe   = re.sub(r"[^\w\-]+", "_", SOURCE.strip("[]")).strip("_")
cache  = SRC_DIR / f"_{safe}_raw.md"
if not cache.exists():
    cache = SRC_DIR / f"_{SOURCE}_raw.md"
template = next(SRC_DIR.glob("*.hwpx"), None)
out_hwpx = PROD_DIR / f"{SOURCE}_{VER}.hwpx"
pdf      = SRC_DIR / f"[{SOURCE}].pdf"

print(f"\n{'='*60}")
print(f"[광주고] → {VER}  (Cycle 15h: 클로바 + LLM)")

if out_hwpx.exists():
    print(f"  {VER} 이미 존재 ({_xml_sha(out_hwpx)}) — 재빌드하려면 파일 수동 제거")
    sys.exit(0)

if not cache.exists():
    print("  캐시 없음 — 중단")
    sys.exit(1)

cost_report: dict = {}

# ── Step 1: LLM 후처리 ────────────────────────────────────────────────
print("\n[1/4] LLM 후처리")
md_raw = cache.read_text(encoding="utf-8")
try:
    guard.check_or_raise("llm")
    md_llm, llm_meta = postprocess_markdown(md_raw, log_stem=f"광주고_{VER}")
    cost_report["llm"] = llm_meta
    if llm_meta.get("skipped"):
        print(f"  SKIP: {llm_meta.get('reason')}")
    else:
        print(f"  완료: ${llm_meta['cost_usd']:.4f}  {llm_meta['output_tokens']} 토큰")
        guard.record("llm", llm_meta["cost_usd"])
except CostCapError as e:
    print(f"  [비용 cap] {e}")
    md_llm = md_raw

# ── Step 2: 클로바 OCR cross-check (page 1 PNG) ───────────────────────
print("\n[2/4] 클로바 OCR")
clova_result = None
if pdf.exists():
    try:
        guard.check_or_raise("clova")
        png_bytes = _render_page1_png(pdf)
        if png_bytes:
            clova_result = ocr_image_bytes(png_bytes, "png", log_stem=f"광주고_{VER}_p1")
            clova_cost = 0.002  # 네이버 클로바 과금 약 $0.002/페이지
            guard.record("clova", clova_cost)
            cost_report["clova_pages"] = 1
            kor_count = len(clova_result.korean_fields())
            print(f"  완료: 한글 필드 {kor_count}개")
        else:
            print("  PDF 렌더링 실패 — 클로바 skip")
    except CostCapError as e:
        print(f"  [비용 cap] {e}")
    except Exception as e:
        print(f"  [오류] {e}")
else:
    print(f"  PDF 없음 ({pdf.name}) — 클로바 skip")

# ── Step 3: 그림 캡처 ──────────────────────────────────────────────────
print("\n[3/4] 그림 캡처")
extracted_images = []
if pdf.exists():
    try:
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        extracted_images = extract_images(pdf, IMG_DIR, dpi=150, pages=[0])
        print(f"  page 1 그림: {len(extracted_images)}개")
        for img in extracted_images:
            tag = f"문항{img.item_no}" if img.item_no else "?번"
            print(f"    {tag}: {img.image_path.name}  ({img.bbox.width:.0f}×{img.bbox.height:.0f}pt)")
    except Exception as e:
        print(f"  [오류] {e}")
else:
    print("  PDF 없음 — 그림 캡처 skip")

# ── Step 4: HWPX 빌드 ─────────────────────────────────────────────────
print("\n[4/4] HWPX 빌드")
try:
    buf = io.StringIO()
    t0  = time.time()
    with redirect_stdout(buf):
        md_proc = apply_fallback(md_llm, pdf)
        r = build_from_markdown(md_proc, out_hwpx, template)
    elapsed = time.time() - t0

    pipeline_out = buf.getvalue()
    xml_sha  = _xml_sha(out_hwpx)
    size_kb  = out_hwpx.stat().st_size // 1024

    print(f"  완료: p={r['paragraphs']} eq={r['equations']}  {size_kb}KB  {elapsed:.1f}s")
    print(f"  xml_sha: {xml_sha}")
    if pipeline_out.strip():
        print(f"  pipeline:\n{pipeline_out.strip()}")

    # ── 회귀 검증 ───────────────────────────────────────────────────────
    prev_hwpx = PROD_DIR / f"{SOURCE}_{PREV}.hwpx"
    if prev_hwpx.exists():
        diffs = compare_scripts(prev_hwpx, out_hwpx, start_idx=PAGE1_SCRIPT_COUNT)
        if diffs:
            print(f"\n  ⚠️  page 2~N 스크립트 변경 {len(diffs)}건:")
            for d in diffs[:5]:
                if d["idx"] == -1:
                    print(f"    총계: {d['a']} → {d['b']}")
                else:
                    print(f"    #{d['idx']}: {d['a'][:60]} → {d['b'][:60]}")
        else:
            print(f"\n  ✅ page 2~N 스크립트 동일 (회귀 통과)")

    # v15 baseline 확인
    if xml_sha == V15_SHA:
        print(f"  [주의] v16 sha = v15 sha — LLM/클로바 교정이 없었음")
    else:
        print(f"  v15: {V15_SHA} → v16: {xml_sha}  (변경 있음)")

except Exception:
    print(f"  오류:\n{traceback.format_exc()}")
    sys.exit(1)

# ── 비용 요약 ────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print("비용 요약")
total = guard.total_today()
summary = guard.summary()
for svc, cost in summary.items():
    print(f"  {svc}: ${cost:.4f}")
print(f"  오늘 합계: ${total:.4f} / $5.00")
print(f"  그림 캡처: {len(extracted_images)}개 → {IMG_DIR}")
print(f"  v16 sha: {xml_sha}")
print("=" * 60)
