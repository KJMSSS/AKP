"""경로·설정·레지스트리 단일 출처 — app.py / engine_api.py 가 공유한다.

- DATA_DIR: Railway 볼륨 마운트 > DATA_DIR env > scripts/web/data
- WORK_DIR: 변환 작업 파일(.work 상당). 작업 중에만 쓰고 3일 후 자동 정리.
- matrix_config.json / matrix_registry.json I/O (락 포함)
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from fastapi import HTTPException

_HERE = Path(__file__).resolve().parent

# Railway 볼륨이 마운트돼 있으면 그 경로가 유일한 영속 저장소다. DATA_DIR 환경변수가
# 볼륨과 다른 경로를 가리키면 앱이 휘발성 컨테이너 디스크에 쓰게 돼 재배포마다
# 데이터가 사라진다 — 볼륨 마운트를 최우선한다.
_VOL_MOUNT = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
DATA_DIR = Path(_VOL_MOUNT or os.environ.get("DATA_DIR") or str(_HERE / "data"))
DATA_DIR.mkdir(exist_ok=True, parents=True)

# 변환 작업 디렉터리 — job 별 하위 폴더(원본·페이지 PNG·크롭·result.hwpx·state.json).
# 결과물의 영속 보관처는 Google Drive 이고, 여기는 검수/재빌드용 임시 작업장이다.
WORK_DIR = DATA_DIR / "work"
WORK_DIR.mkdir(exist_ok=True, parents=True)

UPLOADS_DIR = DATA_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True, parents=True)

CONFIG_FILE = DATA_DIR / "matrix_config.json"
REGISTRY_FILE = DATA_DIR / "matrix_registry.json"

# RLock: update_registry_entry 가 락을 잡은 채 load/save 를 호출한다(재진입).
_config_lock = threading.RLock()
_registry_lock = threading.RLock()

DEFAULT_SUBJECTS = [
    {"id": "공수1", "name": "공통수학1", "grade": "1", "sem": "1"},
    {"id": "공수2", "name": "공통수학2", "grade": "1", "sem": "2"},
    {"id": "대수",  "name": "대수",      "grade": "2", "sem": "1"},
    {"id": "확통",  "name": "확률과 통계", "grade": "2", "sem": "2"},
    {"id": "기하",  "name": "기하",      "grade": "2", "sem": "2"},
    {"id": "미적1", "name": "미적분1",   "grade": "2", "sem": "2"},
    {"id": "미적2", "name": "미적분2",   "grade": "3", "sem": "1"},
]

# 레포에 커밋된 번들 config(학교·과목 목록) — 신규 DATA_DIR 볼륨 부트스트랩용.
_SEED_CONFIG_FILE = _HERE / "data" / "matrix_config.json"


def _load_seed_config() -> dict | None:
    """번들 시드 config 로드. CONFIG_FILE 과 같은 경로(로컬)면 시드 의미 없으므로 None."""
    if _SEED_CONFIG_FILE == CONFIG_FILE or not _SEED_CONFIG_FILE.exists():
        return None
    try:
        return json.loads(_SEED_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_mconfig() -> dict:
    with _config_lock:
        existed = CONFIG_FILE.exists()
        cfg: dict | None = None
        if existed:
            try:
                cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception:
                cfg = None
        need_write = (cfg is None) or (not existed)
        if cfg is None:
            cfg = {"subjects": DEFAULT_SUBJECTS, "schools": []}
        else:
            # 키 누락 파일 방어 — 핸들러의 cfg["schools"] KeyError 방지
            cfg.setdefault("subjects", DEFAULT_SUBJECTS)
            cfg.setdefault("schools", [])

        # 학교 목록이 비어 있으면(신규 Railway 볼륨 등) 번들 시드로 부트스트랩
        if not cfg.get("schools"):
            seed = _load_seed_config()
            if seed and seed.get("schools"):
                cfg["schools"] = seed["schools"]
                cfg["subjects"] = seed.get("subjects") or cfg.get("subjects") or DEFAULT_SUBJECTS
                need_write = True
                print(f"  [config] 번들 시드 부트스트랩 — 학교 {len(cfg['schools'])}개 등록")

        if need_write:
            CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return cfg


def save_mconfig(cfg: dict) -> None:
    with _config_lock:
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def validate_safe_key(key: str) -> str:
    """레지스트리 키/잡ID에 경로 탈출 문자가 없는지 확인."""
    if ".." in key or "/" in key or "\\" in key:
        raise HTTPException(400, "잘못된 키 값입니다.")
    return key


def load_registry() -> dict:
    with _registry_lock:
        if REGISTRY_FILE.exists():
            try:
                return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}


def save_registry(reg: dict) -> None:
    with _registry_lock:
        REGISTRY_FILE.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")


def update_registry_entry(registry_key: str, **fields) -> dict | None:
    """레지스트리 항목 부분 갱신(없으면 생성). 갱신된 항목 반환.

    빌드/분석 완료는 스레드풀·백그라운드 스레드에서 호출된다 — 읽기-수정-쓰기
    전 구간을 락으로 묶어 동시 갱신 시 한쪽이 사라지는 것을 막는다.
    """
    from datetime import datetime
    with _registry_lock:
        reg = load_registry()
        entry = reg.get(registry_key, {})
        entry.update(fields)
        entry.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
        entry["updated_at"] = datetime.now().isoformat(timespec="seconds")
        reg[registry_key] = entry
        save_registry(reg)
    return entry
