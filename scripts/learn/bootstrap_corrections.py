"""
production HWPX vs gold HWPX 비교 → 교정 사전 (재)생성.

사용법:
  py scripts/learn/bootstrap_corrections.py                # 11b만
  py scripts/learn/bootstrap_corrections.py --include-2024 # 11b + 2024 통합

특징:
  - 기존 corrections.json의 approved=true / blacklisted=true 엔트리는
    스냅샷 후 재생성 시 상태 복원 (frequency/schools/context_examples는 갱신).

출력:
  src/learn/corrections.json
  reports/corrections_bootstrap_YYYYMMDD.md
"""
import re
import sys
import json
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from src.learn.correction_capture import diff_hwpx, update_dictionary, _SCHOOL_NAMES

ROOT       = Path(__file__).resolve().parent.parent.parent
DICT_PATH  = ROOT / "src" / "learn" / "corrections.json"
REPORT_DIR = ROOT / "reports"


def _school_name(prod_path: Path) -> str:
    """production HWPX 파일명에서 학교명 추출.

    1) _vN suffix 제거
    2) 명시 학교 리스트(_SCHOOL_NAMES, 40+) 매칭 — 2024 파일명도 안전하게 처리
    3) 매칭 실패 시 마지막 토큰 fallback (11b 호환)
    """
    stem = prod_path.stem
    if stem.endswith(tuple(f"_v{n}" for n in range(1, 100))):
        stem = stem.rsplit("_v", 1)[0]

    # 명시 학교명 매칭 (가장 긴 매치 우선 — "광주여고" > "광주고")
    matches = [s for s in _SCHOOL_NAMES if s in stem]
    if matches:
        return max(matches, key=len)

    # fallback: 마지막 토큰
    parts = stem.split("_")
    return parts[-1] if parts else stem


def _gold_for_prod(prod: Path, gold_dir: Path) -> Path | None:
    """production HWPX → 매칭되는 gold HWPX 찾기.

    1) `[<prod_stem 마이너스 _vN>].hwpx` (11b 패턴)
    2) gold_dir의 .hwpx 중 학교명을 포함하는 것 (2024 패턴)
    """
    parts = prod.stem.split("_")
    if parts and parts[-1].startswith("v") and parts[-1][1:].isdigit():
        base_stem = "_".join(parts[:-1])
    else:
        base_stem = prod.stem

    candidate = gold_dir / f"[{base_stem}].hwpx"
    if candidate.exists():
        return candidate

    school = _school_name(prod)
    # 학교명을 포함하는 gold HWPX (가장 짧은 이름 = 가장 정확한 매치)
    candidates = [g for g in gold_dir.glob("*.hwpx") if school in g.stem]
    if candidates:
        return sorted(candidates, key=lambda p: len(p.stem))[0]
    return None


def _find_pairs(prod_dir: Path, gold_dir: Path) -> list[tuple[Path, Path, str]]:
    pairs = []
    for prod in sorted(prod_dir.glob("*.hwpx")):
        school = _school_name(prod)
        gold = _gold_for_prod(prod, gold_dir)
        if gold:
            pairs.append((prod, gold, school))
        else:
            print(f"  [경고] gold 없음: {prod.name}")
    return pairs


def _snapshot_status(dict_path: Path) -> dict[tuple[str, str], dict]:
    """기존 corrections.json에서 approved/blacklisted 상태만 스냅샷.

    Key: (old, type) → {'approved': bool, 'blacklisted': bool}
    """
    if not dict_path.exists():
        return {}
    data = json.loads(dict_path.read_text(encoding="utf-8"))
    snap: dict[tuple[str, str], dict] = {}
    for e in data.get("entries", []):
        if e.get("approved") or e.get("blacklisted"):
            snap[(e["old"], e["type"])] = {
                "approved":    bool(e.get("approved")),
                "blacklisted": bool(e.get("blacklisted")),
            }
    return snap


