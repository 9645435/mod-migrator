from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.11+ ships tomllib
    tomllib = None  # type: ignore[assignment]

from .models import ModMeta
from .util import norm_key, slugify


PLACEHOLDER_RE = re.compile(r"^\$\{.+\}$")


class JarError(Exception):
    pass


def hash_file(path: Path, compute_sha512: bool = True) -> tuple[str, str]:
    sha1 = hashlib.sha1()
    sha512 = hashlib.sha512() if compute_sha512 else None
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            sha1.update(chunk)
            if sha512 is not None:
                sha512.update(chunk)
    return sha1.hexdigest(), (sha512.hexdigest() if sha512 else "")


def _read_zip_text(zf: zipfile.ZipFile, name: str) -> str:
    try:
        raw = zf.read(name)
    except KeyError:
        return ""
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def _parse_manifest(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    current_key = ""
    for raw_line in text.splitlines():
        if raw_line.startswith(" ") and current_key:
            result[current_key] += raw_line[1:]
            continue
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        current_key = key.strip()
        result[current_key] = value.strip()
    return result


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    return "" if PLACEHOLDER_RE.match(text) else text


def _first(value: Any, default: str = "") -> str:
    if isinstance(value, (list, tuple)):
        return str(value[0]).strip() if value else default
    return str(value or "").strip()


def _authors(value: Any) -> list[str]:
    authors: list[str] = []
    if isinstance(value, str):
        authors.extend(x.strip() for x in re.split(r"[,;/]", value) if x.strip())
    elif isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, dict):
                name = item.get("name") or item.get("title") or item.get("username")
                if name:
                    authors.append(str(name))
            elif item:
                authors.append(str(item))
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in {"name", "title", "username"}:
                authors.append(str(item))
            elif isinstance(item, dict):
                name = item.get("name") or item.get("title")
                if name:
                    authors.append(str(name))
    return authors


