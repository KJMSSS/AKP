"""
직원 검수 수정 이력 — JSON Lines 형식.

logs/corrections.jsonl 에 한 줄씩 기록.

각 항목:
{
  "id": "uuid",
  "ts": "2026-06-03T14:32:11",
  "employee": "김선생",       // 토큰 이름
  "job_id": "abc123",
  "pdf_name": "경신여고.pdf",
  "problem_number": 3,
  "problem_text": "원본 OCR 텍스트",
  "correction_note": "분수가 7/27이 맞음",   // 직원 메모
  "corrected_text": "$\\frac{7}{27}$",       // 직원이 직접 수정한 텍스트 (선택)
  "status": "applied"         // applied | reverted
}
"""
from __future__ import annotations

import difflib
import json
import os
import re
import uuid
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

def _data_dir() -> Path:
    # 볼륨 마운트가 있으면 그게 유일한 영속 경로 (app.py와 동일 규칙) —
    # DATA_DIR 오타로 휘발성 디스크에 교정·패턴이 쌓여 재배포 때 사라지던 것 방지.
    vol = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    d = vol or os.environ.get("DATA_DIR", "")
    return Path(d) if d else Path(__file__).resolve().parent / "logs"

_LOG_DIR      = _data_dir()
_LOG_FILE     = _LOG_DIR / "corrections.jsonl"
_PATTERN_FILE = _LOG_DIR / "prompt_patterns.json"


# ── 패턴 관리 ──────────────────────────────────────────────────────────

