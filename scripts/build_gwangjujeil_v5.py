"""
Cycle 16 — 광주제일고 v5 빌드 (문제 단위 파이프라인).

신규 파이프라인:
  raw.md
  → [1] parse_problems()     문제 분리 + 정렬 (2컬럼 뒤섞임 해결) + 스크래치 제거
  → [2] normalize_choices()  LLM 선택지 （1）~（5） 정규화
  → [3] rebuild_markdown()   정제 MD 재조립 (조건시작/끝 마커 포함)
  → [4] postprocess_markdown() LLM 한글 OCR 교정
  → [5] apply_fallback()     Mathpix 이미지 플레이스홀더
  → [6] build_from_markdown() HWPX 생성
  → [7] replace_condition_tables() 조건 박스 삽입 (자동)
  → [8] replace_boilerplate_tables() 보기 박스 삽입 (자동)
  .hwpx
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
    replace_placeholder_with_data_table,
)

ROOT     = Path(__file__).resolve().parent.parent
SRC_DIR  = ROOT / "samples" / "11b"
PROD_DIR = ROOT / "samples" / "11b_production"
LOG_DIR  = ROOT / "log" / "cycle_16"

SOURCE  = "2025_1_1_b_공수1_광주제일고"
VER     = "v5"

safe    = re.sub(r"[^\w\-]+", "_", SOURCE.strip("[]")).strip("_")
cache   = SRC_DIR / f"_{safe}_raw.md"
if not cache.exists():
    cache = SRC_DIR / f"_{SOURCE}_raw.md"
template = SRC_DIR / f"[{SOURCE}].hwpx"
gold     = template
out_hwpx = PROD_DIR / f"{SOURCE}_{VER}.hwpx"
pdf      = SRC_DIR / f"[{SOURCE}].pdf"

# ── 데이터 표 (문제별 수동 지정) ─────────────────────────────────────────
# 표가 있는 문제는 학원장 확인 후 추가
# DATA_TABLES: list[TableSpec] = [
#     TableSpec(
#         item="19",
#         headers=["헤더1", "헤더2"],
#         rows=[["a", "b"], ["c", "d"]],
#         col_widths=[24000, 24000],
#     ),
# ]
DATA_TABLES: list[TableSpec] = []  # v5: 표 없음 (학원장 확인 후 추가)


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
print(f"[광주제일고] → {VER}  (Cycle 16: 문제 단위 파이프라인)")

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

# ── 광주제일고 OCR 수정 패치 ────────────────────────────────────────────

# 2번: (b) 1 → (1) 1
md_raw = re.sub(r'^[（(]\s*[bB]\s*[）)]', '（1）', md_raw, flags=re.MULTILINE)

# 서술형1: [^0] 각주 참조 제거
md_raw = md_raw.replace('[^0]서술형 1.', '서술형 1.')
# 서술형4: 배점 누락 추가 + 숫자 수식처리
md_raw = md_raw.replace(
    '서술형 4. 0 이상 9 이하의 서로 다른 네 정수',
    '서술형 4. $0$ 이상 $9$ 이하의 서로 다른 네 정수',
)
md_raw = md_raw.replace(
    '를 만들 때, $b>c>d$ 를 만족시키는 짝수의 개수를 구하시오.',
    '를 만들 때, $b>c>d$ 를 만족시키는 짝수의 개수를 구하시오. [6.5점]',
)
# 저작권·확인사항 제거
md_raw = re.sub(
    r'※\s*이 시험 문제의 저작권.*?처벌될 수 있습니다\.?',
    '',
    md_raw, flags=re.DOTALL,
)
md_raw = re.sub(r'\[\^0\]:\s+＊확인 사항.*', '', md_raw, flags=re.DOTALL)

# 3번: 선택지 ①② 합쳐짐 ($9 \quad 12$) → 분리
md_raw = md_raw.replace(r'（1） $9 \quad 12$', '（1） 9\n（2） 12')

# 8번: 선택지 ② 누락 추가 + 스크래치 제거
md_raw = md_raw.replace(
    '（1） 1\n（3） 3\n（4） 4\n（5） 5\n入边二刘 －',
    '（1） 1\n（2） 2\n（3） 3\n（4） 4\n（5） 5',
)

# 6번: 선택지 LaTeX array → 개별 줄
md_raw = md_raw.replace(
    '（1） $6 \\quad 8$\n$\\begin{array}{lllll}\\text {（1）} 6 & 8 / 7 & \\text {（3）} 8 & \\text {（4）} 9 & \\text {（5）} 10 \\\\ & 71 & \\end{array}$',
    '（1） 6\n（2） 7\n（3） 8\n（4） 9\n（5） 10',
)

# 12번: 연립부등식 OCR 수정
md_raw = md_raw.replace(
    r'\left\{\begin{array}{l}' + '\n'
    r'4 x-3<5 \text { atn } \geq \geq 2 \text { an } \\' + '\n'
    r'x-7 \geq-2 x^{3} a' + '\n'
    r'\end{array}\right. \text { 32 atn }',
    r'\left\{\begin{array}{l}' + '\n'
    r'4x-3<5 \\' + '\n'
    r'x-7 \geq -2x+a' + '\n'
    r'\end{array}\right.',
)
# 12번: 본문 숫자 수식처리 (개수가 6)
md_raw = md_raw.replace('개수가 6 이 되도록', '개수가 $6$ 이 되도록')
# 12번: 선택지 ⑤ 스크래치 제거
md_raw = md_raw.replace('(5) 60 $-4-3-2-101$', '(5) 60')

# 13번: OCR 'aco' → '$a>0$인'
md_raw = md_raw.replace('에 대하여 aco 이차부등식', '에 대하여 $a>0$인 이차부등식')
# 13번: 선택지 ③④⑤ 이미지 앞으로 이동 (이미지가 파싱 중단시킴)
md_raw = md_raw.replace(
    '(2) 2\n![](https://cdn.mathpix.com/cropped/3253d2d6',
    '(2) 2\n(3) 3\n(4) 4\n(5) 5\n![](https://cdn.mathpix.com/cropped/3253d2d6',
)
md_raw = md_raw.replace('(4) $4 \\quad 4 a+2 b+c=0$\n(5) 5', '')  # 이미지 뒤 중복 제거

# 16번: 본문 숫자 수식처리
md_raw = md_raw.replace('모두 6 명이', '모두 $6$ 명이')
md_raw = md_raw.replace('수가 576 일 때', '수가 $576$ 일 때')

# 17번: 학생 손글씨 스크래치 이미지 제거 (apply_fallback이 【★ 본문 손상】으로 치환하는 것 방지)
md_raw = md_raw.replace(
    '![](https://cdn.mathpix.com/cropped/3253d2d6-3984-4e9e-9856-4bfe6fb8168c-6.jpg?height=234&width=538&top_left_y=632&top_left_x=554)\n',
    '',
)
# 17번: [4.1점] 뒤 스크래치 '-' 제거
md_raw = md_raw.replace('[4.1점] -', '[4.1점]')

# 18번: 본문 숫자 수식처리
md_raw = md_raw.replace(
    '18. 10 이하의 자연수 중에서 서로 다른 4 개의 수를 뽑을 때, 뽑은 4 개의 수의 합이 3 의 배수가 되는 경우의 수는? [4.2점]',
    '18. $10$ 이하의 자연수 중에서 서로 다른 $4$ 개의 수를 뽑을 때, 뽑은 $4$ 개의 수의 합이 $3$ 의 배수가 되는 경우의 수는? [4.2점]',
)

# 19번: 본문 숫자 수식처리 + 선택지 OCR 순서 오류 수정
md_raw = md_raw.replace('79 번째', '$79$ 번째')
md_raw = md_raw.replace(
    '(3) 41023\n(1) 40312\n(1) 40312\n(2) 40321\n(4) 41032\n(5) 41203',
    '(1) 40312\n(2) 40321\n(3) 41023\n(4) 41032\n(5) 41203',
)

# 21번: 본문 숫자 수식처리 + 선택지 누락 추가
md_raw = md_raw.replace(
    '21． 1 학년 학생 3 명과 2 학년 학생 3 명이 모두 일렬로 배치된 7 개의 의자에 앉을 때，다음 조건을 만족시키는 경우의 수는？',
    '21． $1$ 학년 학생 $3$ 명과 $2$ 학년 학생 $3$ 명이 모두 일렬로 배치된 $7$ 개의 의자에 앉을 때，다음 조건을 만족시키는 경우의 수는？',
)
md_raw = md_raw.replace(
    '（나） 2 학년 학생끼리는 이웃하지 않는다．\n\n22．',
    '（나） 2 학년 학생끼리는 이웃하지 않는다．\n（1） 636\n（2） 648\n（3） 660\n（4） 672\n（5） 684\n\n22．',
)
# 21번: 조건 표 안 숫자 수식처리 (선택지 삽입 후 실행)
md_raw = md_raw.replace('（가）빈 의자 옆에는 2 학년 학생이 앉는다．', '（가）빈 의자 옆에는 $2$ 학년 학생이 앉는다．')
md_raw = md_raw.replace('（나） 2 학년 학생끼리는 이웃하지 않는다．', '（나） $2$ 학년 학생끼리는 이웃하지 않는다．')

# 22번: 방정식 t 수정 (학교 수정사항) + 질문 t² + 조건(나) 분수 제거
md_raw = md_raw.replace(
    r'$x^{3}-5 x^{2}+x-6=0$',
    r'$x^{3}-5 x^{2}+tx-6=0$',
)
md_raw = md_raw.replace('里의 값은？', '$t^{2}$의 값은？')
md_raw = md_raw.replace(
    r'（나）$\frac{(\alpha-3 \beta+3 \gamma)^{2}}{\sqrt{3} / 2}=-3$',
    r'（나）$(\alpha-3\beta+3\gamma)^{2}=-3$',
)

# 서술형2: 본문 숫자 수식처리
md_raw = md_raw.replace('실근의 개수가 2 가 되도록', '실근의 개수가 $2$ 가 되도록')

# ── [1] 문제 분리 + 정렬 + 스크래치 제거 ────────────────────────────────
print("\n[1/7] 문제 파싱 (parse_problems)")
header, segments = parse_problems(md_raw)
obj_cnt  = sum(1 for s in segments if not s.is_subjective)
subj_cnt = sum(1 for s in segments if s.is_subjective)
cond_cnt = sum(1 for s in segments if s.conditions)
bogi_cnt = sum(1 for s in segments if s.boilerplate)
print(f"  발견: 객관식 {obj_cnt}개 / 서술형 {subj_cnt}개")
print(f"  조건 {cond_cnt}개 / 보기 {bogi_cnt}개 문제")
nums = sorted(s.number for s in segments)
print(f"  번호: {nums}")

# ── [2] 선택지 정규화 (LLM) ─────────────────────────────────────────────
print("\n[2/7] 선택지 정규화 (normalize_choices)")
try:
    guard.check_or_raise("choices")
    segments = normalize_choices(segments, log_stem=f"광주제일고_{VER}")
    choices_ok = sum(1 for s in segments if not s.is_subjective and len(s.choices) == 5)
    print(f"  선택지 5개 완비: {choices_ok}/{obj_cnt}개 문제")
    guard.record("choices", 0.01)  # 실제 비용은 normalize_choices 내부서 출력됨
except CostCapError as e:
    print(f"  [비용 cap] {e}")

# ── [3] rebuild_markdown ─────────────────────────────────────────────────
print("\n[3/7] rebuild_markdown")
data_table_set = {t.item for t in DATA_TABLES}
md_rebuilt = rebuild_markdown("", segments, data_table_items=data_table_set)  # 헤더(표지/유의사항) 제외
# 재조립 결과 일부 출력
line_cnt = md_rebuilt.count("\n")
marker_cnt = md_rebuilt.count("【★")
print(f"  줄 수: {line_cnt} / 마커: {marker_cnt}개")

# ── [4] LLM 후처리 ────────────────────────────────────────────────────────
print("\n[4/7] LLM 후처리 (postprocess_markdown)")
md_llm = md_rebuilt
llm_meta: dict = {}
try:
    guard.check_or_raise("llm")
    md_llm, llm_meta = postprocess_markdown(md_rebuilt, log_stem=f"광주제일고_{VER}")
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

# ── [5] OCR fallback ─────────────────────────────────────────────────────
# ── [6] HWPX 빌드 ────────────────────────────────────────────────────────
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
    pipeline_out = buf.getvalue()

    print(f"  완료: p={r['paragraphs']} eq={r['equations']}  {size_kb}KB  {elapsed:.1f}s")
    print(f"  xml_sha: {xml_sha}")
    if pipeline_out.strip():
        print(f"  pipeline:\n{pipeline_out.strip()}")

except Exception:
    print(f"  오류:\n{traceback.format_exc()}")
    sys.exit(1)

# ── [7] 표 삽입 ──────────────────────────────────────────────────────────
print("\n[7/7] 표/박스 삽입")
try:
    n_cond = replace_condition_tables(out_hwpx)
    n_bogi = replace_boilerplate_tables(out_hwpx)

    for spec in DATA_TABLES:
        replace_placeholder_with_data_table(out_hwpx, spec)

    xml_sha_after = _xml_sha(out_hwpx)
    print(f"  xml_sha (표 삽입 후): {xml_sha_after}")

except Exception:
    print(f"  [경고] 표 삽입 오류:\n{traceback.format_exc()}")

# ── gold 비교 ──────────────────────────────────────────────────────────────
print(f"\n  ── gold 비교 ({gold.name}) ──")
try:
    xml_sha_final = _xml_sha(out_hwpx)

    t_gold = _get_hp_t_texts(gold)
    t_v5   = _get_hp_t_texts(out_hwpx)
    ht_diffs = [(i, a, b) for i, (a, b) in enumerate(zip(t_gold, t_v5)) if a != b]
    count_mismatch = abs(len(t_gold) - len(t_v5))

    print(f"  hp:t 개수: gold={len(t_gold)} / {VER}={len(t_v5)}"
          + (f"  ⚠️ {count_mismatch}개 차이" if count_mismatch else "  ✅ 동일"))
    print(f"  hp:t 내용 차이: {len(ht_diffs)}건")
    for i, a, b in ht_diffs[:20]:
        print(f"    #{i}: {repr(a[:50])} → {repr(b[:50])}")
    if len(ht_diffs) > 20:
        print(f"    ... (총 {len(ht_diffs)}건)")

    diffs = compare_scripts(gold, out_hwpx, start_idx=0)
    print(f"\n  script 차이: {len(diffs)}건")
    for d in diffs[:10]:
        idx = d["idx"]
        if idx == -1:
            print(f"    총계: gold={d['a']} / {VER}={d['b']}")
        else:
            print(f"    #{idx}: {d['a'][:60]} → {d['b'][:60]}")

    # 보고서 저장
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    report_path = LOG_DIR / f"광주제일고_{VER}_baseline_diff.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# 광주제일고 {VER} baseline 비교\n\n")
        f.write(f"생성: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"## SHA\n- gold: `{_xml_sha(gold)}`\n- {VER}: `{xml_sha_final}`\n\n")
        f.write(f"## hp:t\n- gold: {len(t_gold)}개 / {VER}: {len(t_v5)}개 / 차이: {len(ht_diffs)}건\n\n")
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
                    f.write(f"- 총계: gold={d['a']} / {VER}={d['b']}\n")
                else:
                    f.write(f"- `#{idx}`: `{d['a'][:80]}` → `{d['b'][:80]}`\n")
        f.write(f"\n## LLM\n- 교정: {llm_meta.get('corrections', 'N/A')}건\n")
        f.write(f"- 거부: {llm_meta.get('rejected', 'N/A')}건\n")
        f.write(f"- 비용: ${llm_meta.get('cost_usd', 0):.4f}\n")
    print(f"\n  보고서: {report_path}")

except Exception:
    print(f"  gold 비교 오류:\n{traceback.format_exc()}")

# ── 비용 요약 ──────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print("비용 요약")
for svc, cost in guard.summary().items():
    print(f"  {svc}: ${cost:.4f}")
print(f"  오늘 합계: ${guard.total_today():.4f} / $5.00")
print("=" * 60)
