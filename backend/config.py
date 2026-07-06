"""환경설정 로더 — 루트 .env 자동 로드."""
from __future__ import annotations

import os
from pathlib import Path


def load_env(dotenv_path: str | None = None) -> dict:
    """루트 .env 를 찾아 os.environ 에 주입(이미 있으면 유지). 로드된 키 반환."""
    if dotenv_path is None:
        here = Path(__file__).resolve()
        for parent in [here.parent, *here.parents]:
            cand = parent / ".env"
            if cand.exists():
                dotenv_path = str(cand)
                break
    loaded = {}
    if not dotenv_path or not Path(dotenv_path).exists():
        return loaded
    for line in Path(dotenv_path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)
        loaded[k] = "***"
    return loaded


def require(*keys: str) -> None:
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"필수 환경변수 누락: {', '.join(missing)} (.env 확인)")