def _load_patterns() -> list[dict]:
    if not _PATTERN_FILE.exists():
        return []
    try:
        return json.loads(_PATTERN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []

def _save_patterns(patterns: list[dict]) -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    _PATTERN_FILE.write_text(
        json.dumps(patterns, ensure_ascii=False, indent=2), encoding="utf-8"
    )

def approve_as_pattern(
    source_cid: str,
    scope: str,        # "global" | "school" | "subject"
    scope_value: str,  # "" | "경신여고" | "공수1"
    original_text: str,
    corrected_text: str,
    note: str = "",
) -> str:
    """수정 항목을 프롬프트 패턴으로 등록. 생성된 pid 반환."""
    patterns = _load_patterns()
    pid = uuid.uuid4().hex[:12]
    patterns.append({
        "id":            pid,
        "source_cid":    source_cid,
        "scope":         scope,
        "scope_value":   scope_value,
        "original_text": original_text,
        "corrected_text": corrected_text,
        "note":          note,
        "active":        True,
        "created_at":    datetime.now().isoformat(timespec="seconds"),
    })
    _save_patterns(patterns)
    return pid

# ── 큐레이션된 기본 패턴 (초기 품질용) ────────────────────────────────────
# corrected_text는 latex_to_hwp.convert가 지원하는 표기만 사용 (검증됨).
# \overrightarrow·\overarc 등 미지원 표기는 새 변환 실패를 유발하므로 금지.
DEFAULT_SEED_PATTERNS: list[dict] = [
    {
        "id": "seed-geom-symbols",
        "original_text": "∠ABC, △ABC, ∽, ≡ (각·삼각형·닮음·합동 기호를 글자로 읽거나 누락)",
        "corrected_text": r"$\angle \mathrm{ABC}$, $\triangle \mathrm{ABC}$, $\sim$, $\equiv$",
        "note": r"도형·관계 기호는 수식 모드로 — 닮음 ∽=\sim, 합동 ≡=\equiv",
    },
    {
        "id": "seed-segment-vector",
        "original_text": "선분 AB 윗줄·벡터 AB 화살표 누락 (그냥 AB로 읽음)",
        "corrected_text": r"$\overline{\mathrm{AB}}$, $\vec{\mathrm{AB}}$",
        "note": r"선분은 \overline, 벡터는 \vec — \overrightarrow는 변환 미지원이므로 쓰지 말 것",
    },
    {
        "id": "seed-parallel-perp",
        "original_text": "평행 ∥, 수직 ⊥ 기호를 //·T 등으로 오인",
        "corrected_text": r"$\parallel$, $\perp$",
        "note": r"평행 \parallel, 수직 \perp",
    },
]


def seed_default_patterns() -> int:
    """큐레이션된 기본 패턴을 global 스코프로 등록 (멱등 — id 중복 시 건너뜀).

    추가된 패턴 수 반환. 학원장이 /admin에서 한 번 눌러 초기 품질을 끌어올리는 용도.
    """
    patterns = _load_patterns()
    existing = {p.get("id") for p in patterns}
    now = datetime.now().isoformat(timespec="seconds")
    added = 0
    for sp in DEFAULT_SEED_PATTERNS:
        if sp["id"] in existing:
            continue
        patterns.append({
            "id":            sp["id"],
            "source_cid":    "seed",
            "scope":         "global",
            "scope_value":   "",
            "original_text": sp["original_text"],
            "corrected_text": sp["corrected_text"],
            "note":          sp["note"],
            "active":        True,
            "created_at":    now,
        })
        added += 1
    if added:
        _save_patterns(patterns)
    return added


def get_active_patterns(school: str = "", subject: str = "") -> list[dict]:
    """변환 시 프롬프트에 주입할 패턴 반환 (전역 + 학교별 + 과목별)."""
    all_p = _load_patterns()
    result = []
    for p in all_p:
        if not p.get("active"):
            continue
        s = p.get("scope", "global")
        v = p.get("scope_value", "")
        if s == "global":
            result.append(p)
        elif s == "school" and v == school:
            result.append(p)
        elif s == "subject" and v == subject:
            result.append(p)
    return result

def list_patterns() -> list[dict]:
    return list(reversed(_load_patterns()))

def toggle_pattern(pid: str, active: bool) -> bool:
    patterns = _load_patterns()
    for p in patterns:
        if p.get("id") == pid:
            p["active"] = active
            _save_patterns(patterns)
            return True
    return False

def delete_pattern(pid: str) -> bool:
    patterns = _load_patterns()
    new = [p for p in patterns if p.get("id") != pid]
    if len(new) == len(patterns):
        return False
    _save_patterns(new)
    return True


def _dup_key(e: dict) -> tuple:
    """중복 판정 키 — 같은 잡·문제·메모·교정텍스트면 동일 항목.
    같은 문제라도 메모/교정 내용이 다르면 다른 항목으로 본다."""
    return (
        e.get("job_id", ""),
        str(e.get("problem_number", "")),
        (e.get("correction_note") or "").strip(),
        (e.get("corrected_text") or "").strip(),
    )


def append_correction(entry: dict) -> str:
    """수정 항목 저장. 생성된 id 반환.

    재제출 등으로 '같은 문제 + 같은 메모 + 같은 교정'이 다시 들어오면
    중복으로 보고 새로 쓰지 않고 기존 id를 반환한다 (reverted 제외).
    """
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    # 중복 검사 (활성 항목만 — 되돌린 항목은 다시 등록 허용)
    key = _dup_key(entry)
    if _LOG_FILE.exists():
        for line in _LOG_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("status") != "reverted" and _dup_key(e) == key:
                return e.get("id", "")

    cid = uuid.uuid4().hex[:12]
    entry = {
        "id":     cid,
        "ts":     datetime.now().isoformat(timespec="seconds"),
        **entry,
        "status": "applied",
    }
    with _LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return cid


def dedupe_corrections() -> int:
    """기존 로그의 중복 항목 정리 (같은 키의 활성 항목은 첫 것만 유지).
    reverted 항목과 키가 다른 항목은 보존. 제거한 줄 수 반환."""
    if not _LOG_FILE.exists():
        return 0
    lines = _LOG_FILE.read_text(encoding="utf-8").splitlines()
    seen: set = set()
    kept: list[str] = []
    removed = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        if e.get("status") == "reverted":
            kept.append(line)  # 되돌린 항목은 그대로 보존
            continue
        key = _dup_key(e)
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        kept.append(line)
    if removed:
        _LOG_FILE.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return removed


def read_corrections(days: int = 30) -> list[dict]:
    """최근 N일 수정 내역 (최신순)."""
    if not _LOG_FILE.exists():
        return []
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    entries: list[dict] = []
    for line in _LOG_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
            if e.get("ts", "") >= cutoff:
                entries.append(e)
        except json.JSONDecodeError:
            pass
    return list(reversed(entries))


def revert_correction(cid: str) -> bool:
    """특정 수정 항목을 reverted 상태로 변경."""
    if not _LOG_FILE.exists():
        return False
    lines = _LOG_FILE.read_text(encoding="utf-8").splitlines()
    found = False
    new_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
            if e.get("id") == cid:
                e["status"] = "reverted"
                found = True
            new_lines.append(json.dumps(e, ensure_ascii=False))
        except json.JSONDecodeError:
            new_lines.append(line)
    if found:
        _LOG_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return found


# 수학 시험지 OCR에서 자주 문제되는 테마 (메모에서 이 키워드를 찾아 빈도 집계)
_THEME_KEYWORDS = [
    "집합", "루트", "분수", "지수", "첨자", "적분", "미분", "극한", "수열",
    "확률", "통계", "벡터", "행렬", "로그", "삼각", "도형", "그림", "표",
    "기호", "바", "괄호", "등호", "부등호", "절댓값", "수식", "조건", "보기",
]

_WS_RE = re.compile(r"\s+")


def _norm_ws(s: str) -> str:
    return _WS_RE.sub(" ", (s or "")).strip()


def _diff_fragments(before: str, after: str) -> list[dict]:
    """검수 전(before)→수정 후(after)의 최소 변경 토막만 추출.

    char 단위로 비교해 바뀐 구간만 뽑는다 — 동일 부분·공백차·무변경은 버린다.
    한쪽이 비면(삭제/삽입) 그대로 둔다. 반환: [{"old": "...", "new": "..."}, ...]

    메모 없이 자동 기록된 '검수 전→후'를 내용 단위로 묶기 위한 핵심 헬퍼.
    """
    if before is None or after is None:
        return []
    if _norm_ws(before) == _norm_ws(after):
        return []
    sm = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    frags: list[dict] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        old = before[i1:i2].strip()
        new = after[j1:j2].strip()
        if _norm_ws(old) == _norm_ws(new):
            continue
        if not old and not new:
            continue
        frags.append({"old": old, "new": new})
    return frags


def analyze_corrections(days: int = 90) -> dict:
    """검수 교정을 묶어 '반복되는 오류'를 surface — 검수→개선 루프 자동화.

    묶는 기준은 두 가지:
      - 직원 메모가 있으면 메모로 묶는다 (기존 동작).
      - 메모가 없는 자동 기록(검수 전→후)은 **내용 diff 토막**으로 묶는다.
        → "수정메모에만 확인할 게 아니라" 내용 변화 자체를 패턴으로 본다.

    반환:
      themes: [{keyword, count}]  메모에 등장한 테마 키워드 빈도 (내림차순)
      groups: [{note, count, has_corrected, job_span, occurrences:[...]}]
              메모 또는 내용변화로 묶은 그룹 (count 내림차순). count≥2가 체계적 오류 신호.
      total:  분석 대상 교정 수
    """
    rows = [e for e in read_corrections(days=days) if e.get("status") != "reverted"]

    theme = Counter()
    for e in rows:
        note = e.get("correction_note", "") or ""
        for k in _THEME_KEYWORDS:
            if k in note:
                theme[k] += 1
    themes = [{"keyword": k, "count": c} for k, c in theme.most_common()]

    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").strip())

    groups: dict[str, dict] = {}

    def _push(key, note, e, ptext, ctext, has_corr):
        g = groups.setdefault(key, {
            "note": note, "count": 0, "has_corrected": False, "occurrences": [],
        })
        g["count"] += 1
        if has_corr:
            g["has_corrected"] = True
        g["occurrences"].append({
            "id":             e.get("id"),
            "problem_number": e.get("problem_number"),
            "pdf_name":       e.get("pdf_name", ""),
            "job_id":         e.get("job_id", ""),
            "problem_text":   ptext,
            "corrected_text": ctext,
        })

    for e in rows:
        memo   = _norm(e.get("correction_note", ""))
        before = e.get("problem_text", "") or ""
        after  = e.get("corrected_text", "") or ""
        if memo:
            # 직원이 남긴 메모 → 메모로 묶는다 (기존 동작)
            _push("memo::" + memo, memo, e, before, after, bool(after.strip()))
            continue
        # 메모 없는 자동 기록(검수 전→후) → 내용 diff 토막으로 묶는다.
        for fr in _diff_fragments(before, after):
            old_s = fr["old"][:40] + ("…" if len(fr["old"]) > 40 else "")
            new_s = fr["new"][:40] + ("…" if len(fr["new"]) > 40 else "")
            label = (old_s or "(빈칸)") + " → " + (new_s or "(삭제)")
            key   = "diff::" + _norm(fr["old"]) + "→" + _norm(fr["new"])
            _push(key, label, e, fr["old"], fr["new"], True)

    # 여러 시험지(job)에 걸쳐 나오면 더 체계적 — span 계산
    for g in groups.values():
        g["job_span"] = len({o["job_id"] for o in g["occurrences"] if o["job_id"]})

    group_list = sorted(groups.values(), key=lambda g: (g["count"], g["job_span"]), reverse=True)
    return {"themes": themes, "groups": group_list, "total": len(rows)}


def corrections_summary(days: int = 7) -> dict:
    """기간별 수정 통계."""
    entries = read_corrections(days=days)
    applied  = [e for e in entries if e.get("status") == "applied"]
    reverted = [e for e in entries if e.get("status") == "reverted"]
    by_employee: dict[str, int] = {}
    for e in applied:
        name = e.get("employee", "알 수 없음")
        by_employee[name] = by_employee.get(name, 0) + 1
    return {
        "total":      len(applied),
        "reverted":   len(reverted),
        "by_employee": by_employee,
    }