def _normalize_dep(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return ""


def _fabric_variant(data: dict[str, Any], manifest: dict[str, str]) -> dict[str, Any]:
    depends = data.get("depends") if isinstance(data.get("depends"), dict) else {}
    return {
        "loader": "fabric",
        "mod_id": _clean(data.get("id")),
        "name": _clean(data.get("name")),
        "version": _clean(data.get("version")) or _clean(manifest.get("Implementation-Version")),
        "description": _clean(data.get("description")),
        "authors": _authors(data.get("authors")),
        "license": _clean(data.get("license")),
        "environment": _clean(data.get("environment")),
        "mc_constraint": _normalize_dep(depends.get("minecraft")),
        "source": "fabric.mod.json",
        "extra": {"depends": depends},
    }


def _quilt_variant(data: dict[str, Any], manifest: dict[str, str]) -> dict[str, Any]:
    loader = data.get("quilt_loader") if isinstance(data.get("quilt_loader"), dict) else {}
    metadata = loader.get("metadata") if isinstance(loader.get("metadata"), dict) else {}
    depends = loader.get("depends")
    mc_constraint = ""
    if isinstance(depends, list):
        for dep in depends:
            if isinstance(dep, dict) and str(dep.get("id", "")).lower() == "minecraft":
                mc_constraint = _normalize_dep(dep.get("versions"))
                break
    elif isinstance(depends, dict):
        mc_constraint = _normalize_dep(depends.get("minecraft"))
    return {
        "loader": "quilt",
        "mod_id": _clean(loader.get("id")),
        "name": _clean(metadata.get("name")),
        "version": _clean(loader.get("version")) or _clean(manifest.get("Implementation-Version")),
        "description": _clean(metadata.get("description")),
        "authors": _authors(metadata.get("contributors")),
        "license": _clean(metadata.get("license")),
        "environment": "",
        "mc_constraint": mc_constraint,
        "source": "quilt.mod.json",
        "extra": {},
    }


def _toml_dependencies(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    mc_constraint = ""
    deps_obj = data.get("dependencies")
    if isinstance(deps_obj, dict):
        for _owner, entries in deps_obj.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("modId", "")).lower() == "minecraft":
                    mc_constraint = _normalize_dep(entry.get("versionRange"))
                    return mc_constraint, entry
    return mc_constraint, {}


def _toml_variant(text: str, loader: str, source: str, manifest: dict[str, str]) -> dict[str, Any] | None:
    if not text:
        return None
    data: dict[str, Any] = {}
    if tomllib is not None:
        try:
            data = tomllib.loads(text)
        except Exception:
            data = {}
    if not data:
        data = _toml_fallback(text)
    mods = data.get("mods")
    first: dict[str, Any] = {}
    if isinstance(mods, list):
        for item in mods:
            if isinstance(item, dict):
                first = item
                break
    if not first:
        first = data
    mod_id = _clean(first.get("modId") or first.get("modid"))
    if not mod_id:
        return None
    mc_constraint, _ = _toml_dependencies(data)
    return {
        "loader": loader,
        "mod_id": mod_id,
        "name": _clean(first.get("displayName") or first.get("name")),
        "version": _clean(first.get("version")) or _clean(manifest.get("Implementation-Version")),
        "description": _clean(first.get("description")),
        "authors": _authors(first.get("authors") or first.get("authorList")),
        "license": _clean(first.get("license")),
        "environment": "",
        "mc_constraint": mc_constraint,
        "source": source,
        "extra": {},
    }


def _toml_fallback(text: str) -> dict[str, Any]:
    """Very tolerant fallback for malformed mods.toml."""
    data: dict[str, Any] = {}
    blocks = re.split(r"(?m)^\s*\[\[mods\]\]\s*$", text)
    if len(blocks) > 1:
        block = re.split(r"(?m)^\s*\[\[", blocks[1])[0]
        item: dict[str, str] = {}
        for key in ("modId", "displayName", "version", "description", "authors", "license"):
            match = re.search(rf'(?m)^\s*{key}\s*=\s*"([^"]*)"', block)
            if match:
                item[key] = match.group(1)
        data["mods"] = [item]
    dep_blocks = re.findall(
        r"(?ms)^\s*\[\[dependencies\.[^\]]+\]\]\s*(.*?)(?=^\s*\[\[|\Z)", text
    )
    deps: list[dict[str, str]] = []
    for block in dep_blocks:
        item = {}
        for key in ("modId", "versionRange"):
            match = re.search(rf'(?m)^\s*{key}\s*=\s*"([^"]*)"', block)
            if match:
                item[key] = match.group(1)
        if item:
            deps.append(item)
    if deps:
        data["dependencies"] = {"mod": deps}
    return data


def _mcmod_variant(data: Any) -> dict[str, Any] | None:
    items = data
    if isinstance(data, dict):
        items = data.get("modList") or data.get("modlist") or []
    if not isinstance(items, list) or not items:
        return None
    first = next((x for x in items if isinstance(x, dict)), None)
    if not first:
        return None
    mod_id = _clean(first.get("modid") or first.get("modId"))
    if not mod_id:
        return None
    accepted = first.get("acceptedMinecraftVersions") or first.get("mcversion") or ""
    return {
        "loader": "forge",
        "mod_id": mod_id,
        "name": _clean(first.get("name")),
        "version": _clean(first.get("version")),
        "description": _clean(first.get("description")),
        "authors": _authors(first.get("authorList") or first.get("author")),
        "license": "",
        "environment": "",
        "mc_constraint": _normalize_dep(accepted),
        "source": "mcmod.info",
        "extra": {},
    }


def _filename_hints(path: Path) -> tuple[str, str]:
    stem = path.stem
    version = ""
    match = re.search(r"(?<![A-Za-z0-9])v?(\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.\-]+)?)", stem)
    if match:
        version = match.group(1)
    name = re.sub(r"[-_.](?:fabric|forge|neoforge|quilt|mc)?[-_.]?\d.*$", "", stem, flags=re.I)
    name = re.sub(r"[-_.](?:fabric|forge|neoforge|quilt)$", "", name, flags=re.I)
    name = name.replace("_", " ").replace("-", " ").strip()
    return name or stem, version


def read_metadata(
    path: str | Path,
    preferred_loader: str | None = None,
    compute_hashes: bool = True,
) -> ModMeta:
    path = Path(path)
    meta = ModMeta(path=path)
    if compute_hashes:
        try:
            meta.sha1, meta.sha512 = hash_file(path)
        except OSError as exc:
            meta.error = f"无法读取文件：{exc}"
            return meta

    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        meta.error = f"不是有效的 JAR 文件：{exc}"
        meta.name = path.stem
        return meta

    with zf:
        names = set(zf.namelist())
        manifest = _parse_manifest(_read_zip_text(zf, "META-INF/MANIFEST.MF"))
        variants: list[dict[str, Any]] = []

        if "fabric.mod.json" in names:
            text = _read_zip_text(zf, "fabric.mod.json")
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    variants.append(_fabric_variant(data, manifest))
            except ValueError:
                pass

        if "quilt.mod.json" in names:
            text = _read_zip_text(zf, "quilt.mod.json")
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    variants.append(_quilt_variant(data, manifest))
            except ValueError:
                pass

        for candidate, loader in (
            ("META-INF/neoforge.mods.toml", "neoforge"),
            ("META-INF/mods.toml", "forge"),
        ):
            if candidate in names:
                variant = _toml_variant(
                    _read_zip_text(zf, candidate), loader, candidate.split("/")[-1], manifest
                )
                if variant:
                    variants.append(variant)

        if "mcmod.info" in names:
            text = _read_zip_text(zf, "mcmod.info")
            try:
                variant = _mcmod_variant(json.loads(text))
                if variant:
                    variants.append(variant)
            except ValueError:
                pass

        variants = [v for v in variants if v.get("mod_id")]
        if not variants:
            name, version = _filename_hints(path)
            meta.name = name
            meta.version = version or _clean(manifest.get("Implementation-Version"))
            meta.error = "未找到 Fabric / Forge / NeoForge / Quilt 模组元数据"
            meta.metadata_source = "filename"
            return meta

        priority = {"fabric": 1, "quilt": 2, "forge": 3, "neoforge": 4}
        chosen = None
        if preferred_loader:
            for variant in variants:
                if variant["loader"] == preferred_loader:
                    chosen = variant
                    break
            if chosen is None and preferred_loader == "quilt":
                for variant in variants:
                    if variant["loader"] == "fabric":
                        chosen = variant
                        break
        if chosen is None:
            chosen = sorted(variants, key=lambda v: priority.get(v["loader"], 0), reverse=True)[0]

        meta.loader = chosen["loader"]
        meta.mod_id = chosen["mod_id"]
        meta.name = chosen.get("name") or meta.mod_id
        meta.version = chosen.get("version") or _clean(manifest.get("Implementation-Version"))
        meta.mc_constraint = chosen.get("mc_constraint") or ""
        meta.description = re.sub(r"\s+", " ", chosen.get("description") or "").strip()[:600]
        meta.authors = chosen.get("authors") or []
        meta.license = chosen.get("license") or ""
        meta.environment = chosen.get("environment") or ""
        meta.metadata_source = chosen.get("source") or ""
        meta.is_mod = True
        meta.extra["variants"] = variants
        if not meta.version:
            _name, guessed = _filename_hints(path)
            meta.version = guessed
        if not meta.mod_id:
            meta.mod_id = slugify(meta.name)
        return meta


def read_mods_folder(folder: Path, preferred_loader: str | None = None) -> list[ModMeta]:
    folder = Path(folder)
    if not folder.is_dir():
        return []
    metas = []
    for path in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
        if not path.is_file():
            continue
        lower = path.name.lower()
        if not lower.endswith(".jar") or lower.endswith(".jar.disabled"):
            continue
        metas.append(read_metadata(path, preferred_loader=preferred_loader))
    return metas
