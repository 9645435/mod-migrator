from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .models import Instance, ModMeta, ModPlan, Project, TargetFile
from .providers.curseforge import CurseForgeProvider
from .providers.http import HttpClient
from .providers.modrinth import ModrinthProvider
from .modjar import read_mods_folder
from .packs import pack_folder, read_packs_folder
from .util import norm_key

LOGGER = logging.getLogger("modmigrator.matcher")
ProgressFn = Callable[[str, int], None]


@dataclass(slots=True)
class _TargetIndex:
    by_sha1: dict[str, ModMeta]
    by_name: dict[str, ModMeta]
    by_mod_id: dict[str, ModMeta]

    @classmethod
    def build(cls, metas: list[ModMeta]) -> "_TargetIndex":
        by_sha1: dict[str, ModMeta] = {}
        by_name: dict[str, ModMeta] = {}
        by_mod_id: dict[str, ModMeta] = {}
        for meta in metas:
            if meta.sha1:
                by_sha1.setdefault(meta.sha1.lower(), meta)
            by_name.setdefault(meta.filename.casefold(), meta)
            if meta.mod_id:
                by_mod_id.setdefault(norm_key(meta.mod_id), meta)
        return cls(by_sha1, by_name, by_mod_id)

    def exact(self, target: TargetFile) -> ModMeta | None:
        if target.sha1:
            hit = self.by_sha1.get(target.sha1.lower())
            if hit:
                return hit
        return self.by_name.get(target.name.casefold())


