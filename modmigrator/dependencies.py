from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from .migrate import download_plans
from .models import Instance, ModMeta, ModPlan, Project, TargetFile
from .modjar import read_mods_folder
from .providers.http import HttpClient
from .providers.modrinth import ModrinthProvider

LOGGER = logging.getLogger("modmigrator.dependencies")
ProgressFn = Callable[[str, int], None]


def _required(dependencies) -> list[dict]:
    result = []
    if not isinstance(dependencies, list):
        return result
    for dep in dependencies:
        if isinstance(dep, dict) and str(dep.get("dependency_type") or "") == "required":
            result.append(dep)
    return result


def _installed_and_required(
    target: Instance, client: HttpClient, modrinth: ModrinthProvider
) -> tuple[set[str], list[dict]]:
    metas = read_mods_folder(target.mods_dir, preferred_loader=target.loader or None)
    hashes = [m.sha1 for m in metas if m.sha1]
    if not hashes:
        return set(), []
    hits = modrinth.lookup_hashes(hashes)
    installed: set[str] = set()
    required: list[dict] = []
    for version in hits.values():
        if not isinstance(version, dict):
            continue
        project_id = str(version.get("project_id") or "")
        if project_id:
            installed.add(project_id)
        required.extend(_required(version.get("dependencies")))
    return installed, required


def _dependency_plan(
    project: Project,
    target_file: TargetFile,
    target: Instance,
    reason: str,
) -> ModPlan:
    meta = ModMeta(
        path=Path(target_file.name),
        name=project.title,
        version=target_file.version_label,
        mod_id=project.slug,
        loader=target.loader,
        kind="mod",
        is_mod=True,
        metadata_source="前置依赖",
    )
    return ModPlan(
        meta=meta,
        status="ready",
        project=project,
        target=target_file,
        source_platform="modrinth",
        reason=reason,
    )


def resolve_missing_dependencies(
    target: Instance,
    *,
    client: HttpClient | None = None,
    overwrite: bool = False,
    progress: ProgressFn | None = None,
    cancel_event=None,
) -> list[ModPlan]:
    """Scan target mods for required Modrinth dependencies and download missing ones.

    Returns plans for newly added/changed dependencies. Required dependencies that cannot
    be resolved are reported with status ``dependency_missing``.
    """
    client = client or HttpClient()
    report = progress or (lambda _message, _percent: None)
    if not target.mc_version:
        return []

    report("检查已迁移模组的必需前置…", 20)
    modrinth = ModrinthProvider(client)
    try:
        installed, required = _installed_and_required(target, client, modrinth)
    except Exception as exc:
        LOGGER.warning("前置扫描失败：%s", exc)
        return []

    if not required:
        report("没有发现缺失的必需前置", 100)
        return []

    queue = list(required)
    seen: set[str] = set()
    plans: list[ModPlan] = []
    while queue:
        if cancel_event is not None and cancel_event.is_set():
            break
        dep = queue.pop(0)
        project_id = str(dep.get("project_id") or "")
        if not project_id:
            version_id = str(dep.get("version_id") or "")
            version = modrinth.get_version(version_id) if version_id else None
            if isinstance(version, dict):
                project_id = str(version.get("project_id") or "")
        if not project_id or project_id in installed or project_id in seen:
            continue
        seen.add(project_id)

        project = modrinth.get_project(project_id)
        if project is None:
            meta = ModMeta(
                path=Path(f"{project_id}.jar"),
                name=project_id,
                mod_id=project_id,
                loader=target.loader,
                kind="mod",
                is_mod=True,
                metadata_source="前置依赖",
            )
            plans.append(
                ModPlan(
                    meta=meta,
                    status="dependency_missing",
                    reason="无法获取前置项目资料",
                )
            )
            continue

        reference = str(dep.get("file_name") or project.title)
        try:
            target_file = modrinth.target_file(
                project_id,
                target.mc_version,
                target.loader,
                project_type="mod",
            )
        except Exception as exc:
            LOGGER.warning("前置版本查询失败（%s）：%s", project_id, exc)
            target_file = None

        if target_file is None:
            meta = ModMeta(
                path=Path(f"{project.slug or project_id}.jar"),
                name=project.title,
                mod_id=project.slug,
                loader=target.loader,
                kind="mod",
                is_mod=True,
                metadata_source="前置依赖",
            )
            plans.append(
                ModPlan(
                    meta=meta,
                    status="dependency_missing",
                    project=project,
                    reason=(
                        f"缺少前置 {project.title}，但 Modrinth 上没有适用于 "
                        f"Minecraft {target.mc_version} · {target.loader_label} 的版本"
                    ),
                )
            )
            continue

        plan = _dependency_plan(
            project,
            target_file,
            target,
            f"自动补全缺少的前置：{project.title}（{reference}）",
        )
        plans.append(plan)
        installed.add(project_id)
        queue.extend(_required(target_file.dependencies))
        report(f"找到缺失前置：{project.title}", 60)

    download_plans(
        [p for p in plans if p.status == "ready"],
        target,
        overwrite=overwrite,
        cancel_event=cancel_event,
    )
    report("前置检查完成", 100)
    return plans
