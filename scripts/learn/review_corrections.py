"""
교정 사전 인터랙티브 검토 도구.

사용법:
  py scripts/learn/review_corrections.py           # 전체 미승인 항목 검토
  py scripts/learn/review_corrections.py --min-freq 2  # 2회 이상만
  py scripts/learn/review_corrections.py --show       # 현황만 표시

조작:
  y     → approved: true (자동 교정 활성화)
  n     → blacklisted: true (영구 제외)
  skip  → 나중에 다시 검토
  q     → 저장 후 종료
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

ROOT      = Path(__file__).resolve().parent.parent.parent
DICT_PATH = ROOT / "src" / "learn" / "corrections.json"


def _print_stats(entries: list[dict]) -> None:
    total     = len(entries)
    approved  = sum(1 for e in entries if e.get("approved"))
    blacklist = sum(1 for e in entries if e.get("blacklisted"))
    pending   = total - approved - blacklist
    print(f"사전 현황: 전체 {total}  승인 {approved}  제외 {blacklist}  대기 {pending}")


def main() -> None:
    args = sys.argv[1:]

    if not DICT_PATH.exists():
        print("사전 없음. bootstrap_corrections.py 먼저 실행하세요.")
        sys.exit(1)

    data    = json.loads(DICT_PATH.read_text(encoding="utf-8"))
    entries = data["entries"]
    _print_stats(entries)

    if "--show" in args:
        return

    # --min-freq N 파싱
    min_freq = 1
    if "--min-freq" in args:
        try:
            min_freq = int(args[args.index("--min-freq") + 1])
        except (IndexError, ValueError):
            pass

    pending = [
        e for e in entries
        if not e.get("approved") and not e.get("blacklisted")
        and e["frequency"] >= min_freq
    ]

    if not pending:
        print("검토 대기 항목 없음.")
        return

    # 빈도 높은 순으로 정렬
    pending.sort(key=lambda e: -e["frequency"])
    print(f"검토 대기: {len(pending)}개  (빈도 ≥ {min_freq})\n")

    approved_n = rejected_n = skipped_n = 0

    for i, entry in enumerate(pending, 1):
        print(f"[{i}/{len(pending)}] {entry['type']}  빈도={entry['frequency']}")
        print(f"  이전: {entry['old']}")
        print(f"  이후: {entry['new']}")
        if entry.get("context_examples"):
            print(f"  맥락: {entry['context_examples'][0][:80]}")
        if entry.get("schools"):
            school_str = ", ".join(entry["schools"][:5])
            if len(entry["schools"]) > 5:
                school_str += f" 외 {len(entry['schools'])-5}교"
            print(f"  학교: {school_str}")

        try:
            ans = input("  자동 교정 OK? [y/n/skip/q] → ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if ans == "q":
            break
        elif ans == "y":
            entry["approved"] = True
            approved_n += 1
        elif ans == "n":
            entry["blacklisted"] = True
            rejected_n += 1
        else:
            skipped_n += 1
        print()

    DICT_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"저장 완료 — 승인: {approved_n}  거절: {rejected_n}  스킵: {skipped_n}")
    _print_stats(entries)


if __name__ == "__main__":
    main()
