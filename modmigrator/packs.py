from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any

from .models import Instance, ModMeta
from .modjar import hash_file
from .util import slugify


PACK_KINDS = {"resourcepack", "shader"}
_VERSION_SUFFIX_RE = re.compile(
    r"^(.*?)[ _-]+(v?\d+(?:\.\d+)+(?:[-+][0-9A-Za-z._-]+)?|r\d+(?:\.\d+)*)([ _-].*)?$",
    re.IGNORECASE,
)


def pack_folder(instance: Instance, kind: str) -> Path:
    if kind == "resourcepack":
        return instance.resourcepacks_dir
    if kind == "shader":
        return instance.shaderpacks_dir
    return instance.mods_dir


def _split_name(filename: str) -> tuple[str, str]:
    stem = Path(filename).stem
    match = _VERSION_SUFFIX_RE.match(stem)
    if not match:
        return stem.replace("_", " ").strip(), ""
    name = match.group(1).replace("_", " ").strip(" -_")
    return (name or stem), match.group(2)


def _description(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str):
            return text
        extra = value.get("extra")
        if isinstance(extra, list):
            return "".join(str(item) for item in extra)
    return ""


def read_pack_metadata(path: str | Path, kind: str) -> ModMeta:
    """Read a .zip resource pack / shader pack without requiring mod metadata."""
    path = Path(path)
    name, version = _split_name(path.name)
    meta = ModMeta(
        path=path,
        name=name,
        version=version,
        mod_id=slugify(name),
        loader="minecraft" if kind == "resourcepack" else "iris",
        kind=kind,
        is_mod=False,
        metadata_source="资源包文件名",
    )
    try:
        meta.sha1, meta.sha512 = hash_file(path)
    except OSError as exc:
        meta.error = f"无法读取文件：{exc}"
        return meta

    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "pack.mcmeta" in names:
                meta.extra["has_pack_mcmeta"] = True
                try:
                    data = json.loads(
                        zf.read("pack.mcmeta").decode("utf-8", errors="replace")
                    )
                    pack = data.get("pack") if isinstance(data, dict) else None
                    if isinstance(pack, dict):
                        meta.description = _description(pack.get("description"))[:600]
                        meta.extra["pack_format"] = pack.get("pack_format")
                        meta.extra["supported_formats"] = pack.get("supported_formats")
                except (ValueError, UnicodeDecodeError) as exc:
                    meta.extra["pack_meta_error"] = str(exc)
            else:
                # Do not reject a pack just because it has no pack.mcmeta.
                # Filename metadata is enough; the copy fallback must still work.
                meta.extra["has_pack_mcmeta"] = False
            meta.error = ""
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        meta.error = f"不是有效的资源包压缩文件：{exc}"
    return meta


def read_packs_folder(folder: Path, kind: str) -> list[ModMeta]:
    folder = Path(folder)
    if not folder.is_dir():
        return []
    metas: list[ModMeta] = []
    for path in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
        if not path.is_file():
            continue
        lower = path.name.lower()
        if not lower.endswith(".zip") or lower.endswith(".zip.disabled"):
            continue
        metas.append(read_pack_metadata(path, kind))
    return metas
