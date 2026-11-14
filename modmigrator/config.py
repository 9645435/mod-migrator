from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .util import app_data_dir, load_json, save_json


CONFIG_PATH = app_data_dir() / "config.json"


@dataclass
class AppConfig:
    last_source: str = ""
    last_target: str = ""
    curseforge_api_key: str = ""
    use_cfwidget: bool = True
    overwrite: bool = False
    window_geometry: str = ""
    auto_open_folder: bool = False
    extra: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> "AppConfig":
        data = load_json(CONFIG_PATH, {}) or {}
        if not isinstance(data, dict):
            data = {}
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
        cfg = cls(**known)
        env_key = os.environ.get("CURSEFORGE_API_KEY", "").strip()
        if env_key and not cfg.curseforge_api_key:
            cfg.curseforge_api_key = env_key
        return cfg

    def save(self) -> None:
        save_json(CONFIG_PATH, asdict(self))

    def set_paths(self, source: str | None = None, target: str | None = None) -> None:
        if source is not None:
            self.last_source = source
        if target is not None:
            self.last_target = target

    @property
    def has_cf_key(self) -> bool:
        return bool(self.curseforge_api_key.strip())


def clear_cache() -> int:
    from .util import cache_dir

    removed = 0
    root = cache_dir()
    for path in root.glob("*.json"):
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def cache_size() -> int:
    from .util import cache_dir

    total = 0
    for path in cache_dir().glob("*.json"):
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return total


def config_path() -> Path:
    return CONFIG_PATH
