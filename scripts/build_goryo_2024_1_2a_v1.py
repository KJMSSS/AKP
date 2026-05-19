"""
2024 고려고 1학기 2차 수하 v1 빌드 (문제 단위 파이프라인).

파일:
  raw.md : samples/2024/_광주_2024_1_2_a_수하_고려고_..._raw.md
  template : samples/2024/(광주)[2024_1_2_a_수하_고려고][...].hwpx  (학교 원본)
  gold     : samples/2024/(광주)[2024_1_2_a_수하_고려고][...][워드초벌][1차검수완료][손풀이][해설완료].hwpx
  output   : samples/2024/(광주)[2024_1_2_a_수하_고려고]_v1.hwpx
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

from src.text_only.problem_segmenter import parse_problems, rebuild_markdown
from src.ocr.choice_normalizer import normalize_choices
from src.text_only.ocr_fallback import apply_fallback
from src.text_only.text_builder import build_from_markdown
from src.text_only.page_extractor import compare_scripts
from src.ocr.llm_postprocess import postprocess_markdown
from src.ocr.cost_guard import CostGuard, CostCapError
from src.common.hwpx_table_inserter import (
    replace_condition_tables,
    replace_boilerplate_tables,
    TableSpec,
)

SRC_DIR  = Path(__file__).resolve().parent.parent / "samples" / "2024"
LOG_DIR  = Path(__file__).resolve().parent.parent / "log" / "2024_고려고"

SOURCE   = "(광주)[2024_1_2_a_수하_고려고][도형의 이동 ~ 합성함수와 역함수]"
VER      = "v1"

cache    = SRC_DIR / "_광주_2024_1_2_a_수하_고려고_도형의_이동_합성함수와_역함수_raw.md"
template = SRC_DIR / f"{SOURCE}.hwpx"
gold     = SRC_DIR / f"{SOURCE}[워드초벌][1차검수완료][손풀이][해설완료].hwpx"
pdf      = SRC_DIR / f"{SOURCE}.pdf"
out_hwpx = SRC_DIR / f"{SOURCE}_{VER}.hwpx"

DATA_TABLES: list[TableSpec] = []


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
print(f"[고려고 2024 수하] → {VER}  (문제 단위 파이프라인)")

if out_hwpx.exists():
    print(f"  {VER} 이미 존재 ({_xml_sha(out_hwpx)}) — 재빌드하려면 수동 삭제")
    sys.exit(0)

if not cache.exists():
    print(f"  캐시 없음: {cache}")
    sys.exit(1)

if not template.exists():
    print(f"  template 없음: {template}")
    sys.exit(1)

md_raw = cache.read_text(encoding="utf-8")

# OCR 오류 수정: "44. " → "14. " (14번 문제 번호 오인식)
md_raw = re.sub(r'^44[．.]\s', '14. ', md_raw, flags=re.MULTILINE)

# ── [1] 문제 분리 + 정렬 ─────────────────────────────────────────────────
print("\n[1/7] 문제 파싱 (parse_problems)")
header, segments = parse_problems(md_raw)
obj_cnt  = sum(1 for s in segments if not s.is_subjective)
subj_cnt = sum(1 for s in segments if s.is_subjective)
cond_cnt = sum(1 for s in segments if s.conditions)
bogi_cnt = sum(1 for s in segments if s.boilerplate)
img_cnt  = sum(1 for s in segments if s.images)
print(f"  발견: 객관식 {obj_cnt}개 / 서술형 {subj_cnt}개")
print(f"  조건 {cond_cnt}개 / 보기 {bogi_cnt}개 / 이미지 {img_cnt}개 문제")
nums = sorted(s.number for s in segments)
print(f"  번호: {nums}")

# ── [2] 선택지 정규화 ────────────────────────────────────────────────────
print("\n[2/7] 선택지 정규화 (normalize_choices)")
try:
    guard.check_or_raise("choices")
    segments = normalize_choices(segments, log_stem=f"고려고_2024_수하_{VER}")
    choices_ok = sum(1 for s in segments if not s.is_subjective and len(s.choices) == 5)
    print(f"  선택지 5개 완비: {choices_ok}/{obj_cnt}개 문제")
    guard.record("choices", 0.01)
except CostCapError as e:
    print(f"  [비용 cap] {e}")

# ── [3] rebuild_markdown ────────────────────────────────────────────────
print("\n[3/7] rebuild_markdown")
md_rebuilt = rebuild_markdown(header, segments)
line_cnt   = md_rebuilt.count("\n")
marker_cnt = md_rebuilt.count("【★")
print(f"  줄 수: {line_cnt} / 마커: {marker_cnt}개")

# ── [4] LLM 후처리 ───────────────────────────────────────────────────────
print("\n[4/7] LLM 후처리 (postprocess_markdown)")
md_llm = md_rebuilt
llm_meta: dict = {}
try:
    guard.check_or_raise("llm")
    md_llm, llm_meta = postprocess_markdown(md_rebuilt, log_stem=f"고려고_2024_수하_{VER}")
    if llm_meta.get("skipped"):
        print(f"  SKIP: {llm_meta.get('reason')}")
    else:
        print(f"  완료: ${llm_meta['cost_usd']:.4f}  교정 {llm_meta.get('corrections',0)}건 / 거부 {llm_meta.get('rejected',0)}건")
        guard.record("llm", llm_meta["cost_usd"])
except CostCapError as e:
    print(f"  [비용 cap] {e}")

# ── [5-6] apply_fallback + HWPX 빌드 ────────────────────────────────────
print("\n[5-6/7] apply_fallback + HWPX 빌드")
try:
    buf = io.StringIO()
    t0  = time.time()
    with redirect_stdout(buf):
        md_proc = apply_fallback(md_llm, pdf)
        r       = build_from_markdown(md_proc, out_hwpx, template)
    elapsed = time.time() - t0

    xml_sha = _xml_sha(out_hwpx)
    size_kb = out_hwpx.stat().st_size // 1024
    print(f"  완료: p={r['paragraphs']} eq={r['equations']}  {size_kb}KB  {elapsed:.1f}s")
    print(f"  xml_sha: {xml_sha}")
    pipeline_out = buf.getvalue()
    if pipeline_out.strip():
        print(f"  pipeline:\n{pipeline_out.strip()}")

except Exception:
    print(f"  오류:\n{traceback.format_exc()}")
    sys.exit(1)

# ── [7] 표/박스 삽입 ────────────────────────────────────────────────────
print("\n[7/7] 표/박스 삽입")
try:
    n_cond = replace_condition_tables(out_hwpx)
    n_bogi = replace_boilerplate_tables(out_hwpx)
    xml_sha_after = _xml_sha(out_hwpx)
    print(f"  조건 {n_cond}개 / 보기 {n_bogi}개  xml_sha: {xml_sha_after}")
except Exception:
    print(f"  [경고] 표 삽입 오류:\n{traceback.format_exc()}")

# ── gold 비교 ─────────────────────────────────────────────────────────────
if gold.exists():
    print(f"\n  ── gold 비교 ({gold.name[:60]}) ──")
    try:
        t_gold = _get_hp_t_texts(gold)
        t_v1   = _get_hp_t_texts(out_hwpx)
        ht_diffs = [(i, a, b) for i, (a, b) in enumerate(zip(t_gold, t_v1)) if a != b]
        count_diff = abs(len(t_gold) - len(t_v1))
        print(f"  hp:t 개수: gold={len(t_gold)} / {VER}={len(t_v1)}"
              + (f"  ⚠ {count_diff}개 차이" if count_diff else "  ✅ 동일"))
        print(f"  hp:t 내용 차이: {len(ht_diffs)}건")
        for i, a, b in ht_diffs[:15]:
            print(f"    #{i}: {repr(a[:50])} → {repr(b[:50])}")
        if len(ht_diffs) > 15:
            print(f"    ... (총 {len(ht_diffs)}건)")

        diffs = compare_scripts(gold, out_hwpx, start_idx=0)
        print(f"\n  script 차이: {len(diffs)}건")
        for d in diffs[:8]:
            idx = d["idx"]
            if idx == -1:
                print(f"    총계: gold={d['a']} / {VER}={d['b']}")
            else:
                print(f"    #{idx}: {d['a'][:60]} → {d['b'][:60]}")

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        report = LOG_DIR / f"고려고_수하_{VER}_diff.md"
        with open(report, "w", encoding="utf-8") as f:
            f.write(f"# 고려고 2024 수하 {VER} 비교\n\n")
            f.write(f"생성: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(f"## hp:t\ngold={len(t_gold)} / {VER}={len(t_v1)} / 차이={len(ht_diffs)}건\n\n")
            if ht_diffs:
                f.write("### 내용 차이\n")
                for i, a, b in ht_diffs:
                    f.write(f"- `#{i}`: `{a[:80]}` → `{b[:80]}`\n")
            f.write(f"\n## script 차이: {len(diffs)}건\n")
            if diffs:
                for d in diffs:
                    idx = d["idx"]
                    if idx == -1:
                        f.write(f"- 총계: gold={d['a']} / {VER}={d['b']}\n")
                    else:
                        f.write(f"- `#{idx}`: `{d['a'][:80]}` → `{d['b'][:80]}`\n")
        print(f"\n  보고서: {report}")
    except Exception:
        print(f"  gold 비교 오류:\n{traceback.format_exc()}")
else:
    print(f"\n  gold 없음: {gold.name}")

# ── 비용 요약 ──────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print("비용 요약")
for svc, cost in guard.summary().items():
    print(f"  {svc}: ${cost:.4f}")
print(f"  오늘 합계: ${guard.total_today():.4f} / $5.00")
print("=" * 60)