def _restore_status(dict_path: Path, snapshot: dict[tuple[str, str], dict]) -> tuple[int, int, list[tuple[str, str]]]:
    """스냅샷의 approved/blacklisted 상태를 새 사전에 복원.

    반환: (복원된_approved_수, 복원된_blacklisted_수, 매칭_실패_목록)
    """
    if not snapshot:
        return 0, 0, []
    data = json.loads(dict_path.read_text(encoding="utf-8"))
    idx  = {(e["old"], e["type"]): e for e in data.get("entries", [])}

    restored_a = 0
    restored_b = 0
    missing: list[tuple[str, str]] = []

    for key, status in snapshot.items():
        if key in idx:
            entry = idx[key]
            if status["approved"]:
                entry["approved"] = True
                restored_a += 1
            if status["blacklisted"]:
                entry["blacklisted"] = True
                restored_b += 1
        else:
            missing.append(key)

    dict_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return restored_a, restored_b, missing


def main() -> None:
    args = sys.argv[1:]
    include_2024 = "--include-2024" in args

    # (prod_dir, gold_dir, form_label) 페어 리스트
    # form_label: "typer" (타이퍼 양식, 11b) / "msbiseo" (수학비서 양식, 2024)
    dir_pairs: list[tuple[Path, Path, str]] = [
        (ROOT / "samples" / "11b_production", ROOT / "samples" / "11b", "typer"),
    ]
    if include_2024:
        dir_pairs.append(
            (ROOT / "samples" / "2024_production", ROOT / "samples" / "2024", "msbiseo")
        )

    # 1) approved/blacklisted 스냅샷
    status_snapshot = _snapshot_status(DICT_PATH)
    print(f"기존 상태 스냅샷: approved {sum(1 for s in status_snapshot.values() if s['approved'])} / "
          f"blacklisted {sum(1 for s in status_snapshot.values() if s['blacklisted'])}")

    # 2) 사전 초기화
    if DICT_PATH.exists():
        print(f"기존 사전 삭제 후 재생성: {DICT_PATH}")
        DICT_PATH.unlink()

    # 3) 각 (prod_dir, gold_dir, form_label) 페어 순회
    pairs: list[tuple[Path, Path, str, str]] = []
    for prod_dir, gold_dir, form_label in dir_pairs:
        if not prod_dir.is_dir():
            print(f"  [경고] production 없음, skip: {prod_dir.name}")
            continue
        if not gold_dir.is_dir():
            print(f"  [경고] gold 없음, skip: {gold_dir.name}")
            continue
        sub = _find_pairs(prod_dir, gold_dir)
        pairs.extend((p, g, s, form_label) for p, g, s in sub)
        print(f"  {prod_dir.name} [{form_label}]: {len(sub)}쌍")

    print(f"비교 쌍 총: {len(pairs)}개")
    print("─" * 60)

    school_counts: dict[str, int] = {}
    errors: list[str] = []

    for prod, gold, school, form in pairs:
        print(f"  [{school}/{form}] ", end="", flush=True)
        try:
            corrs = diff_hwpx(prod, gold)
            update_dictionary(corrs, DICT_PATH, school=school, form=form)
            school_counts[school] = school_counts.get(school, 0) + len(corrs)
            print(f"후보 {len(corrs)}개")
        except Exception as e:
            print(f"오류: {e}")
            errors.append(f"{school}: {e}")

    # 4) approved/blacklisted 상태 복원
    restored_a, restored_b, missing = _restore_status(DICT_PATH, status_snapshot)
    print(f"\n상태 복원: approved {restored_a}건, blacklisted {restored_b}건")
    if missing:
        print(f"  [주의] 매칭 실패 {len(missing)}건 — 패턴이 사라짐 (수동 검토 필요):")
        for k in missing[:5]:
            print(f"    - ({k[1]}) {k[0]!r}")

    # ── 보고서 생성 ───────────────────────────────────────────────
    data    = json.loads(DICT_PATH.read_text(encoding="utf-8"))
    entries = data["entries"]

    text_n     = sum(1 for e in entries if e["type"] == "text")
    eq_n       = sum(1 for e in entries if e["type"] == "equation")
    multi_n    = sum(1 for e in entries if e["frequency"] >= 2)
    high_n     = sum(1 for e in entries if e["frequency"] >= 5)
    approved_n    = sum(1 for e in entries if e.get("approved"))
    blacklisted_n = sum(1 for e in entries if e.get("blacklisted"))

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
        f"- 승인됨 (approved): {approved_n}",
        f"- 블랙리스트: {blacklisted_n}",
        f"- 상태 복원: approved {restored_a}건, blacklisted {restored_b}건"
        + (f" (매칭 실패 {len(missing)}건)" if missing else ""),
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
