from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Callable

from .matcher import target_path_for
from .models import Instance, ModPlan
from .providers.http import (
    CancelledError,
    DownloadError,
    HttpClient,
    download_file,
)

LOGGER = logging.getLogger("modmigrator.migrate")
DownloadProgressFn = Callable[[ModPlan, int, int], None]


def download_plans(
    plans: list[ModPlan],
    target: Instance,
    *,
    overwrite: bool = False,
    client: HttpClient | None = None,
    progress: DownloadProgressFn | None = None,
    cancel_event=None,
) -> list[ModPlan]:
    """Download ready mods/packs and copy resource packs/shaders without a target build."""
    del client  # download_file uses urllib directly; kept for API symmetry
    pending = [p for p in plans if p.status in ("ready", "copy")]
    if not pending:
        return plans
    for plan in pending:
        if cancel_event is not None and cancel_event.is_set():
            plan.status = "cancelled"
            plan.reason = "下载已取消"
            continue
        destination = target_path_for(target, plan)
        plan.downloaded_path = destination
        plan.progress = 0.0
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() and not overwrite:
                plan.status = "already"
                plan.existing = destination.name
                plan.reason = "目标文件夹中已有同名文件，已跳过（可在设置中开启覆盖）"
                continue

            if plan.status == "copy" or plan.target is None:
                source = Path(plan.meta.path)
                if not source.is_file():
                    raise OSError("源文件不存在")
                if progress:
                    progress(plan, 0, source.stat().st_size)
                shutil.copy2(source, destination)
                plan.status = "copied"
                plan.progress = 1.0
                plan.reason = "没有目标版本，已复制源文件"
                continue

            if progress:
                progress(plan, 0, plan.target.size)

            def _progress(done: int, total: int) -> None:
                expected = plan.target.size if plan.target else 0
                plan.progress = (done / total) if total else 0.0
                if progress:
                    progress(plan, done, total or expected)

            size = download_file(
                plan.target.url,
                destination,
                expected_size=plan.target.size if plan.target else 0,
                sha1=plan.target.sha1 if plan.target else "",
                progress=_progress,
                cancel_event=cancel_event,
                timeout=90.0,
            )
            plan.status = "downloaded"
            plan.progress = 1.0
            plan.reason = f"已下载 {size / 1024 / 1024:.1f} MB"
        except CancelledError:
            plan.status = "cancelled"
            plan.reason = "下载已取消"
            plan.downloaded_path = None
            break
        except DownloadError as exc:
            plan.status = "download_failed"
            plan.reason = str(exc)
            plan.downloaded_path = None
        except OSError as exc:
            plan.status = "download_failed"
            plan.reason = f"无法写入目标文件夹：{exc}"
            plan.downloaded_path = None
        except Exception as exc:  # keep going; one bad download should not abort all
            LOGGER.exception("迁移内容失败：%s", plan.meta.path)
            plan.status = "download_failed"
            plan.reason = f"迁移失败：{exc}"
            plan.downloaded_path = None
    return plans


def retry_plan(
    plan: ModPlan,
    target: Instance,
    *,
    overwrite: bool = True,
    progress: DownloadProgressFn | None = None,
    cancel_event=None,
) -> ModPlan:
    if plan.target is None:
        plan.status = "error"
        plan.reason = "没有可用的下载地址"
        return plan
    if plan.status in ("ready", "download_failed", "cancelled"):
        pass
    destination = target_path_for(target, plan)
    plan.downloaded_path = destination
    try:
        def _progress(done: int, total: int) -> None:
            plan.progress = (done / total) if total else 0.0
            if progress:
                progress(plan, done, total)

        size = download_file(
            plan.target.url,
            destination,
            expected_size=plan.target.size,
            sha1=plan.target.sha1,
            progress=_progress,
            cancel_event=cancel_event,
        )
        plan.status = "downloaded"
        plan.progress = 1.0
        plan.reason = f"已下载 {size / 1024 / 1024:.1f} MB"
    except CancelledError:
        plan.status = "cancelled"
        plan.reason = "下载已取消"
        plan.downloaded_path = None
    except DownloadError as exc:
        plan.status = "download_failed"
        plan.reason = str(exc)
        plan.downloaded_path = None
    except OSError as exc:
        plan.status = "download_failed"
        plan.reason = f"无法写入目标文件夹：{exc}"
        plan.downloaded_path = None
    return plan