def scan_instances(
    source: Instance,
    target: Instance,
    *,
    client: HttpClient | None = None,
    curseforge_api_key: str = "",
    use_cfwidget: bool = True,
    overwrite: bool = False,
    progress: ProgressFn | None = None,
    cancel_event=None,
    workers: int = 4,
) -> list[ModPlan]:
    """Build a migration plan for mods, resource packs and shaders in the source instance."""
    client = client or HttpClient()
    report = progress or (lambda _message, _percent: None)
    report("读取源实例模组文件夹…", 2)
    source_metas = read_mods_folder(source.mods_dir, preferred_loader=source.loader or None)
    report("读取目标实例已安装内容…", 5)
    target_metas = read_mods_folder(target.mods_dir, preferred_loader=target.loader or None)
    indexes: dict[str, _TargetIndex] = {"mod": _TargetIndex.build(target_metas)}

    pack_metas: dict[str, list[ModMeta]] = {}
    for kind in ("resourcepack", "shader"):
        folder = pack_folder(source, kind)
        target_folder = pack_folder(target, kind)
        source_packs = read_packs_folder(folder, kind)
        target_packs = read_packs_folder(target_folder, kind)
        pack_metas[kind] = source_packs
        indexes[kind] = _TargetIndex.build(target_packs)

    plans: list[ModPlan] = []
    for meta in source_metas:
        if not meta.is_mod:
            reason = meta.error or "未识别为模组（可能是资源包或整合包）"
            plans.append(ModPlan(meta=meta, status="unsupported", reason=reason))
        else:
            plans.append(ModPlan(meta=meta, status="ready"))

    for kind, metas in pack_metas.items():
        for meta in metas:
            if meta.error:
                plans.append(
                    ModPlan(meta=meta, status="unsupported", reason=meta.error)
                )
            else:
                plans.append(ModPlan(meta=meta, status="ready"))

    if not plans:
        report("源实例中没有可迁移的内容", 100)
        return plans

    # Only plans that passed local validation should be queried. Invalid packs are
    # marked unsupported above and must never be turned into a "copy" fallback.
    runnable = [p for p in plans if p.status == "ready"]
    if cancel_event is not None and cancel_event.is_set():
        _cancel_plans(plans)
        return plans

    report("连接 Modrinth，校验项目文件哈希…", 10)
    modrinth = ModrinthProvider(client)
    hashes = [p.meta.sha1 for p in runnable if p.meta.sha1]
    hash_hits: dict[str, dict] = {}
    try:
        hash_hits = modrinth.lookup_hashes(hashes)
    except Exception as exc:  # lookup_hashes already tolerates errors, this is a guard
        LOGGER.warning("哈希查询失败：%s", exc)

    project_ids = [
        str(hit.get("project_id") or "")
        for hit in hash_hits.values()
        if isinstance(hit, dict)
    ]
    projects: dict[str, Project] = {}
    if project_ids:
        report("获取 Modrinth 项目资料…", 14)
        try:
            projects = modrinth.get_projects(project_ids)
        except Exception as exc:
            LOGGER.warning("项目资料查询失败：%s", exc)

    curseforge = CurseForgeProvider(
        client, curseforge_api_key if use_cfwidget or curseforge_api_key else ""
    )
    if not use_cfwidget and not curseforge_api_key:
        curseforge.official_disabled = True

    report(
        f"在 Modrinth / CurseForge 上匹配 {len(runnable)} 个内容"
        f"（目标：Minecraft {target.mc_version} · {target.loader_label}）…",
        16,
    )

    total = len(runnable)
    completed = 0
    cancelled = False
    executor = ThreadPoolExecutor(max_workers=max(1, min(workers, total)))
    futures: dict[Future, ModPlan] = {}
    try:
        for plan in runnable:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            hit = hash_hits.get(plan.meta.sha1.lower()) if plan.meta.sha1 else None
            project = projects.get(str(hit.get("project_id") or "")) if hit else None
            futures[
                executor.submit(
                    _resolve_one,
                    plan,
                    target,
                    modrinth,
                    curseforge,
                    hit,
                    project,
                )
            ] = plan

        for future in as_completed(futures):
            plan = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # defensive: a bad jar must not kill the scan
                LOGGER.exception("匹配模组失败：%s", plan.meta.path)
                plan.status = "error"
                plan.reason = f"查询失败：{exc}"
                result = plan
            _apply_existing(result, indexes.get(result.kind, indexes["mod"]))
            completed += 1
            percent = 16 + int(78 * completed / max(1, total))
            report(f"已匹配 {completed}/{total}：{result.meta.display_name}", percent)
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
    finally:
        if cancelled:
            for future, plan in futures.items():
                if not future.done():
                    future.cancel()
                    plan.status = "cancelled"
                    plan.reason = "操作已取消"
            executor.shutdown(wait=False, cancel_futures=True)
        else:
            executor.shutdown(wait=True)

    if cancelled:
        for plan in plans:
            if plan.status in ("ready", "copy") and plan.target is None:
                plan.status = "cancelled"
                plan.reason = "操作已取消"
        report("已取消", 100)
    else:
        report("匹配完成", 100)
    return plans


