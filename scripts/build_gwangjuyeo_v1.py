"""
Cycle 16 Step 1 — 광주여고 v1 baseline 빌드.

목적:
  - 정상 PDF 학교(광주여고)에서 현재 시스템이 잘 작동하는지 확인
  - gold HWPX(타이퍼 원본)와 자동 비교 → 학원장 검수 가이드
  - 광주고(사진 PDF)와 별개 트랙

변경사항 (Cycle 16 Step 0 반영):
  - temperature=0 (비결정성 차단)
  - corrections.json approved=true 34건 적용
  - 헤더 보호 (llm_postprocess.py)

측정:
  - gold vs v1 hp:t diff
  - gold vs v1 script diff
  - LLM 교정 건수 + 비용
  - layout_filter 발동 건수

비용 cap: $5
push 금지 (학원장 검수 OK 후)
"""
import hashlib
import io
import os
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
from src.text_only.page_extractor import compare_scripts, get_hwpx_scripts
from src.ocr.llm_postprocess import postprocess_markdown
from src.ocr.cost_guard import CostGuard, CostCapError
from src.common.image_extractor import extract_images
from src.common.hwpx_image_inserter import crop_figure_from_pdf, replace_placeholder_with_image

ROOT     = Path(__file__).resolve().parent.parent
SRC_DIR  = ROOT / "samples" / "11b"
PROD_DIR = ROOT / "samples" / "11b_production"
LOG_DIR  = ROOT / "log" / "cycle_16"

SOURCE  = "2025_1_1_b_공수1_광주여고"
VER     = "v13"

safe    = re.sub(r"[^\w\-]+", "_", SOURCE.strip("[]")).strip("_")
cache   = SRC_DIR / f"_{safe}_raw.md"
if not cache.exists():
    cache = SRC_DIR / f"_{SOURCE}_raw.md"
template = SRC_DIR / "[2025_1_1_b_공수1_광주여고].hwpx"   # gold = template
gold     = template
out_hwpx = PROD_DIR / f"{SOURCE}_{VER}.hwpx"
pdf      = SRC_DIR / f"[{SOURCE}].pdf"


def _xml_sha(hwpx_path: Path) -> str:
    with zipfile.ZipFile(hwpx_path) as zf:
        data = zf.read("Contents/section0.xml")
    return hashlib.sha256(data).hexdigest()[:12]


def _get_hp_t_texts(hwpx: Path) -> list[str]:
    with zipfile.ZipFile(hwpx) as zf:
        xml = zf.read("Contents/section0.xml").decode("utf-8")
    return re.findall(r"<hp:t[^>]*>([^<]+)</hp:t>", xml)


guard = CostGuard(cap_usd=5.0)

print(f"\n{'='*60}")
print(f"[광주여고] → {VER}  (Cycle 16: v13 그림삽입 content.hpf 수정 + ZIP 중복 수정)")

if out_hwpx.exists():
    print(f"  {VER} 이미 존재 ({_xml_sha(out_hwpx)}) — 재빌드하려면 수동 삭제")
    sys.exit(0)

if not cache.exists():
    print(f"  캐시 없음: {cache}")
    sys.exit(1)

if not template.exists():
    print(f"  template 없음: {template}")
    sys.exit(1)

# ── LLM 후처리 ────────────────────────────────────────────────────────────
print("\n[1/3] LLM 후처리 (temperature=0, 헤더 보호)")
md_raw = cache.read_text(encoding="utf-8")
md_llm = md_raw
llm_meta: dict = {}

try:
    guard.check_or_raise("llm")
    md_llm, llm_meta = postprocess_markdown(md_raw, log_stem=f"광주여고_{VER}")
    if llm_meta.get("skipped"):
        print(f"  SKIP: {llm_meta.get('reason')}")
    else:
        cost        = llm_meta["cost_usd"]
        corrections = llm_meta.get("corrections", 0)
        rejected    = llm_meta.get("rejected", 0)
        print(f"  완료: ${cost:.4f}  교정 {corrections}건 / 거부 {rejected}건")
        guard.record("llm", cost)
except CostCapError as e:
    print(f"  [비용 cap] {e}")

# ── 그림 캡처 ────────────────────────────────────────────────────────────
print("\n[2/3] 그림 캡처")
extracted_images = []
if pdf.exists():
    try:
        IMG_DIR = LOG_DIR / "images" / "광주여고_v1"
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        extracted_images = extract_images(pdf, IMG_DIR, dpi=150, pages=[0])
        print(f"  page 1 그림: {len(extracted_images)}개")
    except Exception as e:
        print(f"  [오류] {e}")
else:
    print(f"  PDF 없음 — skip")

