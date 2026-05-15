"""
17개 production HWPX vs 18개 gold HWPX 비교 → 교정 사전 초기 생성.

사용법:
  py scripts/learn/bootstrap_corrections.py

출력:
  src/learn/corrections.json         — 교정 후보 사전
  reports/corrections_bootstrap_YYYYMMDD.md — 학원장 검토 보고서
"""
import sys
import json
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from src.learn.correction_capture import diff_hwpx, update_dictionary

ROOT      = Path(__file__).resolve().parent.parent.parent
PROD_DIR  = ROOT / "samples" / "11b_production"
GOLD_DIR  = ROOT / "samples" / "11b"
DICT_PATH = ROOT / "src" / "learn" / "corrections.json"
REPORT_DIR = ROOT / "reports"


def _school_name(prod_path: Path) -> str:
    """'2025_1_1_b_공수1_경신여고_v1' → '경신여고'"""
    parts = prod_path.stem.split("_")
    # 마지막 토큰이 vN 형태면 제거
    if parts and parts[-1].startswith("v") and parts[-1][1:].isdigit():
        parts = parts[:-1]
    return parts[-1] if parts else prod_path.stem


def _find_pairs() -> list[tuple[Path, Path, str]]:
    pairs = []
    for prod in sorted(PROD_DIR.glob("*.hwpx")):
        school = _school_name(prod)
        gold = GOLD_DIR / f"[2025_1_1_b_공수1_{school}].hwpx"
        if gold.exists():
            pairs.append((prod, gold, school))
        else:
            print(f"  [경고] gold 없음: {school}")
    return pairs


def main() -> None:
    # 기존 사전 초기화 (부트스트랩은 새로 생성)
    if DICT_PATH.exists():
        print(f"기존 사전 삭제 후 재생성: {DICT_PATH}")
        DICT_PATH.unlink()

    pairs = _find_pairs()
    print(f"비교 쌍: {len(pairs)}개")
    print("─" * 60)

    school_counts: dict[str, int] = {}
    errors: list[str] = []

    for prod, gold, school in pairs:
        print(f"  [{school}] ", end="", flush=True)
        try:
            corrs = diff_hwpx(prod, gold)
            update_dictionary(corrs, DICT_PATH, school=school)
            school_counts[school] = len(corrs)
            print(f"후보 {len(corrs)}개")
        except Exception as e:
            print(f"오류: {e}")
            errors.append(f"{school}: {e}")

    # ── 보고서 생성 ───────────────────────────────────────────────
    data    = json.loads(DICT_PATH.read_text(encoding="utf-8"))
    entries = data["entries"]

    text_n    = sum(1 for e in entries if e["type"] == "text")
    eq_n      = sum(1 for e in entries if e["type"] == "equation")
    multi_n   = sum(1 for e in entries if e["frequency"] >= 2)
    high_n    = sum(1 for e in entries if e["frequency"] >= 5)

    by_freq = sorted(entries, key=lambda e: (-e["frequency"], e["old"]))

    today_str  = date.today().strftime("%Y%m%d")
    report_path = REPORT_DIR / f"corrections_bootstrap_{today_str}.md"
    REPORT_DIR.mkdir(exist_ok=True)

    lines = [
        f"# 교정 사전 부트스트랩 결과 ({date.today()})",
        "",
        "## 요약",
        f"- 처리 쌍: {len(pairs)}개",
        f"- 교정 후보 총수: {len(entries)}",
        f"  - 텍스트 교정: {text_n}",
        f"  - 수식 교정: {eq_n}",
        f"- 2회 이상 반복: {multi_n}",
        f"- 5회 이상 반복: {high_n}",
        "",
        "## 학교별 발견 후보 수",
        "",
    ]
    for school, cnt in sorted(school_counts.items(), key=lambda x: -x[1]):
        lines.append(f"- {school}: {cnt}개")

    lines += [
        "",
        "## 상위 30개 교정 후보 (학원장 검토용)",
        "",
        "| # | 타입 | 이전 | 이후 | 빈도 | 학교 |",
        "|---|------|------|------|------|------|",
    ]
    for i, e in enumerate(by_freq[:30], 1):
        schools_str = ", ".join(e["schools"][:3])
        if len(e["schools"]) > 3:
            schools_str += f" 외 {len(e['schools'])-3}교"
        old_e = e["old"].replace("|", "\\|")
        new_e = e["new"].replace("|", "\\|")
        lines.append(
            f"| {i} | {e['type']} | `{old_e}` | `{new_e}` "
            f"| {e['frequency']} | {schools_str} |"
        )

    lines += [
        "",
        "## 빈도 1회 후보 샘플 (노이즈 가능성 높음)",
        "",
    ]
    once_sample = [e for e in entries if e["frequency"] == 1][:15]
    for e in once_sample:
        ctx = e["context_examples"][0] if e["context_examples"] else ""
        lines.append(
            f"- [{e['type']}] `{e['old']}` → `{e['new']}`"
            + (f"  *(맥락: {ctx})*" if ctx else "")
        )

    if errors:
        lines += ["", "## 오류", ""]
        for err in errors:
            lines.append(f"- {err}")

    report_path.write_text("\n".join(lines), encoding="utf-8")

    print()
    print("─" * 60)
    print(f"  총 후보: {len(entries)}  (텍스트 {text_n} / 수식 {eq_n})")
    print(f"  2회 이상: {multi_n}  /  5회 이상: {high_n}")
    print(f"  사전: {DICT_PATH}")
    print(f"  보고서: {report_path}")


if __name__ == "__main__":
    main()
