from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from .config import AppConfig
from .dependencies import resolve_missing_dependencies
from .matcher import scan_instances, summarize
from .mcinstance import InstanceError, detect_instance
from .migrate import download_plans
from .models import STATUS_LABELS, ModPlan
from .util import APP_DISPLAY_NAME, APP_VERSION, elide_middle, human_size, open_local_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="modmigrator",
        description=f"{APP_DISPLAY_NAME} {APP_VERSION}：把模组、资源包和光影迁移到另一个 Minecraft 版本",
    )
    parser.add_argument("--source", required=True, help="源实例路径（.minecraft、versions/版本 或启动器实例目录）")
    parser.add_argument("--target", required=True, help="目标实例路径")
    parser.add_argument("--download", action="store_true", help="匹配完成后下载/复制可迁移项目，并自动补全前置")
    parser.add_argument("--yes", action="store_true", help="跳过确认，自动跳过无法迁移的项目")
    parser.add_argument("--overwrite", action="store_true", help="覆盖目标文件夹中的同名文件")
    parser.add_argument("--no-cfwidget", action="store_true", help="禁用 CurseForge 公共数据源（只用官方 API）")
    parser.add_argument("--cf-key", default="", help="CurseForge 官方 API Key（也可写入设置或环境变量）")
    parser.add_argument("--workers", type=int, default=4, help="并发查询数（默认 4）")
    parser.add_argument("--csv", default="", help="把结果写入 CSV 文件")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    parser.add_argument("--open", action="store_true", help="下载完成后打开目标实例文件夹")
    parser.add_argument("--version", action="version", version=APP_VERSION)
    return parser


def _status_line(plan: ModPlan, index: int) -> str:
    if plan.status == "copy":
        target = "复制原文件"
    elif plan.target:
        target = f"{plan.target.platform_label} {plan.target.version_label}".strip()
    else:
        target = ""
    source = plan.project.title if plan.project else plan.meta.display_name
    return (
        f"{index:>3}. {plan.kind_label:<6} "
        f"{elide_middle(source, 30):<30} "
        f"{elide_middle(plan.meta.version or '—', 14):<14} "
        f"{plan.status_label:<8} "
        f"{elide_middle(target, 34)}"
    )


def _print_plans(plans: list[ModPlan]) -> None:
    print(f"{'':>4} {'类型':<6} {'名称':<30} {'源版本':<14} {'状态':<8} 目标版本")
    print("-" * 118)
    for i, plan in enumerate(plans, 1):
        print(_status_line(plan, i))
    counts = summarize(plans)
    parts = [f"{STATUS_LABELS.get(k, k)} {v}" for k, v in counts.items()]
    print("-" * 118)
    print("汇总：" + " · ".join(parts))


def _write_csv(plans: list[ModPlan], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "源文件",
                "类型",
                "模组 ID",
                "项目名称",
                "源版本",
                "源加载器",
                "识别平台",
                "项目名称",
                "项目链接",
                "目标平台",
                "目标版本",
                "目标文件",
                "目标下载链接",
                "状态",
                "说明",
                "已下载路径",
            ]
        )
        for plan in plans:
            writer.writerow(
                [
                    plan.meta.filename,
                    plan.kind_label,
                    plan.meta.mod_id,
                    plan.meta.name,
                    plan.meta.version,
                    plan.meta.loader,
                    plan.source_label,
                    plan.project.title if plan.project else "",
                    plan.project.url if plan.project else "",
                    plan.target.platform_label if plan.target else "",
                    plan.target.version_label if plan.target else "",
                    plan.target.name if plan.target else "",
                    plan.target.url if plan.target else "",
                    plan.status_label,
                    plan.reason,
                    str(plan.downloaded_path or ""),
                ]
            )


