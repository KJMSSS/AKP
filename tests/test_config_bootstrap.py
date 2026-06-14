"""matrix_config 부트스트랩 회귀 테스트.

Railway 신규 볼륨(DATA_DIR=/data)이 비어 시작할 때, 레포 번들 시드의
학교 목록으로 자동 채워지는지 검증. 기존 학교가 있으면 건드리지 않는다.
"""
from __future__ import annotations

import json

import pytest

import scripts.web.app as appmod


@pytest.fixture()
def cfgfiles(tmp_path, monkeypatch):
    """볼륨 config와 번들 시드를 별도 임시 경로로 격리."""
    vol  = tmp_path / "vol" / "matrix_config.json"          # DATA_DIR 볼륨
    seed = tmp_path / "bundle" / "matrix_config.json"       # 레포 번들
    vol.parent.mkdir(parents=True)
    seed.parent.mkdir(parents=True)
    seed.write_text(json.dumps({
        "subjects": [{"id": "공수1", "name": "공통수학1"}],
        "schools": ["경신여고", "수완고", "고려고"],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(appmod, "_CONFIG_FILE", vol)
    monkeypatch.setattr(appmod, "_SEED_CONFIG_FILE", seed)
    return vol, seed


def test_empty_volume_bootstraps_from_seed(cfgfiles):
    vol, _ = cfgfiles
    assert not vol.exists()
    cfg = appmod._load_mconfig()
    assert cfg["schools"] == ["경신여고", "수완고", "고려고"]
    # 볼륨에 영속화됨
    saved = json.loads(vol.read_text(encoding="utf-8"))
    assert saved["schools"] == ["경신여고", "수완고", "고려고"]


def test_existing_empty_schools_gets_seeded(cfgfiles):
    # 이전에 빈 목록이 볼륨에 쓰인 상태 (현재 Railway 상황)
    vol, _ = cfgfiles
    vol.write_text(json.dumps({"subjects": [], "schools": []}, ensure_ascii=False), encoding="utf-8")
    cfg = appmod._load_mconfig()
    assert len(cfg["schools"]) == 3


def test_existing_schools_preserved(cfgfiles):
    # 사용자가 이미 다른 학교를 등록했으면 시드로 덮어쓰지 않음
    vol, _ = cfgfiles
    vol.write_text(json.dumps({
        "subjects": [{"id": "대수", "name": "대수"}],
        "schools": ["내가추가한고"],
    }, ensure_ascii=False), encoding="utf-8")
    cfg = appmod._load_mconfig()
    assert cfg["schools"] == ["내가추가한고"]


def test_no_seed_falls_back_to_empty(cfgfiles, monkeypatch):
    # 번들 시드가 없으면(경로 부재) 빈 목록 기본값
    vol, seed = cfgfiles
    seed.unlink()
    cfg = appmod._load_mconfig()
    assert cfg["schools"] == []
    assert "subjects" in cfg


def test_local_same_path_no_self_seed(tmp_path, monkeypatch):
    # 로컬: _CONFIG_FILE == _SEED_CONFIG_FILE 이면 시드 무의미 → 기본값
    same = tmp_path / "matrix_config.json"
    monkeypatch.setattr(appmod, "_CONFIG_FILE", same)
    monkeypatch.setattr(appmod, "_SEED_CONFIG_FILE", same)
    cfg = appmod._load_mconfig()
    assert cfg["schools"] == []
