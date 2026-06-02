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

import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

def _data_dir() -> Path:
    d = os.environ.get("DATA_DIR", "")
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


def append_correction(entry: dict) -> str:
    """수정 항목 저장. 생성된 id 반환."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
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