def _resolve_one(
    plan: ModPlan,
    target: Instance,
    modrinth: ModrinthProvider,
    curseforge: CurseForgeProvider,
    hash_hit: dict | None,
    hash_project: Project | None,
) -> ModPlan:
    meta = plan.meta
    kind = meta.kind if meta.kind in ("resourcepack", "shader") else "mod"
    project_type = kind
    mc_version = target.mc_version
    loader = target.loader if kind == "mod" else ""
    alt: list[Project] = []
    errors: list[str] = []

    project: Project | None = None
    target_file: TargetFile | None = None

    def accept(candidate: Project, found: TargetFile) -> ModPlan:
        plan.project = candidate
        plan.target = found
        plan.status = "ready"
        plan.source_platform = candidate.platform
        return plan

    if hash_hit:
        project_id = str(hash_hit.get("project_id") or "")
        project = hash_project
        if project is None and project_id:
            project = modrinth.get_project(project_id)
        if project:
            project.match_method = "文件哈希"
            project.confidence = 1.0
            target_file = modrinth.target_file(
                project.project_id, mc_version, loader, project_type=project_type
            )
            if target_file:
                return accept(project, target_file)
            alt.append(project)

    if not hash_hit or project is None:
        project = modrinth.find_project(meta, project_type=project_type)
        if project:
            target_file = modrinth.target_file(
                project.project_id, mc_version, loader, project_type=project_type
            )
            if target_file:
                return accept(project, target_file)
            alt.append(project)

    cf_project: Project | None = None
    try:
        cf_project = curseforge.find_project(
            meta,
            project if project and project.platform == "modrinth" else None,
            project_type=project_type,
        )
    except Exception as exc:
        errors.append(f"CurseForge 查询失败：{exc}")

    if cf_project:
        try:
            target_file = curseforge.target_file(
                cf_project.project_id,
                mc_version,
                loader,
                project_type=project_type,
            )
        except Exception as exc:
            errors.append(f"CurseForge 版本查询失败：{exc}")
            target_file = None
        if target_file:
            plan.alt_projects = [p for p in alt if p.platform != cf_project.platform]
            return accept(cf_project, target_file)
        alt.append(cf_project)

    # Deduplicate alternate projects by platform / id.
    deduped: list[Project] = []
    seen: set[tuple[str, str]] = set()
    for item in alt:
        key = (item.platform, item.project_id)
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    if project:
        deduped = [p for p in deduped if p.platform != project.platform]
    plan.alt_projects = deduped

    if kind != "mod":
        plan.project = project or cf_project
        plan.status = "copy"
        if plan.project:
            plan.reason = (
                f"没有找到适用于 Minecraft {mc_version} 的新版本，"
                "将直接复制源文件到目标实例"
            )
        else:
            plan.reason = (
                "没有在 Modrinth / CurseForge 匹配到该项目，"
                "将直接复制源文件到目标实例"
            )
        if errors:
            plan.reason += f"；{errors[0]}"
        return plan

    if project or cf_project:
        plan.project = project or cf_project
        plan.status = "no_target"
        platforms = " / ".join(
            dict.fromkeys(
                p.platform_label
                for p in ([project] if project else [])
                + ([cf_project] if cf_project else [])
            )
        )
        plan.reason = (
            f"{platforms} 上没有找到适用于 Minecraft {mc_version} · "
            f"{target.loader_label} 的版本"
        )
        if errors:
            plan.reason += f"；{errors[0]}"
    else:
        plan.status = "not_found"
        plan.reason = "Modrinth 和 CurseForge 上都没有匹配到该模组"
        if errors:
            plan.reason += f"；{errors[0]}"
    return plan


def _apply_existing(plan: ModPlan, index: _TargetIndex) -> None:
    if plan.status not in ("ready", "copy"):
        return
    if plan.status == "copy":
        existing = index.by_name.get(plan.meta.filename.casefold())
        if existing is None and plan.meta.mod_id:
            existing = index.by_mod_id.get(norm_key(plan.meta.mod_id))
        if existing is not None:
            plan.status = "already"
            plan.existing = existing.filename
            plan.reason = "目标实例中已存在相同内容，无需复制"
        return
    if plan.target is None:
        return
    target_file = plan.target
    exact = index.exact(target_file)
    if exact is not None:
        plan.status = "already"
        plan.existing = exact.filename
        plan.reason = "目标实例中已存在完全相同的文件，无需重复下载"
        return
    if plan.meta.mod_id:
        same_id = index.by_mod_id.get(norm_key(plan.meta.mod_id))
        if same_id is not None:
            plan.status = "already"
            plan.existing = same_id.filename
            version = same_id.version or "未知版本"
            plan.reason = (
                f"目标实例已安装同一内容（{version}），为避免重复已跳过"
            )


def _cancel_plans(plans: list[ModPlan]) -> None:
    for plan in plans:
        if plan.status in ("ready", "copy"):
            plan.status = "cancelled"
            plan.reason = "操作已取消"


def summarize(plans: list[ModPlan]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for plan in plans:
        counts[plan.status] = counts.get(plan.status, 0) + 1
    return counts


def target_path_for(target: Instance, plan: ModPlan) -> Path:
    name = plan.target.name if plan.target and plan.target.name else plan.meta.filename
    from .util import safe_filename

    if plan.kind in ("resourcepack", "shader"):
        folder = pack_folder(target, plan.kind)
    else:
        folder = target.mods_dir
    return Path(folder) / safe_filename(name, fallback=plan.meta.filename)
