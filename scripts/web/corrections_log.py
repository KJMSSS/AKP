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

_LOG_DIR  = _data_dir()
_LOG_FILE = _LOG_DIR / "corrections.jsonl"


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
