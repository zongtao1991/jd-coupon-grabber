"""配置管理模块"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = BASE_DIR / "config.yaml"
DATA_DIR = BASE_DIR / "data"
COOKIES_FILE = DATA_DIR / "cookies.json"
TASKS_FILE = DATA_DIR / "tasks.json"
SCREENSHOT_DIR = DATA_DIR / "screenshots"

_DEFAULTS: dict[str, Any] = {
    "server": {"host": "0.0.0.0", "port": 8080},
    "grabber": {
        "preheat_seconds": 3,
        "retry_count": 3,
        "retry_interval_ms": 200,
        "screenshot": True,
    },
    "browser": {"headless": True, "user_agent": ""},
    "ntp": {"server": "ntp.aliyun.com", "sync_before_grab": True},
}


def _deep_merge(base: dict, override: dict) -> dict:
    merged = base.copy()
    for k, v in override.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged


class Config:
    def __init__(self, path: str | Path | None = None):
        self._data = _DEFAULTS.copy()
        cfg_path = Path(path) if path else DEFAULT_CONFIG
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                user_cfg = yaml.safe_load(f) or {}
            self._data = _deep_merge(self._data, user_cfg)
        # ensure dirs
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    def get(self, *keys: str, default: Any = None) -> Any:
        val = self._data
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k)
            else:
                return default
            if val is None:
                return default
        return val

    @property
    def server(self) -> dict:
        return self._data.get("server", {})

    @property
    def grabber(self) -> dict:
        return self._data.get("grabber", {})

    @property
    def browser(self) -> dict:
        return self._data.get("browser", {})

    @property
    def ntp(self) -> dict:
        return self._data.get("ntp", {})


cfg = Config()
