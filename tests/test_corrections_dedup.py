"""검수 수정 로그 중복 방지 회귀 테스트.

규칙: 같은 잡+문제+메모+교정 = 중복(스킵). 같은 문제라도 메모가 다르면 새 항목.
되돌린(reverted) 항목은 다시 등록 허용.
"""
from __future__ import annotations

import json

import pytest

import scripts.web.corrections_log as cl


@pytest.fixture()
def logfile(tmp_path, monkeypatch):
    f = tmp_path / "corrections.jsonl"
    monkeypatch.setattr(cl, "_LOG_DIR", tmp_path)
    monkeypatch.setattr(cl, "_LOG_FILE", f)
    return f


def _count(f) -> int:
    return len([l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]) if f.exists() else 0


def _entry(prob, note, corrected="", job="J1"):
    return {"job_id": job, "problem_number": prob, "problem_text": "원본",
            "correction_note": note, "corrected_text": corrected}


class TestAppendDedup:
    def test_identical_resubmit_skipped(self, logfile):
        id1 = cl.append_correction(_entry(3, "집합기호 없음"))
        id2 = cl.append_correction(_entry(3, "집합기호 없음"))  # 동일 재제출
        assert id1 == id2          # 같은 id 반환
        assert _count(logfile) == 1  # 새로 안 씀

    def test_same_problem_different_note_kept(self, logfile):
        cl.append_correction(_entry(3, "집합기호 없음"))
        cl.append_correction(_entry(3, "분수도 틀림"))   # 같은 문제, 다른 메모
        assert _count(logfile) == 2

    def test_same_note_different_corrected_kept(self, logfile):
        cl.append_correction(_entry(3, "고침", corrected="$A \\cap B$"))
        cl.append_correction(_entry(3, "고침", corrected="$A \\cup B$"))
        assert _count(logfile) == 2

    def test_different_problem_kept(self, logfile):
        cl.append_correction(_entry(3, "집합기호 없음"))
        cl.append_correction(_entry(4, "집합기호 없음"))
        assert _count(logfile) == 2

    def test_reverted_allows_reregister(self, logfile):
        cid = cl.append_correction(_entry(3, "집합기호 없음"))
        cl.revert_correction(cid)
        # 되돌린 뒤 같은 내용 재등록 → 허용 (새 활성 항목)
        cl.append_correction(_entry(3, "집합기호 없음"))
        applied = [e for e in cl.read_corrections() if e.get("status") == "applied"]
        assert len(applied) == 1


class TestDedupeExisting:
    def test_dedupe_removes_existing_dups(self, logfile):
        # append_correction을 우회해 직접 중복 기록 (기존 오염 로그 재현)
        rows = [
            _entry(3, "집합기호 없음"), _entry(4, "집합기호 없음"),
            _entry(3, "집합기호 없음"), _entry(4, "집합기호 없음"),  # 중복
            _entry(13, "루트 아님"),
        ]
        import uuid
        with logfile.open("w", encoding="utf-8") as fh:
            for r in rows:
                r = {"id": uuid.uuid4().hex[:12], "ts": "2026-06-15T00:00:00",
                     **r, "status": "applied"}
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        assert _count(logfile) == 5
        removed = cl.dedupe_corrections()
        assert removed == 2
        assert _count(logfile) == 3

    def test_dedupe_keeps_reverted(self, logfile):
        import uuid
        rows = [
            dict(_entry(3, "x"), id="a", ts="t", status="reverted"),
            dict(_entry(3, "x"), id="b", ts="t", status="applied"),
            dict(_entry(3, "x"), id="c", ts="t", status="applied"),  # 중복
        ]
        with logfile.open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        removed = cl.dedupe_corrections()
        assert removed == 1  # applied 중복 1개만 제거, reverted 보존
        kept = [json.loads(l) for l in logfile.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert any(e["status"] == "reverted" for e in kept)