def _json_payload(plans: list[ModPlan], source, target) -> dict:
    return {
        "app": APP_DISPLAY_NAME,
        "version": APP_VERSION,
        "source": {
            "path": str(source.path),
            "mc_version": source.mc_version,
            "loader": source.loader,
            "loader_version": source.loader_version,
            "mods_dir": str(source.mods_dir),
            "resourcepacks_dir": str(source.resourcepacks_dir),
            "shaderpacks_dir": str(source.shaderpacks_dir),
            "mod_count": source.mod_count,
            "resourcepack_count": source.resourcepack_count,
            "shader_count": source.shader_count,
        },
        "target": {
            "path": str(target.path),
            "mc_version": target.mc_version,
            "loader": target.loader,
            "loader_version": target.loader_version,
            "mods_dir": str(target.mods_dir),
            "resourcepacks_dir": str(target.resourcepacks_dir),
            "shaderpacks_dir": str(target.shaderpacks_dir),
            "mod_count": target.mod_count,
            "resourcepack_count": target.resourcepack_count,
            "shader_count": target.shader_count,
        },
        "summary": summarize(plans),
        "mods": [
            {
                "file": plan.meta.filename,
                "kind": plan.kind,
                "kind_label": plan.kind_label,
                "mod_id": plan.meta.mod_id,
                "name": plan.meta.name,
                "version": plan.meta.version,
                "project": {
                    "platform": plan.project.platform,
                    "title": plan.project.title,
                    "url": plan.project.url,
                    "match_method": plan.project.match_method,
                    "confidence": plan.project.confidence,
                }
                if plan.project
                else None,
                "alternate_projects": [
                    {"platform": p.platform, "title": p.title, "url": p.url}
                    for p in plan.alt_projects
                ],
                "target": {
                    "platform": plan.target.platform,
                    "version": plan.target.version_label,
                    "file": plan.target.name,
                    "url": plan.target.url,
                    "size": plan.target.size,
                    "size_label": human_size(plan.target.size),
                }
                if plan.target
                else None,
                "status": plan.status,
                "status_label": plan.status_label,
                "reason": plan.reason,
                "downloaded_path": str(plan.downloaded_path or ""),
            }
            for plan in plans
        ],
    }


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = AppConfig.load()
    key = args.cf_key or config.curseforge_api_key
    overwrite = args.overwrite or config.overwrite
    use_cfwidget = (not args.no_cfwidget) and config.use_cfwidget

    try:
        source = detect_instance(args.source)
        target = detect_instance(args.target)
    except InstanceError as exc:
        print(f"无法识别实例：{exc}", file=sys.stderr)
        return 2

    if args.json:
        # status messages go to stderr so stdout stays machine-readable
        def progress(message: str, percent: int) -> None:
            print(f"[{percent:3d}%] {message}", file=sys.stderr)
    else:
        print(f"{APP_DISPLAY_NAME} {APP_VERSION}")
        print(f"源实例  {source.path}")
        print(
            f"        {source.display} · {source.mod_count} 个模组 · "
            f"{source.resourcepack_count} 个资源包 · {source.shader_count} 个光影"
        )
        print(f"目标实例 {target.path}")
        print(
            f"        {target.display} · {target.mod_count} 个模组 · "
            f"{target.resourcepack_count} 个资源包 · {target.shader_count} 个光影"
        )
        if not source.notes and not target.notes:
            print()
        for note in source.notes + target.notes:
            print(f"提示：{note}")
        print()

        def progress(message: str, percent: int) -> None:
            print(f"[{percent:3d}%] {message}")

    plans = scan_instances(
        source,
        target,
        curseforge_api_key=key,
        use_cfwidget=use_cfwidget,
        overwrite=overwrite,
        progress=progress,
        workers=max(1, min(args.workers, 12)),
    )

    if not args.json:
        print()
        _print_plans(plans)

    blocked = [
        p
        for p in plans
        if p.status not in ("ready", "copy", "already", "downloaded", "copied")
    ]
    if args.download:
        if blocked:
            print(
                f"\n有 {len(blocked)} 个项目无法迁移："
                + "、".join(p.meta.display_name for p in blocked[:5])
                + ("…" if len(blocked) > 5 else "")
            )
            if not args.yes:
                if not sys.stdin.isatty():
                    print("非交互环境，请加 --yes 以继续并跳过这些项目。", file=sys.stderr)
                    return 3
                answer = input("仍要继续并跳过这些项目吗？[y/N] ").strip().lower()
                if answer not in ("y", "yes"):
                    print("已取消下载。")
                    return 0
        ready = [p for p in plans if p.status in ("ready", "copy")]
        if not ready:
            print("没有需要下载或复制的项目。")
        else:
            print(f"\n开始迁移 {len(ready)} 个项目到 {target.path} …")

            def download_progress(plan: ModPlan, done: int, total: int) -> None:
                if total:
                    print(
                        f"  ↓ {elide_middle(plan.meta.display_name, 32)} "
                        f"{done / 1024 / 1024:5.1f}/{total / 1024 / 1024:5.1f} MB",
                        end="\r",
                    )

            download_plans(
                plans,
                target,
                overwrite=overwrite,
                progress=download_progress,
            )
            print()
            print("正在检查并自动补全模组前置…")
            deps = resolve_missing_dependencies(target, overwrite=overwrite)
            if deps:
                plans.extend(deps)
            _print_plans(plans)
            if args.open:
                open_local_path(target.game_dir)

    if args.csv:
        _write_csv(plans, Path(args.csv))
        if not args.json:
            print(f"\n报告已写入：{Path(args.csv).resolve()}")

    if args.json:
        json.dump(_json_payload(plans, source, target), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")

    counts = summarize(plans)
    if args.download:
        return 0 if not counts.get("download_failed") else 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
