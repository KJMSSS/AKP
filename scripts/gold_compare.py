"""골드셋 품질 비교 하네스 (개발용 회귀 안전장치).

samples/11b 의 (시험지 PDF + 사람이 완성한 정답 HWPX) 18쌍에 대해:
  현재 변환 파이프라인으로 PDF → HWPX 변환 → 정답 HWPX와 텍스트 비교
  → 학교별 유사도 + 수식/마커 수 + 이전 기준선 대비 회귀 표시.

목적은 '점수 자랑'이 아니라 **변환기를 고칠 때 어디가 나빠졌는지** 잡는 것.

비용:
  - OCR(Claude)은 PDF당 ~$0.3. 첫 실행/`--reocr` 시에만 호출하고
    결과 마크다운을 scripts/web/tmp/goldcache/ 에 캐시한다.
  - 빌드측(파싱·표·2단 등)만 바꿨다면 캐시 재사용으로 **무과금 재실행**.

사용:
  py scripts/gold_compare.py                 # 캐시 사용(빌드측 비교, 무과금)
  py scripts/gold_compare.py --reocr         # OCR 다시(프롬프트 바꿨을 때, 과금)
  py scripts/gold_compare.py --schools 경신여고,고려고   # 일부만
  py scripts/gold_compare.py --save-baseline # 현재 점수를 기준선으로 저장
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import zipfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

_GOLD_DIR  = _ROOT / "samples" / "11b"
_CACHE_DIR = _ROOT / "scripts" / "web" / "tmp" / "goldcache"
_BASELINE  = _ROOT / "scripts" / "web" / "tmp" / "gold_baseline.json"
_TMP       = _ROOT / "scripts" / "web" / "tmp"


# ── 텍스트 추출·정규화 ────────────────────────────────────────────────────

def extract_text(hwpx: Path) -> str:
    """HWPX에서 비교용 텍스트(본문 hp:t + 수식 hp:script)를 등장 순서대로."""
    parts: list[str] = []
    with zipfile.ZipFile(hwpx) as z:
        secs = sorted(n for n in z.namelist() if re.match(r"Contents/section\d+\.xml", n))
        for s in secs:
            xml = z.read(s).decode("utf-8")
            for m in re.finditer(r"<hp:t[^>]*>([^<]*)</hp:t>|<hp:script>([\s\S]*?)</hp:script>", xml):
                if m.group(1) is not None:
                    parts.append(m.group(1))
                else:
                    parts.append("$" + (m.group(2) or "") + "$")
    return "".join(parts)


# 골드 타이퍼 머리말 보일러플레이트 (문제 내용 아님 — 비교에서 제외)
_BOILER = [
    "이 자료의 2차 저작권은 광주 전남 타이퍼에 있습니다.",
    "공통수학1", "여러 가지 방정식 ~ 행렬",
]
_BASE64ISH = re.compile(r"[A-Za-z0-9+/=]{30,}")  # 광덕고 등 임베드 잡음


def normalize(text: str) -> str:
    for b in _BOILER:
        text = text.replace(b, "")
    text = _BASE64ISH.sub("", text)        # base64 잡음 제거
    text = re.sub(r"\s+", "", text)        # 공백 전부 제거
    return text


def doc_metrics(hwpx: Path) -> dict:
    raw = extract_text(hwpx)
    norm = normalize(raw)
    with zipfile.ZipFile(hwpx) as z:
        xml = "".join(
            z.read(n).decode("utf-8")
            for n in z.namelist() if re.match(r"Contents/section\d+\.xml", n)
        )
    return {
        "chars": len(norm),
        "equations": xml.count("<hp:equation"),
        "markers": len(re.findall(r"【★", xml)),
        "_norm": norm,
    }


def similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


# ── 우리 파이프라인으로 PDF → HWPX ─────────────────────────────────────────

def convert_pdf(pdf: Path, reg_key: str, reocr: bool) -> Path | None:
    """현재 파이프라인으로 변환. OCR 마크다운은 캐시. 실패 시 None."""
    from src.ocr.claude_pdf_reader import read_pdf_as_markdown
    from src.ocr.latex_corrector import correct_latex
    from src.text_only.ocr_fallback import apply_fallback
    from src.text_only.problem_segmenter import parse_problems, rebuild_markdown
    from src.text_only.text_builder import build_from_markdown
    from src.common.hwpx_table_inserter import replace_condition_tables, replace_boilerplate_tables
    from src.common.hwpx_namespace_fixer import fix_hwpx_namespaces
    from src.common.pdf_utils import normalize_pdf_rotation

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    md_cache = _CACHE_DIR / f"{reg_key}.md"

    subject = reg_key.split("_")[4] if len(reg_key.split("_")) >= 5 else ""

    if md_cache.exists() and not reocr:
        md = md_cache.read_text(encoding="utf-8")
    else:
        try:
            src_pdf = normalize_pdf_rotation(pdf)
        except Exception:
            src_pdf = pdf
        print(f"    [OCR] {reg_key} (과금)...")
        md = read_pdf_as_markdown(src_pdf, full_content=False, subject=subject)
        md = correct_latex(md, subject=subject)
        md = apply_fallback(md, src_pdf)
        md_cache.write_text(md, encoding="utf-8")

    header, segments = parse_problems(md)
    md2 = rebuild_markdown(header, segments)

    template = next(
        (f for f in (_ROOT / "samples").glob("*.hwpx")
         if "워드초벌" in f.name and "]1." not in f.name), None)
    if template is None:
        print("    템플릿 없음"); return None

    out = _CACHE_DIR / f"{reg_key}.our.hwpx"
    build_from_markdown(md2, out, template)
    replace_condition_tables(out)
    replace_boilerplate_tables(out)
    fix_hwpx_namespaces(str(out))
    # 2단 타이퍼 양식으로 (골드와 같은 형식). 실패해도 1단으로 비교.
    try:
        from src.text_only.typer_builder import build_typer_hwpx
        two = out.with_suffix(".2dan.hwpx")
        build_typer_hwpx(out, reg_key, two)
        two.replace(out)
    except Exception as e:
        print(f"    [2단] 스킵({e}) — 1단으로 비교")
    return out


# ── 메인 ──────────────────────────────────────────────────────────────────

def _reg_key(gold_hwpx: Path) -> str:
    # [2025_1_1_b_공수1_경신여고].hwpx → 2025_1_1_b_공수1_경신여고
    return gold_hwpx.stem.strip("[]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reocr", action="store_true", help="OCR 다시 (과금)")
    ap.add_argument("--schools", default="", help="쉼표구분 학교명 일부만")
    ap.add_argument("--save-baseline", action="store_true")
    args = ap.parse_args()

    pairs = []
    for pdf in sorted(_GOLD_DIR.glob("*.pdf")):
        gold = pdf.with_suffix(".hwpx")
        if gold.exists():
            pairs.append((pdf, gold))
    if args.schools:
        want = [s.strip() for s in args.schools.split(",") if s.strip()]
        pairs = [(p, g) for p, g in pairs if any(w in p.stem for w in want)]

    baseline = {}
    if _BASELINE.exists():
        try:
            baseline = json.loads(_BASELINE.read_text(encoding="utf-8"))
        except Exception:
            pass

    print(f"골드셋 비교 — {len(pairs)}개교 "
          f"({'OCR 재실행(과금)' if args.reocr else '캐시 사용(무과금)'})\n")
    rows = []
    for pdf, gold in pairs:
        key = _reg_key(gold)
        school = key.split("_")[-1]
        try:
            our = convert_pdf(pdf, key, args.reocr)
            if our is None:
                rows.append((school, None, "변환 실패")); continue
            gm = doc_metrics(gold)
            om = doc_metrics(our)
            sim = similarity(om["_norm"], gm["_norm"])
            rows.append((school, sim, {
                "eq_our": om["equations"], "eq_gold": gm["equations"],
                "markers": om["markers"], "chars_our": om["chars"], "chars_gold": gm["chars"],
            }))
        except Exception as e:
            rows.append((school, None, f"오류: {e}"))

    # ── 리포트 ──
    print(f"{'학교':<12} {'유사도':>7}  {'수식(우/정)':>12}  {'미해결':>6}  {'회귀':>10}")
    print("-" * 60)
    scores = {}
    sims = []
    for school, sim, info in rows:
        if sim is None:
            print(f"{school:<12} {'--':>7}  {info}")
            continue
        scores[school] = round(sim, 4)
        sims.append(sim)
        eq = f"{info['eq_our']}/{info['eq_gold']}"
        # 회귀: 기준선 대비 3%p 이상 하락
        reg = ""
        if school in baseline:
            d = sim - baseline[school]
            if d <= -0.03:
                reg = f"🔴 {baseline[school]*100:.0f}→{sim*100:.0f}"
            elif d >= 0.03:
                reg = f"🟢 +{d*100:.0f}%p"
            else:
                reg = "≈"
        print(f"{school:<12} {sim*100:>6.1f}%  {eq:>12}  {info['markers']:>6}  {reg:>10}")

    if sims:
        avg = sum(sims) / len(sims)
        print("-" * 60)
        print(f"{'평균':<12} {avg*100:>6.1f}%   (낮은 학교일수록 우리 변환이 정답과 다름 = 약점)")
        worst = sorted([(s, sc) for s, sc in scores.items()], key=lambda x: x[1])[:3]
        print(f"약한 학교 Top3: " + ", ".join(f"{s} {sc*100:.0f}%" for s, sc in worst))

    if args.save_baseline and scores:
        _BASELINE.write_text(json.dumps(scores, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n기준선 저장: {_BASELINE}")


if __name__ == "__main__":
    main()
