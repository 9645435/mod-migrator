from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


LOADER_LABELS = {
    "fabric": "Fabric",
    "quilt": "Quilt",
    "forge": "Forge",
    "neoforge": "NeoForge",
    "iris": "Iris",
    "optifine": "OptiFine",
    "minecraft": "Minecraft",
    "vanilla": "原版",
    "unknown": "未知",
}

PLATFORM_LABELS = {
    "modrinth": "Modrinth",
    "curseforge": "CurseForge",
}

KIND_LABELS = {
    "mod": "模组",
    "resourcepack": "资源包",
    "shader": "光影",
}

STATUS_LABELS = {
    "ready": "可迁移",
    "already": "已存在",
    "copy": "可复制",
    "copied": "已复制",
    "no_target": "无目标版本",
    "not_found": "未找到模组",
    "unsupported": "跳过",
    "error": "查询失败",
    "downloaded": "已下载",
    "download_failed": "下载失败",
    "dependency_missing": "缺少前置",
    "cancelled": "已取消",
}

STATUS_ORDER = {
    "ready": 0,
    "copy": 1,
    "downloaded": 2,
    "copied": 3,
    "already": 4,
    "no_target": 5,
    "not_found": 6,
    "dependency_missing": 7,
    "error": 8,
    "download_failed": 9,
    "unsupported": 10,
    "cancelled": 11,
}


def _count_zip_files(folder: Path) -> int:
    if not folder.is_dir():
        return 0
    return sum(1 for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".zip")


@dataclass(slots=True)
class Instance:
    """A Minecraft Java game directory / instance."""

    path: Path
    game_dir: Path
    mods_dir: Path
    mc_version: str
    loader: str = "vanilla"
    loader_version: str = ""
    version_id: str = ""
    version_json: Path | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def loader_label(self) -> str:
        return LOADER_LABELS.get(self.loader, self.loader or "未知")

    @property
    def display(self) -> str:
        bits = [f"Minecraft {self.mc_version or '?'}"]
        if self.loader and self.loader != "vanilla":
            bits.append(f"{self.loader_label} {self.loader_version}".strip())
        else:
            bits.append("原版")
        return " · ".join(bits)

    @property
    def mod_count(self) -> int:
        if not self.mods_dir.is_dir():
            return 0
        return sum(1 for p in self.mods_dir.iterdir() if p.is_file() and p.suffix.lower() == ".jar")

    @property
    def resourcepacks_dir(self) -> Path:
        for base in (self.path, self.game_dir):
            folder = base / "resourcepacks"
            if folder.is_dir():
                return folder
        return self.game_dir / "resourcepacks"

    @property
    def shaderpacks_dir(self) -> Path:
        for base in (self.path, self.game_dir):
            folder = base / "shaderpacks"
            if folder.is_dir():
                return folder
        return self.game_dir / "shaderpacks"

    @property
    def resourcepack_count(self) -> int:
        return _count_zip_files(self.resourcepacks_dir)

    @property
    def shader_count(self) -> int:
        return _count_zip_files(self.shaderpacks_dir)


@dataclass(slots=True)
class ModMeta:
    path: Path
    sha1: str = ""
    sha512: str = ""
    mod_id: str = ""
    name: str = ""
    version: str = ""
    loader: str = ""
    mc_constraint: str = ""
    description: str = ""
    authors: list[str] = field(default_factory=list)
    license: str = ""
    environment: str = ""
    metadata_source: str = ""
    is_mod: bool = False
    kind: str = "mod"
    error: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def display_name(self) -> str:
        return self.name or self.mod_id or self.path.stem

    @property
    def display_version(self) -> str:
        return self.version or "—"


@dataclass(slots=True)
class Project:
    platform: str
    project_id: str
    slug: str
    title: str
    url: str = ""
    description: str = ""
    icon_url: str = ""
    match_method: str = ""
    confidence: float = 1.0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def platform_label(self) -> str:
        return PLATFORM_LABELS.get(self.platform, self.platform)


@dataclass(slots=True)
class TargetFile:
    platform: str
    project_id: str
    file_id: str
    name: str
    url: str
    version_label: str = ""
    mc_versions: list[str] = field(default_factory=list)
    loaders: list[str] = field(default_factory=list)
    release_type: str = ""
    size: int = 0
    published: str = ""
    sha1: str = ""
    dependencies: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def platform_label(self) -> str:
        return PLATFORM_LABELS.get(self.platform, self.platform)

    @property
    def size_label(self) -> str:
        if not self.size:
            return "未知大小"
        value = float(self.size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} GB"


@dataclass(slots=True)
class ModPlan:
    meta: ModMeta
    status: str
    project: Project | None = None
    target: TargetFile | None = None
    alt_projects: list[Project] = field(default_factory=list)
    reason: str = ""
    existing: str = ""
    source_platform: str = ""
    downloaded_path: Path | None = None
    progress: float = 0.0

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def kind(self) -> str:
        return self.meta.kind

    @property
    def kind_label(self) -> str:
        return self.meta.kind_label

    @property
    def display_name(self) -> str:
        if self.project and self.project.title:
            return self.meta.display_name or self.project.title
        return self.meta.display_name

    @property
    def source_label(self) -> str:
        platforms = []
        if self.project:
            platforms.append(self.project.platform_label)
        for p in self.alt_projects:
            if p.platform_label not in platforms:
                platforms.append(p.platform_label)
        if platforms:
            return " + ".join(platforms)
        return "—"

    @property
    def action_needed(self) -> bool:
        return self.status in ("ready", "copy")
