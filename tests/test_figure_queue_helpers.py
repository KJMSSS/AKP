"""그림 검수 큐 라우트 헬퍼 회귀 테스트 (app.py).

핵심 시나리오: needs_apply 집계 의미, applied_at fresh 병합 마킹
(동시 결정 보존), 재변환 큐 리셋(레거시 포함), 등록 시 첫 빌드 스탬프.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.web.app as appmod


@pytest.fixture()
def figq(tmp_path, monkeypatch):
    """_FIGQ_DIR/_TMP_DIR를 임시 경로로 격리."""
    fdir = tmp_path / "figure_queue"
    fdir.mkdir()
    monkeypatch.setattr(appmod, "_FIGQ_DIR", fdir)
    monkeypatch.setattr(appmod, "_TMP_DIR", tmp_path)
    return fdir


def _write_queue(figq: Path, key: str, data: dict) -> None:
    d = figq / key
    d.mkdir(parents=True, exist_ok=True)
    (d / "items.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


class TestSummary:
    def test_no_queue_returns_none(self, figq):
        assert appmod._figq_summary("없는키") is None

    def test_pending_and_needs_apply(self, figq):
        _write_queue(figq, "k", {"items": {
            # 검수 대기
            "1": {"status": "pending"},
            # 결정됐지만 한 번도 반영 안 됨 (사람 결정 여부 무관) → needs_apply
            "2": {"status": "auto_selected"},
            # 첫 빌드에서 반영됨 → 완료
            "3": {"status": "auto_selected", "applied_at": "2026-06-12T10:00:00"},
            # 반영 후 결정 변경 → needs_apply
            "4": {"status": "manual_selected",
                  "applied_at": "2026-06-12T10:00:00",
                  "updated_at": "2026-06-12T11:00:00"},
            # 반영 후 변경 없음 → 완료
            "5": {"status": "skipped",
                  "applied_at": "2026-06-12T11:00:00",
                  "updated_at": "2026-06-12T10:30:00"},
        }})
        fs = appmod._figq_summary("k")
        assert fs == {"total": 5, "pending": 1, "needs_apply": 2}


class TestMarkApplied:
    def test_marks_only_applied_and_unchanged(self, figq):
        items = {
            "1": {"status": "manual_selected", "updated_at": "T1"},
            "2": {"status": "skipped", "updated_at": "T1"},
            "3": {"status": "auto_selected", "updated_at": "T1"},  # 반영 실패(missing)
        }
        _write_queue(figq, "k", {"items": items})
        snapshot = json.loads(json.dumps(items))

        # 반영 도중 1번에 새 결정이 들어옴 (동시 decision)
        concurrent = json.loads(json.dumps(items))
        concurrent["1"]["status"] = "skipped"
        concurrent["1"]["updated_at"] = "T2"
        _write_queue(figq, "k", {"items": concurrent})

        result = {"applied_nos": ["1"], "skipped_nos": ["2"]}  # 3번은 missing
        appmod._figq_mark_applied("k", snapshot, result, "out.hwpx")

        saved = json.loads((figq / "k" / "items.json").read_text(encoding="utf-8"))
        si = saved["items"]
        # 1번: 도중 변경 → 마킹 생략 + 새 결정 보존 (needs_apply로 살아남음)
        assert "applied_at" not in si["1"]
        assert si["1"]["status"] == "skipped" and si["1"]["updated_at"] == "T2"
        # 2번: 정상 마킹
        assert si["2"].get("applied_at")
        # 3번: 반영 안 됐으므로 마킹 없음
        assert "applied_at" not in si["3"]
        assert saved["last_applied"]["target"] == "out.hwpx"


class TestRegisterQueue:
    def _png(self, tmp_path: Path, name: str) -> Path:
        from PIL import Image
        p = tmp_path / name
        Image.new("RGB", (10, 10)).save(str(p))
        return p

    def test_reset_on_job_change_including_legacy(self, figq, tmp_path):
        # 레거시 큐: job_id 필드 없음 + 옛 경로 항목
        _write_queue(figq, "k", {"items": {
            "1": {"status": "manual_selected", "crop_path": "old_job_figs/1.png"},
        }})
        png = self._png(tmp_path, "fig1.png")
        appmod._register_figure_queue("k", "newjob", {"1"}, {"1": png})

        saved = json.loads((figq / "k" / "items.json").read_text(encoding="utf-8"))
        assert saved["job_id"] == "newjob"
        # 레거시 항목이 리셋되고 새 항목으로 교체됨
        assert saved["items"]["1"]["status"] != "manual_selected" or \
            saved["items"]["1"].get("crop_path") != "old_job_figs/1.png"

    def test_same_job_keeps_decisions(self, figq, tmp_path):
        png = self._png(tmp_path, "fig2.png")
        appmod._register_figure_queue("k2", "job1", {"3"}, {"3": png})
        # 사람이 결정
        q = json.loads((figq / "k2" / "items.json").read_text(encoding="utf-8"))
        q["items"]["3"]["status"] = "skipped"
        q["items"]["3"]["updated_at"] = "T1"
        _write_queue(figq, "k2", q)
        # 같은 잡으로 재등록 (중복 호출) — 결정 보존
        appmod._register_figure_queue("k2", "job1", {"3"}, {"3": png})
        saved = json.loads((figq / "k2" / "items.json").read_text(encoding="utf-8"))
        assert saved["items"]["3"]["status"] == "skipped"

    def test_first_build_inserted_gets_applied_stamp(self, figq, tmp_path):
        png = self._png(tmp_path, "fig3.png")
        # 4번은 첫 빌드 삽입(figure_map 존재), 5번은 미삽입(figure_map 없음)
        appmod._register_figure_queue("k3", "job1", {"4", "5"}, {"4": png})
        saved = json.loads((figq / "k3" / "items.json").read_text(encoding="utf-8"))
        assert saved["items"]["4"].get("applied_at")      # 이미 반영됨
        assert not saved["items"]["5"].get("applied_at")  # 플레이스홀더 잔존 → 반영 필요
        fs = appmod._figq_summary("k3")
        # 5번: auto 이미지가 없어 pending 또는 needs_apply 어느 쪽이든 '완료'는 아님
        assert fs["pending"] + fs["needs_apply"] >= 1
