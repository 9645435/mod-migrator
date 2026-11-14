from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import Instance


VERSION_RE = re.compile(r"^\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.\-]+)?$")
SNAPSHOT_RE = re.compile(r"^(\d+\.\d+(?:\.\d+)?)-snapshot-\d+$", re.IGNORECASE)
LOADER_BUILD_RE = re.compile(
    r"^(\d+\.\d+(?:\.\d+)?)[-_ ](?:fabric|quilt|forge|neoforge)(?:[-_ ].*)?$",
    re.IGNORECASE,
)
LOADER_NAMES = {
    "fabric": "Fabric",
    "quilt": "Quilt",
    "forge": "Forge",
    "neoforge": "NeoForge",
    "vanilla": "原版",
}


class InstanceError(Exception):
    pass


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _extract_mc_version(value: Any) -> str:
    """Return the Minecraft release a launcher version id belongs to.

    Modern launcher ids are not always just ``26.3``. Fabric/Forge builds look
    like ``26.3-Fabric 0.19.5`` and snapshots like ``26.3-snapshot-7``; both
    still target the 26.3 release for mod matching.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    snapshot = SNAPSHOT_RE.match(text)
    if snapshot:
        return snapshot.group(1)
    loader_build = LOADER_BUILD_RE.match(text)
    if loader_build:
        return loader_build.group(1)
    if VERSION_RE.match(text):
        return text
    return ""


def _looks_like_mc_version(value: Any) -> bool:
    return bool(_extract_mc_version(value))


def _candidate_game_dirs(path: Path) -> list[tuple[int, Path]]:
    candidates: list[tuple[int, Path]] = []

    def add(candidate: Path, base_score: int = 0) -> None:
        if not candidate.is_dir():
            return
        score = base_score
        if (candidate / "versions").is_dir():
            score += 6
        if (candidate / "mods").is_dir():
            score += 4
        if (candidate / "launcher_profiles.json").is_file():
            score += 3
        if (candidate / "options.txt").is_file():
            score += 2
        if (candidate / "mmc-pack.json").is_file():
            score += 2
        if (candidate / "minecraftinstance.json").is_file():
            score += 3
        if score:
            candidates.append((score, candidate))

    add(path, 2)
    add(path / ".minecraft")
    add(path / "minecraft")
    if path.name.lower() == "mods":
        add(path.parent)
    return sorted(candidates, key=lambda item: item[0], reverse=True)


def _jar_count(folder: Path) -> int:
    """Count enabled mod jars directly inside a mods folder."""
    if not folder.is_dir():
        return 0
    return sum(
        1
        for p in folder.iterdir()
        if p.is_file()
        and p.suffix.lower() == ".jar"
        and not p.name.lower().endswith(".jar.disabled")
    )


def _prefer_modded_version(game_dir: Path, version_dir: Path | None) -> tuple[Path | None, str]:
    """Pick a version-isolated profile that actually contains mods.

    The official launcher stores version-isolated mods under
    ``versions/<profile>/mods``. When the user selects the game root, the
    launcher profile may point at the plain vanilla version while the modded
    Fabric/Forge profile sits next to it. In that case prefer the sibling with
    the same Minecraft release and a non-empty mods folder.
    """
    if version_dir is None:
        return version_dir, ""

    own = version_dir / "mods"
    if _jar_count(own):
        return version_dir, ""
    if _jar_count(game_dir / "mods"):
        # Shared global mods are already usable; don't silently switch profile.
        return version_dir, ""

    selected = _version_dir_info(version_dir)
    mc_version = selected["mc"]
    versions_dir = game_dir / "versions"
    if not mc_version or not versions_dir.is_dir():
        return version_dir, ""

    best: tuple[tuple[Any, ...], Path, int] | None = None
    for candidate in versions_dir.iterdir():
        if (
            not candidate.is_dir()
            or candidate == version_dir
            or not (candidate / f"{candidate.name}.json").is_file()
        ):
            continue
        info = _version_dir_info(candidate)
        count = _jar_count(candidate / "mods")
        if not count or info["mc"] != mc_version:
            continue
        try:
            modified = (candidate / "mods").stat().st_mtime
        except OSError:
            modified = 0.0
        key = (modified, _version_sort_key(str(info["mc"])), count)
        if best is None or key > best[0]:
            best = (key, candidate, count)

    if best is None:
        return version_dir, ""
    _, chosen, count = best
    return chosen, f"已选择带 {count} 个模组的版本 {chosen.name}"


def _version_dir_info(path: Path) -> dict[str, Any]:
    data = _read_json(path / f"{path.name}.json") or {}
    version_id = str(data.get("id") or path.name).strip()
    mc_version = (
        _extract_mc_version(data.get("inheritsFrom"))
        or _extract_mc_version(data.get("jar"))
        or _extract_mc_version(version_id)
        or _extract_mc_version(path.name)
    )
    version_type = str(data.get("type") or "").strip().lower()
    snapshot = version_type == "snapshot" or "snapshot" in version_id.lower()
    # A plain version folder (as created by the official launcher) has no
    # inheritsFrom/jar and is named exactly after its release.
    plain = bool(mc_version) and version_id == mc_version and not data.get("inheritsFrom") and not data.get("jar")
    return {
        "path": path,
        "id": version_id,
        "mc": mc_version,
        "type": version_type,
        "snapshot": snapshot,
        "plain": plain,
    }


def _pick_latest_version(records: list[dict[str, Any]], snapshots: bool = False) -> dict[str, Any] | None:
    pool = [r for r in records if bool(r["snapshot"]) == snapshots and r["mc"]]
    if not pool:
        return None
    # Prefer the highest release number; for equal MC versions prefer the
    # unmodified version folder over launcher-created loader builds.
    return max(pool, key=lambda r: (_version_sort_key(str(r["mc"])), bool(r["plain"])))


def _find_version_dir(game_dir: Path, explicit: Path | None = None) -> tuple[Path | None, str]:
    if explicit and (explicit / f"{explicit.name}.json").is_file():
        return explicit, ""

    versions_dir = game_dir / "versions"
    if not versions_dir.is_dir():
        return None, ""

    version_dirs = [
        p
        for p in versions_dir.iterdir()
        if p.is_dir() and (p / f"{p.name}.json").is_file()
    ]
    if not version_dirs:
        return None, ""

    records = [_version_dir_info(p) for p in version_dirs]
    by_name = {str(r["path"].name): r for r in records}

    # Launcher profiles remember which version was used last. This is the most
    # reliable hint when a .minecraft folder contains many versions.
    profiles = _read_json(game_dir / "launcher_profiles.json") or {}
    preferred_id = ""
    profile_map = profiles.get("profiles")
    if isinstance(profile_map, dict) and profile_map:
        last_used = profiles.get("lastUsed")
        chosen = None
        if isinstance(last_used, str) and isinstance(profile_map.get(last_used), dict):
            chosen = profile_map.get(last_used)
        elif isinstance(profiles.get("selectedProfile"), str):
            chosen = profile_map.get(profiles.get("selectedProfile"))
        if chosen is None:
            def sort_key(item: tuple[str, Any]):
                value = item[1] if isinstance(item[1], dict) else {}
                return str(value.get("lastUsed", ""))

            chosen = sorted(profile_map.items(), key=sort_key, reverse=True)[0][1]
        if isinstance(chosen, dict):
            preferred_id = str(chosen.get("lastVersionId") or "").strip()

    if preferred_id:
        exact = by_name.get(preferred_id)
        if exact is not None:
            return exact["path"], f"已根据启动器配置选择版本 {exact['path'].name}"

        # Modern launchers store aliases such as "latest-release" instead of a
        # concrete version folder. Resolve them against the installed versions.
        alias = preferred_id.lower()
        if alias == "latest-release":
            latest = _pick_latest_version(records, snapshots=False)
            if latest is not None:
                return latest["path"], f"启动器使用 latest-release，已选择最新正式版 {latest['mc']}"
        elif alias == "latest-snapshot":
            latest = _pick_latest_version(records, snapshots=True)
            if latest is not None:
                return latest["path"], f"启动器使用 latest-snapshot，已选择最新快照 {latest['mc']}"

    if len(records) == 1:
        return records[0]["path"], ""

    latest_release = _pick_latest_version(records, snapshots=False)
    if latest_release is not None:
        return (
            latest_release["path"],
            f"检测到 {len(records)} 个版本，已选择最新正式版 {latest_release['mc']}，可手动修改",
        )

    latest_snapshot = _pick_latest_version(records, snapshots=True)
    if latest_snapshot is not None:
        return (
            latest_snapshot["path"],
            f"检测到 {len(records)} 个版本，已选择最新快照 {latest_snapshot['mc']}，可手动修改",
        )

    newest = max(records, key=lambda r: (r["path"] / f"{r['path'].name}.json").stat().st_mtime)
    return newest["path"], f"检测到 {len(records)} 个版本，暂选最近修改的 {newest['path'].name}，可手动修改"


def _strip_placeholders(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("${") and text.endswith("}"):
        return ""
    return text


def _maven_parts(name: str) -> tuple[str, str, str]:
    parts = (name or "").split(":")
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[0], parts[1]
    return name or "", name or "", ""


def _detect_loader_from_version_json(data: dict[str, Any]) -> tuple[str, str]:
    libraries = data.get("libraries")
    if not isinstance(libraries, list):
        libraries = []

    candidates: list[tuple[str, str]] = []
    for lib in libraries:
        if not isinstance(lib, dict):
            continue
        group, artifact, version = _maven_parts(str(lib.get("name", "")))
        ga = f"{group}:{artifact}".lower()
        if not version:
            continue
        if ga == "net.fabricmc:fabric-loader":
            candidates.append(("fabric", version))
        elif ga in {"org.quiltmc:quilt-loader", "org.quiltmc:quilt-loader-base"}:
            candidates.append(("quilt", version))
        elif ga in {"net.minecraftforge:forge", "net.minecraftforge:fmlloader"}:
            loader_version = version
            if "-" in version and version.split("-", 1)[0].count(".") >= 1:
                loader_version = version.split("-", 1)[1]
            candidates.append(("forge", loader_version))
        elif ga in {
            "net.neoforged:neoforge",
            "net.neoforged.fancymodloader:loader",
            "net.neoforged:loader",
        }:
            candidates.append(("neoforge", version))

    priority = {"neoforge": 4, "forge": 3, "quilt": 2, "fabric": 1}
    if candidates:
        candidates.sort(key=lambda item: priority.get(item[0], 0), reverse=True)
        return candidates[0]

    # Some launchers inject the loader via a custom version id.
    version_id = str(data.get("id", "")).lower()
    for loader in ("neoforge", "fabric", "quilt", "forge"):
        if loader in version_id:
            return loader, ""
    return "vanilla", ""


def _detect_from_mmc_pack(path: Path) -> tuple[str, str, str, list[str]]:
    data = _read_json(path) or {}
    components = data.get("components")
    mc_version = ""
    loader = "vanilla"
    loader_version = ""
    notes: list[str] = []
    if isinstance(components, list):
        for comp in components:
            if not isinstance(comp, dict):
                continue
            uid = str(comp.get("uid", "")).lower()
            version = str(comp.get("version", "")).strip()
            if uid == "net.minecraft":
                mc_version = version
            elif uid in {"net.fabricmc.fabric-loader", "net.fabricmc:fabric-loader"}:
                loader, loader_version = "fabric", version
            elif uid in {"org.quiltmc.quilt-loader", "org.quiltmc:quilt-loader"}:
                loader, loader_version = "quilt", version
            elif uid in {"net.minecraftforge", "net.minecraftforge:forge"}:
                loader, loader_version = "forge", version
            elif uid in {"net.neoforged", "net.neoforged:neoforge"}:
                loader, loader_version = "neoforge", version
    if mc_version:
        notes.append("从 Prism/MultiMC 实例配置识别版本")
    return mc_version, loader, loader_version, notes


def _detect_from_curseforge_instance(path: Path) -> tuple[str, str, str, list[str]]:
    data = _read_json(path) or {}
    mc_version = str(
        data.get("gameVersion")
        or (data.get("baseModLoader") or {}).get("minecraftVersion")
        or ""
    ).strip()
    loader = "vanilla"
    loader_version = ""
    base = data.get("baseModLoader")
    if isinstance(base, dict):
        name = str(base.get("name") or "").lower()
        version = str(base.get("version") or base.get("forgeVersion") or "")
        for key in ("neoforge", "fabric", "quilt", "forge"):
            if name.startswith(key) or key in name.split("-")[0]:
                loader = key
                loader_version = version or name.split("-", 1)[-1]
                break
    notes = ["从 CurseForge 启动器实例配置识别版本"] if mc_version else []
    return mc_version, loader, loader_version, notes


def _mc_from_neoforge(version: str) -> str:
    """Best-effort mapping for NeoForge 20.4.x -> 1.20.4."""
    match = re.match(r"^(\d+)\.(\d+)", version or "")
    if not match:
        return ""
    major, minor = match.group(1), match.group(2)
    if major == "20":
        return f"1.20.{minor}"
    if major == "21":
        return f"1.21.{minor}" if minor != "0" else "1.21"
    return ""


def detect_instance(
    raw_path: str | Path,
    override_version: str | None = None,
    override_loader: str | None = None,
) -> Instance:
    if not raw_path:
        raise InstanceError("请选择 Minecraft 实例路径")

    path = Path(str(raw_path).strip().strip('"')).expanduser()
    if not path.exists():
        raise InstanceError(f"路径不存在：{path}")
    if path.is_file():
        path = path.parent
    # Keep the folder the user actually typed for display/saving. Detection may
    # walk up to the game root, but that should not rewrite the path field.
    selected_path = path

    explicit_version_dir = None
    if (path / f"{path.name}.json").is_file() and path.parent.name == "versions":
        explicit_version_dir = path
        path = path.parent.parent

    candidates = _candidate_game_dirs(path)
    if not candidates:
        raise InstanceError(
            "没有找到 Minecraft 实例。请选择包含 mods / versions 的 .minecraft 文件夹，"
            "或 Prism / CurseForge 实例文件夹。"
        )
    game_dir = candidates[0][1]
    notes: list[str] = []

    version_dir, note = _find_version_dir(game_dir, explicit_version_dir)
    if note:
        notes.append(note)
    if explicit_version_dir is None:
        preferred_version, mod_note = _prefer_modded_version(game_dir, version_dir)
        if preferred_version is not None and preferred_version != version_dir:
            version_dir = preferred_version
            notes.insert(0, mod_note)

    mc_version = ""
    loader = "vanilla"
    loader_version = ""
    version_id = ""
    version_json: Path | None = None

    if version_dir:
        version_json = version_dir / f"{version_dir.name}.json"
        data = _read_json(version_json) or {}
        version_id = str(data.get("id") or version_dir.name)
        inherited = data.get("inheritsFrom")
        jar = data.get("jar")
        for candidate in (inherited, jar, version_id, version_dir.name):
            extracted = _extract_mc_version(candidate)
            if extracted:
                mc_version = extracted
                break
        loader, loader_version = _detect_loader_from_version_json(data)
        if loader == "neoforge" and not mc_version:
            mc_version = _mc_from_neoforge(loader_version)
        notes.append(f"已读取版本文件 {version_json.name}")

    mmc = game_dir / "mmc-pack.json"
    if mmc.is_file() and not mc_version:
        mmc_mc, mmc_loader, mmc_ver, mmc_notes = _detect_from_mmc_pack(mmc)
        mc_version = mc_version or mmc_mc
        if loader == "vanilla":
            loader, loader_version = mmc_loader, mmc_ver
        notes.extend(mmc_notes)

    cf_instance = game_dir / "minecraftinstance.json"
    if cf_instance.is_file() and not mc_version:
        cf_mc, cf_loader, cf_ver, cf_notes = _detect_from_curseforge_instance(cf_instance)
        mc_version = mc_version or cf_mc
        if loader == "vanilla":
            loader, loader_version = cf_loader, cf_ver
        notes.extend(cf_notes)

    if override_version and override_version.strip():
        mc_version = override_version.strip()
        notes.append(f"已手动指定 Minecraft {mc_version}")
    if override_loader:
        chosen = override_loader.strip().lower()
        if chosen in LOADER_NAMES:
            loader = chosen
            notes.append(f"已手动指定加载器 {LOADER_NAMES[chosen]}")

    if not mc_version:
        raise InstanceError(
            "已找到实例文件夹，但无法识别 Minecraft 版本。请在实例卡片中点击“手动设置”。"
        )

    mods_dir = game_dir / "mods"
    if version_dir is not None:
        version_mods = version_dir / "mods"
        if version_mods.is_dir() and (_jar_count(version_mods) > 0 or not mods_dir.is_dir()):
            mods_dir = version_mods
    if not mods_dir.is_dir():
        notes.append("mods 文件夹不存在（迁移时会自动创建）")

    return Instance(
        path=selected_path,
        game_dir=game_dir,
        mods_dir=mods_dir,
        mc_version=mc_version,
        loader=loader or "vanilla",
        loader_version=loader_version,
        version_id=version_id,
        version_json=version_json,
        notes=notes,
    )


def list_local_versions(game_dir: Path) -> list[str]:
    versions_dir = Path(game_dir) / "versions"
    if not versions_dir.is_dir():
        return []
    names = []
    for p in versions_dir.iterdir():
        if p.is_dir() and (p / f"{p.name}.json").is_file():
            data = _read_json(p / f"{p.name}.json") or {}
            inherited = data.get("inheritsFrom")
            name = _extract_mc_version(inherited) or _extract_mc_version(p.name)
            if name:
                names.append(name)
    return sorted(set(names), key=_version_sort_key)


COMMON_MC_VERSIONS = [
    "26.3", "26.2", "26.1.2", "26.1.1", "26.1",
    "1.21.11", "1.21.10", "1.21.9", "1.21.8", "1.21.7", "1.21.6", "1.21.5",
    "1.21.4", "1.21.3", "1.21.1", "1.21", "1.20.6", "1.20.5", "1.20.4",
    "1.20.3", "1.20.2", "1.20.1", "1.20", "1.19.4", "1.19.3", "1.19.2",
    "1.19.1", "1.19", "1.18.2", "1.18.1", "1.18", "1.17.1", "1.16.5",
    "1.16.4", "1.15.2", "1.14.4", "1.12.2", "1.8.9", "1.7.10",
]


def _version_sort_key(value: str):
    parts = []
    for piece in re.split(r"[.\-+]", value):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(piece)
    return tuple((0, p) if isinstance(p, int) else (1, str(p)) for p in parts)
