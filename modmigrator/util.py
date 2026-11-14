from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import quote


APP_NAME = "ModMigrator"
APP_DISPLAY_NAME = "Mod 迁移助手"
APP_VERSION = "1.0.5"


def resource_path(relative: str) -> Path:
    """Path to a bundled resource, both in source and PyInstaller builds."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / relative
    return Path(__file__).resolve().parent.parent / relative


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
    if base:
        path = Path(base) / APP_NAME
    else:
        path = Path.home() / f".{APP_NAME.lower()}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def local_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        path = Path(base) / APP_NAME
    else:
        path = Path.home() / f".{APP_NAME.lower()}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    path = local_data_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir() -> Path:
    path = local_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def norm_key(value: str) -> str:
    """Lowercase alphanumeric-only key used for loose name matching."""
    value = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in value.casefold() if ch.isalnum())


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = value.casefold()
    value = re.sub(r"['’]", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def elide_middle(text: str, limit: int = 68) -> str:
    if len(text) <= limit:
        return text
    keep = limit - 1
    left = keep // 2
    right = keep - left
    return text[:left] + "…" + text[-right:]


def safe_filename(name: str, fallback: str = "mod.jar") -> str:
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = name.replace("\x00", "").strip().strip(".")
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    if not name:
        name = fallback
    stem, dot, suffix = name.rpartition(".")
    if len(name) > 180:
        name = (stem[:170] + "." + suffix) if dot else name[:180]
    if not name.lower().endswith((".jar", ".zip")):
        name += ".jar"
    return name


def human_size(size: int) -> str:
    if not size:
        return "未知"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def open_local_path(path: Path) -> None:
    path = Path(path)
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        os.system(f'open "{path}"')
    else:
        os.system(f'xdg-open "{path}"')


def quote_path_component(value: str) -> str:
    return quote(str(value), safe="")


def load_json(path: Path, default=None):
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