# ── HWPX 빌드 ────────────────────────────────────────────────────────────
print("\n[3/3] HWPX 빌드")
try:
    buf = io.StringIO()
    t0  = time.time()
    with redirect_stdout(buf):
        md_proc = apply_fallback(md_llm, pdf)
        r       = build_from_markdown(md_proc, out_hwpx, template)
    elapsed = time.time() - t0

    xml_sha = _xml_sha(out_hwpx)
    size_kb = out_hwpx.stat().st_size // 1024
    pipeline_out = buf.getvalue()

    print(f"  완료: p={r['paragraphs']} eq={r['equations']}  {size_kb}KB  {elapsed:.1f}s")
    print(f"  xml_sha: {xml_sha}")
    if pipeline_out.strip():
        print(f"  pipeline:\n{pipeline_out.strip()}")

    # ── gold 비교 ─────────────────────────────────────────────────────
    print(f"\n  ── gold 비교 ({gold.name}) ──")
    gold_sha = _xml_sha(gold)
    print(f"  gold sha: {gold_sha}")
    print(f"  v1   sha: {xml_sha}")

    # hp:t diff
    t_gold = _get_hp_t_texts(gold)
    t_v1   = _get_hp_t_texts(out_hwpx)
    ht_diffs = [(i, a, b) for i, (a, b) in enumerate(zip(t_gold, t_v1)) if a != b]
    count_mismatch = abs(len(t_gold) - len(t_v1))

    print(f"\n  hp:t 개수: gold={len(t_gold)} / v1={len(t_v1)}"
          + (f"  ⚠️ {count_mismatch}개 차이" if count_mismatch else "  ✅ 동일"))
    print(f"  hp:t 내용 차이: {len(ht_diffs)}건")
    for i, a, b in ht_diffs[:20]:
        print(f"    #{i}: {repr(a[:50])} → {repr(b[:50])}")
    if len(ht_diffs) > 20:
        print(f"    ... (총 {len(ht_diffs)}건, 처음 20건만 표시)")

    # script diff
    diffs = compare_scripts(gold, out_hwpx, start_idx=0)
    print(f"\n  script 차이: {len(diffs)}건")
    for d in diffs[:10]:
        idx = d["idx"]
        if idx == -1:
            print(f"    총계: gold={d['a']} / v1={d['b']}")
        else:
            print(f"    #{idx}: {d['a'][:60]} → {d['b'][:60]}")

    # 보고서 저장
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    report_path = LOG_DIR / "광주여고_v1_baseline_diff.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# 광주여고 v1 baseline 비교 보고서\n\n")
        f.write(f"생성: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"## SHA\n- gold: `{gold_sha}`\n- v1: `{xml_sha}`\n\n")
        f.write(f"## hp:t\n- gold: {len(t_gold)}개 / v1: {len(t_v1)}개 / 차이: {len(ht_diffs)}건\n\n")
        if ht_diffs:
            f.write("### 내용 차이\n")
            for i, a, b in ht_diffs:
                f.write(f"- `#{i}`: `{a[:80]}` → `{b[:80]}`\n")
        f.write(f"\n## script\n- 차이: {len(diffs)}건\n\n")
        if diffs:
            f.write("### 차이 목록\n")
            for d in diffs:
                idx = d["idx"]
                if idx == -1:
                    f.write(f"- 총계: gold={d['a']} / v1={d['b']}\n")
                else:
                    f.write(f"- `#{idx}`: `{d['a'][:80]}` → `{d['b'][:80]}`\n")
        f.write(f"\n## LLM\n- 교정: {llm_meta.get('corrections', 'N/A')}건\n")
        f.write(f"- 거부: {llm_meta.get('rejected', 'N/A')}건\n")
        f.write(f"- 비용: ${llm_meta.get('cost_usd', 0):.4f}\n")
    print(f"\n  보고서: {report_path}")

    # ── 그림 삽입 (12번, 20번) ────────────────────────────────────────
    # Mathpix URL 좌표 (top_left_x, top_left_y, width, height, page)
    FIGURES = [
        dict(item="12", page=3,  mx=387, my=1836, mw=716,  mh=395),
        dict(item="20", page=5,  mx=304, my=1694, mw=874,  mh=874),
    ]
    if pdf.exists():
        print("\n  ── 그림 삽입 ──")
        fig_dir = LOG_DIR / "figures" / f"광주여고_{VER}"
        fig_dir.mkdir(parents=True, exist_ok=True)
        for fig in FIGURES:
            png = fig_dir / f"fig_{fig['item']}.png"
            try:
                crop_figure_from_pdf(
                    pdf, fig["page"],
                    fig["mx"], fig["my"], fig["mw"], fig["mh"],
                    png, render_dpi=300, mathpix_dpi=180,
                )
                # HWP 크기: Mathpix 비율 유지, width ≈ 17640 HWP (176.4pt)
                w_hpc = 17640
                h_hpc = round(w_hpc * fig["mh"] / fig["mw"])
                replace_placeholder_with_image(
                    out_hwpx, fig["item"], png,
                    w_hpc=w_hpc, h_hpc=h_hpc,
                )
            except Exception as e:
                print(f"  [pic] {fig['item']}번 오류: {e}")
    else:
        print("\n  ── 그림 삽입: PDF 없음 — skip")

    # 광주고 v17 (2차) sha 회귀 검증
    gj_v17 = PROD_DIR / "2025_1_1_b_공수1_광주고_v17.hwpx"
    EXPECTED_GJ_SHA = "e1c77bae1921"
    if gj_v17.exists():
        gj_sha = _xml_sha(gj_v17)
        match = "✅" if gj_sha == EXPECTED_GJ_SHA else "⚠️ 변경됨!"
        print(f"\n  광주고 v17 회귀: {gj_sha}  {match}")

except Exception:
    print(f"  오류:\n{traceback.format_exc()}")
    sys.exit(1)

# ── 비용 요약 ──────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print("비용 요약")
summary = guard.summary()
for svc, cost in summary.items():
    print(f"  {svc}: ${cost:.4f}")
print(f"  오늘 합계: ${guard.total_today():.4f} / $5.00")
print("=" * 60)
