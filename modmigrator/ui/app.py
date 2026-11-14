from __future__ import annotations

import ctypes
import logging
import queue
import re
import sys
import threading
import time
import tkinter as tk
import traceback
import webbrowser
from pathlib import Path
from tkinter import filedialog, ttk
from urllib.parse import quote

from ..config import AppConfig, cache_size
from ..dependencies import resolve_missing_dependencies
from ..matcher import scan_instances, summarize
from ..mcinstance import COMMON_MC_VERSIONS, InstanceError, detect_instance
from ..migrate import download_plans
from ..models import LOADER_LABELS, STATUS_LABELS, Instance, ModPlan
from ..util import (
    APP_DISPLAY_NAME,
    APP_NAME,
    APP_VERSION,
    elide_middle,
    human_size,
    log_dir,
    open_local_path,
    resource_path,
)
from .dialogs import (
    ConfirmDialog,
    HelpDialog,
    MessageDialog,
    SettingsDialog,
    SkipConfirmDialog,
    SummaryDialog,
    enable_dark_title_bar,
)
from .theme import COLORS, STATUS_COLORS, center_window, font, setup_theme
from .widgets import Card, LinkButton, ScrollFrame, StatusPill, Tooltip, field_label, hint_label, mix

LOADER_KEYS = ["fabric", "quilt", "forge", "neoforge", "vanilla"]
LOADER_CHOICES = ["Fabric", "Quilt", "Forge", "NeoForge", "原版"]
LABEL_TO_LOADER = {LOADER_LABELS[key]: key for key in LOADER_KEYS}
FILTERS = ["全部", "可迁移", "资源包光影", "目标缺失", "已迁移"]
MISSING_STATUSES = ("no_target", "not_found", "dependency_missing", "error", "unsupported")
LOGGER = logging.getLogger("modmigrator.ui")

_VERSION_PREFIX_RE = re.compile(r"^mc\d+(?:\.\d+)*(?:[-+])", re.IGNORECASE)
_VERSION_MC_SUFFIX_RE = re.compile(r"\+(?:mc)?\d+(?:\.\d+)+$", re.IGNORECASE)
_VERSION_LOADER_SUFFIX_RE = re.compile(r"[-+](?:fabric|forge|neoforge|quilt)$", re.IGNORECASE)


def _compact_version(label: str) -> str:
    """Trim long loader/MC decorations for the narrow plan table."""
    if not label:
        return "—"
    text = label.strip()
    text = _VERSION_MC_SUFFIX_RE.sub("", text)
    text = _VERSION_PREFIX_RE.sub("", text)
    text = _VERSION_LOADER_SUFFIX_RE.sub("", text)
    return elide_middle(text or label, 24)


def _set_dpi_aware() -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
        except Exception:
            pass


class InstanceCard(Card):
    def __init__(self, parent: tk.Misc, app: "ModMigratorApp", role: str):
        super().__init__(parent)
        self.app = app
        self.role = role
        self.instance: Instance | None = None
        self._updating = False
        self._enabled = True
        self.accent = COLORS["accent"] if role == "source" else COLORS["blue"]
        self._build()

    def _build(self) -> None:
        inner = tk.Frame(self, bg=COLORS["card"])
        inner.pack(fill="both", expand=True, padx=15, pady=12)

        head = tk.Frame(inner, bg=COLORS["card"])
        head.pack(fill="x")
        badge_text = "源" if self.role == "source" else "目标"
        badge = tk.Label(
            head,
            text=badge_text,
            bg=mix(self.accent, COLORS["card"], 0.18),
            fg=self.accent,
            font=font(9, "bold"),
            padx=8,
            pady=2,
        )
        badge.pack(side="left")
        title = "源实例" if self.role == "source" else "目标实例"
        tk.Label(
            head,
            text=title,
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=font(10, "bold"),
        ).pack(side="left", padx=(8, 0))
        self.dot = tk.Label(
            head,
            text="●",
            bg=COLORS["card"],
            fg=COLORS["dim"],
            font=font(8),
        )
        self.dot.pack(side="right")
        self.state_label = tk.Label(
            head,
            text="未识别",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=font(8),
        )
        self.state_label.pack(side="right", padx=(0, 4))

        path_row = tk.Frame(inner, bg=COLORS["card"])
        path_row.pack(fill="x", pady=(9, 0))
        self.path_var = tk.StringVar()
        self.path_var.trace_add("write", self._on_path_edited)
        self.path_entry = ttk.Entry(path_row, textvariable=self.path_var)
        self.path_entry.pack(side="left", fill="x", expand=True)
        self.path_entry.bind("<Return>", lambda _e: self.app.detect_card(self))
        Tooltip(self.path_entry, lambda: self.path_var.get().strip() or "实例目录的完整路径")
        self.browse_btn = ttk.Button(
            path_row,
            text="浏览",
            style="Secondary.TButton",
            command=self._browse,
        )
        self.browse_btn.pack(side="left", padx=(7, 0))

        self.meta_label = tk.Label(
            inner,
            text="选择 .minecraft 或启动器实例目录",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=font(8),
            anchor="w",
            justify="left",
        )
        self.meta_label.pack(fill="x", pady=(5, 0))

        override = tk.Frame(inner, bg=COLORS["card"])
        override.pack(fill="x", pady=(6, 0))
        tk.Label(
            override,
            text="版本",
            bg=COLORS["card"],
            fg=COLORS["dim"],
            font=font(8),
        ).pack(side="left")
        self.version_var = tk.StringVar()
        self.version_combo = ttk.Combobox(
            override,
            textvariable=self.version_var,
            values=COMMON_MC_VERSIONS,
            state="readonly",
            width=9,
        )
        self.version_combo.pack(side="left", padx=(5, 9))
        tk.Label(
            override,
            text="加载器",
            bg=COLORS["card"],
            fg=COLORS["dim"],
            font=font(8),
        ).pack(side="left")
        self.loader_var = tk.StringVar()
        self.loader_combo = ttk.Combobox(
            override,
            textvariable=self.loader_var,
            values=LOADER_CHOICES,
            state="readonly",
            width=8,
        )
        self.loader_combo.pack(side="left", padx=(5, 0))
        self.version_combo.bind("<<ComboboxSelected>>", lambda _e: self._manual())
        self.loader_combo.bind("<<ComboboxSelected>>", lambda _e: self._manual())
        self.redetect = LinkButton(override, "重新识别", lambda: self.app.detect_card(self, quiet=False))
        self.redetect.pack(side="right")

        self.note_label = tk.Label(
            inner,
            text="支持 .minecraft、versions/版本、Prism / MultiMC / CurseForge 实例",
            bg=COLORS["card"],
            fg=COLORS["dim"],
            font=font(8),
            anchor="w",
            justify="left",
        )
        self.note_label.pack(fill="x", pady=(5, 0))

    def _browse(self) -> None:
        initial = self.path_var.get().strip()
        if not initial or not Path(initial).is_dir():
            initial = str(Path.home())
        title = "选择源实例目录" if self.role == "source" else "选择目标实例目录"
        selected = filedialog.askdirectory(parent=self.app, title=title, initialdir=initial, mustexist=True)
        if selected:
            self.path_var.set(str(Path(selected)))
            self.app.detect_card(self, quiet=False)

    def _manual(self) -> None:
        if self._updating:
            return
        self.app.manual_override(self)

    def _on_path_edited(self, *_args: object) -> None:
        if self._updating:
            return
        had_instance = self.instance is not None
        self.instance = None
        self.state_label.configure(text="未识别", fg=COLORS["muted"])
        self.dot.configure(fg=COLORS["dim"])
        if self.path_var.get().strip():
            self.meta_label.configure(
                text="路径已修改，按 Enter 或点击扫描时重新识别",
                fg=COLORS["muted"],
            )
        else:
            self.meta_label.configure(
                text="选择 .minecraft 或启动器实例目录",
                fg=COLORS["muted"],
            )
        if had_instance:
            self.app.on_instance_changed(self)

    def set_path(self, path: str) -> None:
        self._updating = True
        try:
            self.path_var.set(path)
        finally:
            self._updating = False
        self.path_entry.xview_moveto(0.0)

    def set_instance(self, instance: Instance) -> None:
        self.instance = instance
        self._updating = True
        self.path_var.set(str(instance.path))
        self.path_entry.xview_moveto(0.0)
        version = instance.mc_version or ""
        if version:
            # Keep versions detected outside COMMON_MC_VERSIONS selectable too.
            values = list(dict.fromkeys([version, *COMMON_MC_VERSIONS]))
            self.version_combo.configure(values=values)
        self.version_var.set(version)
        self.loader_var.set(instance.loader_label)
        self._updating = False
        self.state_label.configure(text="已识别", fg=COLORS["accent"])
        self.dot.configure(fg=COLORS["accent"])
        bits = [f"Minecraft {instance.mc_version or '?'}"]
        if instance.loader and instance.loader != "vanilla":
            bits.append(f"{instance.loader_label} {instance.loader_version}".strip())
        else:
            bits.append("原版")
        bits.append(f"{instance.mod_count} 个模组")
        if instance.resourcepack_count:
            bits.append(f"{instance.resourcepack_count} 个资源包")
        if instance.shader_count:
            bits.append(f"{instance.shader_count} 个光影")
        self.meta_label.configure(text=" · ".join(bits), fg=COLORS["text"])
        if instance.notes:
            self.note_label.configure(text=instance.notes[0], fg=COLORS["muted"])
        else:
            self.note_label.configure(text=f"mods：{instance.mods_dir}", fg=COLORS["dim"])
        self.app.on_instance_changed(self)
        self.app._save_paths()

    def set_error(self, message: str) -> None:
        self.instance = None
        self.state_label.configure(text="识别失败", fg=COLORS["red"])
        self.dot.configure(fg=COLORS["red"])
        self.meta_label.configure(text=message, fg=COLORS["red"])
        if not self.version_var.get():
            self.version_combo.configure(values=COMMON_MC_VERSIONS)
        self.app.on_instance_changed(self)

    def clear(self) -> None:
        self.instance = None
        self._updating = True
        try:
            self.path_var.set("")
        finally:
            self._updating = False
        self.version_var.set("")
        self.loader_var.set("")
        self.state_label.configure(text="未识别", fg=COLORS["muted"])
        self.dot.configure(fg=COLORS["dim"])
        self.meta_label.configure(text="选择 .minecraft 或启动器实例目录", fg=COLORS["muted"])
        self.app.on_instance_changed(self)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        state = "normal" if enabled else "disabled"
        self.path_entry.configure(state=state)
        self.browse_btn.configure(state=state)
        self.version_combo.configure(state="readonly" if enabled else "disabled")
        self.loader_combo.configure(state="readonly" if enabled else "disabled")
        self.redetect.configure(state=state)


class ModMigratorApp(tk.Tk):
    def __init__(self) -> None:
        _set_dpi_aware()
        super().__init__()
        self.settings = AppConfig.load()
        self.title(f"{APP_DISPLAY_NAME} {APP_VERSION}")
        setup_theme(self)
        self._set_window_geometry()
        self._set_icon()

        self.plans: list[ModPlan] = []
        self.source_instance: Instance | None = None
        self.target_instance: Instance | None = None
        self.busy: str | None = None
        self.cancel_event: threading.Event | None = None
        self.queue: queue.Queue = queue.Queue()
        self._scan_token = 0
        self._download_started = 0.0
        self._closing = False

        self._build()
        self._bind_keys()
        self._restore_paths()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(80, self._drain_queue)
        self.after(180, self._auto_detect)
        self.after(20, lambda: enable_dark_title_bar(self))
        self._log(f"{APP_DISPLAY_NAME} {APP_VERSION} 已启动", "muted")

    def report_callback_exception(self, exc, val, tb) -> None:  # noqa: ANN001
        """Log unexpected Tk callback errors and show a readable dialog."""
        text = "".join(traceback.format_exception(exc, val, tb))
        LOGGER.error("界面回调异常：\n%s", text)
        if self._closing or getattr(self, "_error_showing", False):
            return
        self._error_showing = True
        try:
            MessageDialog(
                self,
                "出现未处理的错误",
                f"{val}\n\n详细信息已写入日志：\n{log_dir() / 'modmigrator.log'}",
            ).show()
        except Exception:
            LOGGER.exception("显示错误对话框失败")
        finally:
            self._error_showing = False

    # ------------------------------------------------------------------ shell
    def _set_window_geometry(self) -> None:
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        width = min(1200, max(1000, sw - 100))
        height = min(800, max(660, sh - 80))
        self.minsize(1000, 640)
        geo = self.settings.window_geometry
        match = re.match(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", geo or "")
        if match:
            w, h, x, y = (int(match.group(i)) for i in range(1, 5))
            if w >= 1000 and h >= 640 and -40 <= x <= sw - 300 and -40 <= y <= sh - 220:
                self.geometry(f"{w}x{h}{x:+d}{y:+d}")
                return
        center_window(self, width, height)

    def _set_icon(self) -> None:
        try:
            self.iconbitmap(default=str(resource_path("assets/icon.ico")))
        except Exception:
            pass
        try:
            self._icon_image = tk.PhotoImage(file=str(resource_path("assets/icon64.png")))
            self.iconphoto(True, self._icon_image)
        except Exception:
            self._icon_image = None

    def _load_logo(self, parent: tk.Misc, size: int = 42) -> tk.Widget:
        try:
            from PIL import Image, ImageTk

            image = Image.open(resource_path("assets/icon64.png")).convert("RGBA")
            image = image.resize((size, size), Image.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            label = tk.Label(parent, image=photo, bg=COLORS["bg"], bd=0)
            label.image = photo  # type: ignore[attr-defined]
            return label
        except Exception:
            canvas = tk.Canvas(parent, width=size, height=size, bg=COLORS["bg"], highlightthickness=0)
            canvas.create_rectangle(2, 2, size - 2, size - 2, fill=COLORS["card"], outline=COLORS["border"])
            canvas.create_text(size / 2, size / 2, text="M", fill=COLORS["accent"], font=font(15, "bold"))
            return canvas

    def _build(self) -> None:
        self.configure(bg=COLORS["bg"])
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        header = tk.Frame(self, bg=COLORS["bg"])
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 6))
        logo = self._load_logo(header)
        logo.pack(side="left", padx=(0, 10))
        titles = tk.Frame(header, bg=COLORS["bg"])
        titles.pack(side="left")
        tk.Label(
            titles,
            text=APP_DISPLAY_NAME,
            bg=COLORS["bg"],
            fg=COLORS["text"],
            font=font(16, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            titles,
            text="按目标 Minecraft 版本重新匹配并下载模组",
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=font(8),
            anchor="w",
        ).pack(anchor="w", pady=(1, 0))

        right = tk.Frame(header, bg=COLORS["bg"])
        right.pack(side="right")
        ttk.Button(right, text="使用说明", style="Ghost.TButton", command=self._help).pack(
            side="left", padx=(0, 4)
        )
        ttk.Button(right, text="设置", style="Ghost.TButton", command=self._open_settings).pack(side="left")

        instances = tk.Frame(self, bg=COLORS["bg"])
        instances.grid(row=1, column=0, sticky="ew", padx=18, pady=(2, 0))
        instances.grid_columnconfigure(0, weight=1)
        instances.grid_columnconfigure(2, weight=1)
        self.source_card = InstanceCard(instances, self, "source")
        self.source_card.grid(row=0, column=0, sticky="nsew")
        self.target_card = InstanceCard(instances, self, "target")
        self.target_card.grid(row=0, column=2, sticky="nsew")

        swap_holder = tk.Frame(instances, bg=COLORS["bg"], width=54)
        swap_holder.grid(row=0, column=1, sticky="ns")
        swap_holder.grid_propagate(False)
        swap = tk.Button(
            swap_holder,
            text="⇄",
            bg=COLORS["card"],
            fg=COLORS["blue"],
            activebackground=COLORS["card_hover"],
            activeforeground=COLORS["text"],
            relief="flat",
            bd=0,
            font=font(14, "bold"),
            cursor="hand2",
            command=self._swap,
        )
        swap.place(relx=0.5, rely=0.5, anchor="center", width=38, height=38)
        Tooltip(swap, "交换源实例与目标实例")

        self._build_action_bar()
        self._build_content()
        self._build_log()
        self._build_footer()

    def _build_action_bar(self) -> None:
        bar = tk.Frame(self, bg=COLORS["bg"])
        bar.grid(row=2, column=0, sticky="ew", padx=18, pady=(10, 8))
        self.scan_btn = ttk.Button(
            bar, text="扫描实例", style="Accent.TButton", command=self.start_scan
        )
        self.scan_btn.pack(side="left")
        Tooltip(self.scan_btn, "读取源模组并在 Modrinth / CurseForge 查询目标版本（F5）")
        self.migrate_btn = ttk.Button(
            bar, text="开始迁移", style="Secondary.TButton", command=self.start_download, state="disabled"
        )
        self.migrate_btn.pack(side="left", padx=(8, 0))
        Tooltip(self.migrate_btn, "把可迁移的模组下载到目标 mods 文件夹")
        self.cancel_btn = ttk.Button(
            bar, text="取消", style="Ghost.TButton", command=self.cancel_task
        )
        Tooltip(self.cancel_btn, "停止当前扫描或下载")

        right = tk.Frame(bar, bg=COLORS["bg"])
        right.pack(side="right")
        self.progress_label = tk.Label(
            right,
            text="准备就绪",
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=font(8),
            width=30,
            anchor="e",
        )
        self.progress_label.pack(side="left", padx=(0, 8))
        self.progress = ttk.Progressbar(right, mode="determinate", maximum=100, length=170)
        self.progress.pack(side="left")
        self.log_btn = ttk.Button(
            bar, text="日志 ▾", style="Ghost.TButton", command=self._toggle_log
        )
        self.log_btn.pack(side="right", padx=(8, 0))
        self.log_visible = False

    def _build_content(self) -> None:
        content = tk.Frame(self, bg=COLORS["bg"])
        content.grid(row=3, column=0, sticky="nsew", padx=18, pady=(0, 8))
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, minsize=330)
        content.grid_rowconfigure(0, weight=1)

        table_card = Card(content)
        table_card.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        table_card.grid_columnconfigure(0, weight=1)
        table_card.grid_rowconfigure(1, weight=1)

        head = tk.Frame(table_card, bg=COLORS["card"])
        head.grid(row=0, column=0, sticky="ew", padx=13, pady=(10, 8))
        left = tk.Frame(head, bg=COLORS["card"])
        left.pack(side="left")
        title_row = tk.Frame(left, bg=COLORS["card"])
        title_row.pack(anchor="w")
        tk.Label(
            title_row,
            text="迁移计划",
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=font(10, "bold"),
        ).pack(side="left")
        self.count_label = tk.Label(
            title_row,
            text="0 个模组",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=font(8),
        )
        self.count_label.pack(side="left", padx=(8, 0))
        self.summary_label = tk.Label(
            left,
            text="扫描后将在这里列出每个模组的匹配结果",
            bg=COLORS["card"],
            fg=COLORS["dim"],
            font=font(8),
            anchor="w",
        )
        self.summary_label.pack(anchor="w", pady=(2, 0))

        tools = tk.Frame(head, bg=COLORS["card"])
        tools.pack(side="right")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_a: self._refresh_tree())
        tk.Label(
            tools, text="搜索", bg=COLORS["card"], fg=COLORS["dim"], font=font(8)
        ).pack(side="left", padx=(0, 4))
        self.search_entry = ttk.Entry(tools, textvariable=self.search_var, width=14)
        self.search_entry.pack(side="left")
        self.filter_var = tk.StringVar(value="全部")
        self.filter_combo = ttk.Combobox(
            tools,
            textvariable=self.filter_var,
            values=FILTERS,
            state="readonly",
            width=8,
        )
        self.filter_combo.pack(side="left", padx=(7, 0))
        self.filter_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_tree())

        tree_holder = tk.Frame(table_card, bg=COLORS["card"])
        tree_holder.grid(row=1, column=0, sticky="nsew", padx=1, pady=(0, 1))
        tree_holder.grid_columnconfigure(0, weight=1)
        tree_holder.grid_rowconfigure(0, weight=1)
        columns = ("status", "kind", "name", "source", "target", "platform")
        self.tree = ttk.Treeview(
            tree_holder, columns=columns, show="headings", selectmode="browse", height=8
        )
        headings = {
            "status": ("状态", 78, "center"),
            "kind": ("类型", 58, "center"),
            "name": ("名称", 200, "w"),
            "source": ("源版本", 108, "w"),
            "target": ("目标版本", 160, "w"),
            "platform": ("平台", 70, "w"),
        }
        for key, (text, width, anchor) in headings.items():
            self.tree.heading(key, text=text, anchor=anchor)
            self.tree.column(
                key,
                width=width,
                minwidth=60,
                anchor=anchor,
                stretch=(key == "name"),
            )
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(tree_holder, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_double_click)
        for status, color in STATUS_COLORS.items():
            self.tree.tag_configure(status, foreground=color)

        self.empty_label = tk.Label(
            tree_holder,
            text="选择两个实例后，点击「扫描实例」开始",
            bg=COLORS["card"],
            fg=COLORS["dim"],
            font=font(9),
            justify="center",
        )
        self.empty_label.place(relx=0.5, rely=0.46, anchor="center")

        detail_card = Card(content)
        detail_card.grid(row=0, column=1, sticky="nsew")
        detail_card.grid_columnconfigure(0, weight=1)
        detail_card.grid_rowconfigure(1, weight=1)
        dhead = tk.Frame(detail_card, bg=COLORS["card"])
        dhead.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 6))
        tk.Label(
            dhead,
            text="项目详情",
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=font(10, "bold"),
        ).pack(side="left")
        self.detail_scroll = ScrollFrame(detail_card, bg=COLORS["card"])
        self.detail_scroll.canvas.configure(width=305)
        self.detail_scroll.grid(row=1, column=0, sticky="nsew", padx=(1, 0), pady=(0, 1))
        self.detail_body = self.detail_scroll.inner
        self._render_placeholder("在左侧选择一个项目查看详情")

    def _build_log(self) -> None:
        self.log_card = Card(self)
        self.log_card.grid(row=4, column=0, sticky="ew", padx=18, pady=(0, 6))
        self.log_card.grid_columnconfigure(0, weight=1)
        self.log_card.grid_rowconfigure(1, weight=1)
        head = tk.Frame(self.log_card, bg=COLORS["card"])
        head.grid(row=0, column=0, sticky="ew", padx=12, pady=(6, 0))
        tk.Label(
            head, text="运行日志", bg=COLORS["card"], fg=COLORS["muted"], font=font(8, "bold")
        ).pack(side="left")
        ttk.Button(head, text="清空", style="Ghost.TButton", command=self._clear_log).pack(side="right")
        ttk.Button(head, text="收起", style="Ghost.TButton", command=self._toggle_log).pack(
            side="right", padx=(0, 4)
        )
        self.log_text = tk.Text(
            self.log_card,
            height=6,
            bg=COLORS["card"],
            fg=COLORS["muted"],
            relief="flat",
            bd=0,
            wrap="word",
            font=font(8, family="Consolas"),
            padx=12,
            pady=4,
            cursor="arrow",
        )
        self.log_text.grid(row=1, column=0, sticky="nsew", padx=1, pady=(2, 1))
        self.log_text.tag_configure("info", foreground=COLORS["muted"])
        self.log_text.tag_configure("muted", foreground=COLORS["dim"])
        self.log_text.tag_configure("success", foreground=COLORS["accent"])
        self.log_text.tag_configure("warn", foreground=COLORS["orange"])
        self.log_text.tag_configure("error", foreground=COLORS["red"])
        self.log_text.configure(state="disabled")
        self.log_card.grid_remove()

    def _build_footer(self) -> None:
        footer = tk.Frame(self, bg=COLORS["bg_alt"], height=24)
        footer.grid(row=5, column=0, sticky="ew")
        footer.grid_propagate(False)
        self.status_label = tk.Label(
            footer,
            text="就绪",
            bg=COLORS["bg_alt"],
            fg=COLORS["muted"],
            font=font(8),
            anchor="w",
        )
        self.status_label.pack(side="left", padx=18)
        self.cf_label = tk.Label(
            footer,
            text="",
            bg=COLORS["bg_alt"],
            fg=COLORS["dim"],
            font=font(8),
        )
        self.cf_label.pack(side="right", padx=18)
        self._refresh_footer()

    def _bind_keys(self) -> None:
        self.bind("<F5>", lambda _e: self.start_scan())
        self.bind("<Control-comma>", lambda _e: self._open_settings())
        self.bind("<Control-h>", lambda _e: self._help())
        self.bind("<Escape>", lambda _e: self.cancel_task())

    # ------------------------------------------------------------------ state
    def _restore_paths(self) -> None:
        if self.settings.last_source:
            self.source_card.set_path(self.settings.last_source)
        if self.settings.last_target:
            self.target_card.set_path(self.settings.last_target)

    def _auto_detect(self) -> None:
        for card in (self.source_card, self.target_card):
            if card.path_var.get().strip():
                self.detect_card(card, quiet=True)

    def detect_card(self, card: InstanceCard, quiet: bool = False) -> Instance | None:
        raw = card.path_var.get().strip().strip('"')
        if not raw:
            if not quiet:
                card.set_error("请先选择实例目录")
            return None
        try:
            instance = detect_instance(str(Path(raw).expanduser()))
            card.set_instance(instance)
            if card.role == "source":
                self.source_instance = instance
            else:
                self.target_instance = instance
            if not quiet:
                self._log(f"已识别{('源' if card.role == 'source' else '目标')}实例：{instance.display}", "success")
            self._save_paths()
            return instance
        except InstanceError as exc:
            card.set_error(str(exc))
            if not quiet:
                self._log(f"实例识别失败：{exc}", "error")
            return None
        except Exception as exc:
            card.set_error(f"无法读取实例：{exc}")
            if not quiet:
                self._log(f"实例识别失败：{exc}", "error")
            return None

    def manual_override(self, card: InstanceCard) -> None:
        version = card.version_var.get().strip()
        loader = LABEL_TO_LOADER.get(card.loader_var.get().strip(), "")
        if not version:
            return
        if card.instance is not None:
            card.instance.mc_version = version
            if loader:
                card.instance.loader = loader
            card.instance.notes = ["版本 / 加载器已手动修正"] + [
                n for n in card.instance.notes if "手动修正" not in n
            ]
            card.set_instance(card.instance)
            self._invalidate_plans("实例版本已被手动修改，请重新扫描")
            return
        try:
            instance = detect_instance(
                card.path_var.get().strip(),
                override_version=version,
                override_loader=loader or None,
            )
            card.set_instance(instance)
        except InstanceError as exc:
            card.set_error(str(exc))

    def on_instance_changed(self, card: InstanceCard) -> None:
        if card.role == "source":
            self.source_instance = card.instance
        else:
            self.target_instance = card.instance
        if self.plans:
            self._invalidate_plans("实例已更改，请重新扫描以更新迁移计划")

    def _invalidate_plans(self, reason: str) -> None:
        if not self.plans:
            return
        self.plans = []
        self._refresh_tree()
        self._render_placeholder("实例已更改，请重新扫描")
        self._update_actions()
        self.progress.configure(value=0)
        self.progress_label.configure(text="准备就绪")
        self._log(reason, "warn")
        self._set_status(reason, "orange")

    def _swap(self) -> None:
        if self.busy:
            return
        left_path = self.source_card.path_var.get()
        right_path = self.target_card.path_var.get()
        left_instance = self.source_card.instance
        right_instance = self.target_card.instance
        self.source_card.path_var.set(right_path)
        self.target_card.path_var.set(left_path)
        self.source_card.instance = right_instance
        self.target_card.instance = left_instance
        if right_instance:
            self.source_card.set_instance(right_instance)
        else:
            self.source_card.clear()
            self.source_card.path_var.set(right_path)
        if left_instance:
            self.target_card.set_instance(left_instance)
        else:
            self.target_card.clear()
            self.target_card.path_var.set(left_path)
        self.source_instance = self.source_card.instance
        self.target_instance = self.target_card.instance
        self._invalidate_plans("已交换源实例与目标实例，请重新扫描")
        self._log("已交换源实例与目标实例", "muted")

    # ------------------------------------------------------------------ scan
    def _validate_instances(self) -> bool:
        source = self.source_card.instance or self.detect_card(self.source_card, quiet=False)
        target = self.target_card.instance or self.detect_card(self.target_card, quiet=False)
        if source is None or target is None:
            MessageDialog(
                self,
                "实例未就绪",
                "请先选择并识别源实例和目标实例。\n\n"
                "如果自动识别失败，可以手动选择 Minecraft 版本和加载器，"
                "或确认路径指向 .minecraft / versions/版本 / 启动器实例目录。",
            ).show()
            return False
        self.source_instance, self.target_instance = source, target
        if not (
            source.mods_dir.is_dir()
            or source.resourcepack_count
            or source.shader_count
        ):
            MessageDialog(
                self,
                "找不到可迁移内容",
                f"源实例中没有 mods、resourcepacks 或 shaderpacks 文件夹：\n{source.game_dir}\n\n"
                "请确认这是正确的实例目录。",
            ).show()
            return False
        if source.mods_dir.is_dir() and target.mods_dir.is_dir() and str(
            source.mods_dir.resolve()
        ).casefold() == str(target.mods_dir.resolve()).casefold():
            if not ConfirmDialog(
                self,
                "源和目标相同",
                "源实例与目标实例使用同一个 mods 文件夹。继续扫描不会修改文件，"
                "但仍建议确认路径是否选择正确。",
                confirm_text="仍然扫描",
            ).show():
                return False
        return True

    def start_scan(self) -> None:
        if self.busy:
            return
        if not self._validate_instances():
            return
        assert self.source_instance and self.target_instance
        source, target = self.source_instance, self.target_instance
        self._save_paths()
        self.plans = []
        self._refresh_tree()
        self._render_placeholder("正在读取模组、资源包与光影并查询平台…")
        self._set_busy("scan")
        self.progress.configure(value=0)
        self.progress_label.configure(text="准备扫描…")
        self._set_status(f"正在扫描：Minecraft {source.mc_version} → {target.mc_version}", "blue")
        self._log(
            f"开始扫描：{source.display}（{source.mod_count} 个模组 · "
            f"{source.resourcepack_count} 个资源包 · {source.shader_count} 个光影） → "
            f"{target.display}（目标 {target.mod_count} 个模组 · "
            f"{target.resourcepack_count} 个资源包 · {target.shader_count} 个光影）",
            "info",
        )
        cancel = threading.Event()
        self.cancel_event = cancel
        token = self._scan_token + 1
        self._scan_token = token

        settings = self.settings

        def worker() -> None:
            try:
                plans = scan_instances(
                    source,
                    target,
                    curseforge_api_key=settings.curseforge_api_key,
                    use_cfwidget=settings.use_cfwidget,
                    overwrite=settings.overwrite,
                    progress=lambda message, percent: self.queue.put(("progress", message, percent)),
                    cancel_event=cancel,
                    workers=4,
                )
                self.queue.put(("scan_done", token, plans, None))
            except Exception as exc:
                self.queue.put(("scan_done", token, None, str(exc)))

        threading.Thread(target=worker, name="mod-scan", daemon=True).start()

    def _handle_scan_done(self, token: int, plans: list[ModPlan] | None, error: str | None) -> None:
        if token != self._scan_token:
            return
        self._set_busy(None)
        self.cancel_event = None
        if error:
            self.progress.configure(value=0)
            self.progress_label.configure(text="扫描失败")
            self._set_status("扫描失败", "red")
            self._log(f"扫描失败：{error}", "error")
            MessageDialog(
                self,
                "扫描失败",
                f"查询模组时发生错误：\n\n{error}\n\n请检查网络连接后重试。",
            ).show()
            return
        self.plans = plans or []
        self._refresh_tree()
        self._update_actions()
        if not self.plans:
            self.progress.configure(value=100)
            self.progress_label.configure(text="源实例中没有可迁移内容")
            self._set_status("源实例中没有找到模组、资源包或光影", "orange")
            self._log("源实例中没有找到模组、资源包或光影", "warn")
            self._render_placeholder("源实例的 mods / resourcepacks / shaderpacks 为空")
            return
        counts = summarize(self.plans)
        self.progress.configure(value=100)
        detail = " · ".join(
            f"{STATUS_LABELS.get(key, key)} {counts[key]}" for key in counts
        )
        self.progress_label.configure(text=f"扫描完成：{len(self.plans)} 个项目")
        self._set_status(f"扫描完成：{detail}", "accent")
        self._log(f"扫描完成：{detail}", "success")
        ready = counts.get("ready", 0) + counts.get("copy", 0)
        if ready:
            first = next((p for p in self.plans if p.status in ("ready", "copy")), None)
            if first is not None:
                index = self.plans.index(first)
                self.tree.selection_set(str(index))
                self.tree.focus(str(index))
                self.tree.see(str(index))
        else:
            self._render_placeholder("没有可迁移的项目，请在详情中查看每个项目的原因")

    # ------------------------------------------------------------------ download
    def start_download(self) -> None:
        if self.busy or not self.target_instance:
            return
        ready = [
            p for p in self.plans if p.status in ("ready", "copy") and (p.target is not None or p.status == "copy")
        ]
        if not ready:
            MessageDialog(
                self,
                "没有可迁移的项目",
                "当前迁移计划中没有状态为“可迁移”或“可复制”的项目。\n"
                "请先扫描，或查看每个项目的状态与原因。",
            ).show()
            return
        blocked = [p for p in self.plans if p.status in MISSING_STATUSES]
        if blocked:
            if not SkipConfirmDialog(self, blocked).show():
                return
        if self.settings.overwrite:
            if not ConfirmDialog(
                self,
                "覆盖模式已开启",
                "设置中已开启“覆盖同名文件”。目标实例中与下载/复制文件同名的文件会被替换。\n\n"
                f"目标实例：{self.target_instance.path}",
                confirm_text="继续迁移",
                danger=True,
            ).show():
                return
        target = self.target_instance
        self._set_busy("download")
        self.progress.configure(value=0)
        self.progress_label.configure(text="准备下载…")
        self._set_status(f"正在处理 {len(ready)} 个项目…", "blue")
        self._log(f"开始迁移 {len(ready)} 个项目到 {target.path}", "info")
        cancel = threading.Event()
        self.cancel_event = cancel
        order = {id(plan): i + 1 for i, plan in enumerate(ready)}
        total = len(ready)
        started = time.monotonic()
        self._download_started = started
        last_update = {"time": 0.0, "bucket": -1}

        def progress(plan: ModPlan, done: int, total_bytes: int) -> None:
            index = order.get(id(plan), 1)
            fraction = (done / total_bytes) if total_bytes else 0.0
            overall = max(0.0, min(100.0, ((index - 1) + fraction) / total * 100.0))
            now = time.monotonic()
            bucket = int(overall // 3)
            if now - last_update["time"] < 0.14 and bucket == last_update["bucket"] and done not in (0, total_bytes):
                return
            last_update["time"] = now
            last_update["bucket"] = bucket
            self.queue.put(
                (
                    "download_progress",
                    index,
                    total,
                    plan.meta.display_name,
                    overall,
                    done,
                    total_bytes,
                )
            )

        def worker() -> None:
            try:
                download_plans(
                    ready,
                    target,
                    overwrite=self.settings.overwrite,
                    progress=progress,
                    cancel_event=cancel,
                )
                if not cancel.is_set():
                    self.queue.put(("progress", "正在检查模组前置…", 100))
                    try:
                        deps = resolve_missing_dependencies(
                            target,
                            overwrite=self.settings.overwrite,
                            cancel_event=cancel,
                        )
                    except Exception as exc:  # dependency repair must not undo downloads
                        self.queue.put(("log", f"前置检查失败：{exc}", "error"))
                        deps = []
                    self.queue.put(("deps_done", deps))
                self.queue.put(("download_done", None))
            except Exception as exc:
                self.queue.put(("download_done", str(exc)))

        threading.Thread(target=worker, name="mod-download", daemon=True).start()

    def _handle_deps_done(self, deps: list[ModPlan] | None) -> None:
        deps = deps or []
        if not deps:
            return
        self.plans.extend(deps)
        self._refresh_tree()
        self._update_actions()
        resolved = sum(1 for p in deps if p.status in ("downloaded", "already"))
        missing = sum(1 for p in deps if p.status == "dependency_missing")
        self._log(
            f"前置补全：已处理 {resolved} 个 · 缺失 {missing} 个",
            "success" if not missing else "warn",
        )

    def _handle_download_done(self, error: str | None) -> None:
        self._set_busy(None)
        self.cancel_event = None
        self.progress.configure(value=100 if not error else self.progress["value"])
        self._refresh_tree()
        self._update_actions()
        counts = summarize(self.plans)
        downloaded = counts.get("downloaded", 0)
        copied = counts.get("copied", 0)
        failed = counts.get("download_failed", 0)
        already = counts.get("already", 0)
        skipped = sum(
            counts.get(key, 0)
            for key in ("no_target", "not_found", "dependency_missing", "unsupported")
        )
        cancelled = counts.get("cancelled", 0)
        success = downloaded + copied
        elapsed = max(0.1, time.monotonic() - self._download_started)
        if error:
            self.progress_label.configure(text="迁移任务异常结束")
            self._set_status("迁移任务异常结束", "red")
            self._log(f"迁移任务异常结束：{error}", "error")
        elif cancelled:
            self.progress_label.configure(text=f"已取消：成功 {success} 个")
            self._set_status(f"迁移已取消，已处理 {success} 个项目", "orange")
            self._log(f"迁移已取消：成功 {success} 个", "warn")
        else:
            self.progress_label.configure(text=f"迁移完成：{success} 个项目")
            self._set_status(
                f"迁移完成：{downloaded} 个已下载，{copied} 个已复制，{failed} 个失败",
                "accent" if not failed else "orange",
            )
            self._log(
                f"迁移完成：下载 {downloaded} · 复制 {copied} · 失败 {failed} · "
                f"已存在 {already} · 跳过 {skipped} · 用时 {elapsed:.1f}s",
                "success" if not failed else "warn",
            )

        if self.settings.auto_open_folder and success:
            self._open_target_folder()

        details: list[str] = []
        for plan in self.plans:
            if plan.status == "downloaded":
                details.append(f"✓ {plan.display_name}  →  {plan.target.name}")
            elif plan.status == "copied":
                details.append(f"✓ {plan.display_name}  →  已复制原文件")
            elif plan.status == "download_failed":
                details.append(f"✗ {plan.display_name}  —  {plan.reason}")
            elif plan.status in (
                "no_target",
                "not_found",
                "dependency_missing",
                "unsupported",
            ):
                details.append(f"— {plan.display_name}  —  {plan.status_label}：{plan.reason}")
        message = (
            f"已下载 {downloaded} 个项目，并复制 {copied} 个旧资源包/光影到 "
            f"Minecraft {self.target_instance.mc_version} · {self.target_instance.loader_label} 实例。"
            if success
            else "本次没有下载或复制任何项目。"
        )
        if failed:
            message += f"\n其中 {failed} 个失败，可在详情中查看原因。"
        if already:
            message += f"\n另有 {already} 个项目目标实例中已存在，已跳过。"
        summary_counts = [
            ("已下载", downloaded, COLORS["accent"]),
            ("已复制", copied, COLORS["blue"]),
            ("已存在", already, COLORS["blue"]),
            ("跳过", skipped, COLORS["orange"]),
            ("失败", failed, COLORS["red"]),
        ]
        SummaryDialog(
            self,
            "迁移完成" if not failed else "迁移完成，但有失败",
            message,
            summary_counts,
            details,
            on_export=self._export_csv,
            on_open=self._open_target_folder,
        ).show()

    def cancel_task(self) -> None:
        if not self.busy or self.cancel_event is None:
            return
        self.cancel_event.set()
        self.cancel_btn.configure(state="disabled", text="正在取消…")
        self.progress_label.configure(text="正在取消…")
        self._set_status("正在取消当前任务…", "orange")
        self._log("用户请求取消当前任务", "warn")

    # ------------------------------------------------------------------ table/detail
    def _filtered_indices(self) -> list[int]:
        query = self.search_var.get().strip().casefold()
        mode = self.filter_var.get()
        result = []
        for index, plan in enumerate(self.plans):
            if mode == "可迁移" and plan.status not in ("ready", "copy"):
                continue
            if mode == "资源包光影" and plan.kind == "mod":
                continue
            if mode == "目标缺失" and plan.status not in (
                "no_target",
                "not_found",
                "dependency_missing",
                "unsupported",
            ):
                continue
            if mode == "已迁移" and plan.status not in ("downloaded", "copied", "already"):
                continue
            if query:
                haystack = " ".join(
                    [
                        plan.meta.display_name,
                        plan.meta.mod_id,
                        plan.meta.filename,
                        plan.kind_label,
                        plan.project.title if plan.project else "",
                    ]
                ).casefold()
                if query not in haystack:
                    continue
            result.append(index)
        return result

    def _refresh_tree(self) -> None:
        selected = self.tree.selection()
        selected_iid = selected[0] if selected else None
        self.tree.delete(*self.tree.get_children())
        indices = self._filtered_indices()
        for index in indices:
            plan = self.plans[index]
            target_text = _compact_version(plan.target.version_label) if plan.target else "—"
            if plan.target and plan.target.size_label:
                target_text = f"{target_text} · {plan.target.size_label.replace(' ', '')}"
            if plan.status == "copy":
                target_text = "复制旧文件"
            platform_text = (
                plan.source_label.replace("Modrinth", "MR").replace("CurseForge", "CF").replace(" + ", "+")
                if plan.source_label != "—"
                else "—"
            )
            values = (
                f"● {plan.status_label}",
                plan.kind_label,
                plan.display_name,
                _compact_version(plan.meta.version),
                target_text,
                platform_text,
            )
            self.tree.insert("", "end", iid=str(index), values=values, tags=(plan.status,))
        if selected_iid and self.tree.exists(selected_iid):
            self.tree.selection_set(selected_iid)
        if indices:
            self.empty_label.place_forget()
        else:
            self.empty_label.place(relx=0.5, rely=0.46, anchor="center")
            if self.plans:
                self.empty_label.configure(text="没有符合当前搜索或筛选条件的项目")
            elif not self.busy:
                self.empty_label.configure(text="选择两个实例后，点击「扫描实例」开始")
        self.count_label.configure(
            text=f"{len(indices)} / {len(self.plans)} 个项目" if len(indices) != len(self.plans) else f"{len(self.plans)} 个项目"
        )
        counts = summarize(self.plans)
        if self.plans:
            missing = sum(
                counts.get(key, 0)
                for key in ("no_target", "not_found", "dependency_missing", "error", "unsupported")
            )
            self.summary_label.configure(
                text=f"可迁移 {counts.get('ready', 0)} · 可复制 {counts.get('copy', 0)} · "
                f"已存在 {counts.get('already', 0)} · 目标缺失 {missing} · "
                f"已下载 {counts.get('downloaded', 0)} · 已复制 {counts.get('copied', 0)}"
            )
        else:
            self.summary_label.configure(text="扫描后将在这里列出每个模组、资源包或光影的匹配结果")

    def _on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        try:
            index = int(selection[0])
        except ValueError:
            return
        if 0 <= index < len(self.plans):
            self._render_detail(self.plans[index])

    def _on_double_click(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        index = int(selection[0])
        plan = self.plans[index]
        if plan.project and plan.project.url:
            webbrowser.open(plan.project.url)

    def _clear_detail(self) -> None:
        for child in self.detail_body.winfo_children():
            child.destroy()

    def _render_placeholder(self, text: str) -> None:
        self._clear_detail()
        wrapper = tk.Frame(self.detail_body, bg=COLORS["card"])
        wrapper.pack(fill="both", expand=True, padx=16, pady=30)
        tk.Label(
            wrapper,
            text="◇",
            bg=COLORS["card"],
            fg=COLORS["dim"],
            font=(font(20)[0], 24),
        ).pack()
        tk.Label(
            wrapper,
            text=text,
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=font(9),
            wraplength=280,
            justify="center",
        ).pack(pady=(8, 0))
        self.detail_scroll.canvas.yview_moveto(0)

    def _render_detail(self, plan: ModPlan) -> None:
        self._clear_detail()
        color = STATUS_COLORS.get(plan.status, COLORS["muted"])
        wrapper = tk.Frame(self.detail_body, bg=COLORS["card"])
        wrapper.pack(fill="both", expand=True, padx=14, pady=10)
        top = tk.Frame(wrapper, bg=COLORS["card"])
        top.pack(fill="x")
        StatusPill(top, plan.status_label, color).pack(side="left")
        if plan.source_label and plan.source_label != "—":
            tk.Label(
                top,
                text=plan.source_label,
                bg=COLORS["card"],
                fg=COLORS["dim"],
                font=font(8),
            ).pack(side="right")
        tk.Label(
            wrapper,
            text=plan.display_name,
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=font(12, "bold"),
            anchor="w",
            justify="left",
            wraplength=290,
        ).pack(fill="x", pady=(7, 2))
        tk.Label(
            wrapper,
            text=f"{plan.kind_label} · {plan.meta.mod_id or '未知 ID'} · {plan.meta.display_version}",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=font(8),
            anchor="w",
            wraplength=290,
        ).pack(fill="x")
        tk.Frame(wrapper, bg=COLORS["border_soft"], height=1).pack(fill="x", pady=9)

        source_text = plan.meta.filename
        self._kv(wrapper, "源文件", source_text)
        if plan.meta.loader:
            self._kv(wrapper, "源加载器", LOADER_LABELS.get(plan.meta.loader, plan.meta.loader))
        if plan.target:
            self._kv(wrapper, "目标平台", plan.target.platform_label)
            self._kv(wrapper, "目标版本", plan.target.version_label or "—")
            self._kv(wrapper, "目标文件", plan.target.name)
            self._kv(wrapper, "文件大小", plan.target.size_label)
            if plan.target.published:
                self._kv(wrapper, "发布时间", plan.target.published[:10])
        elif plan.status == "copy":
            self._kv(wrapper, "处理方式", "复制源文件")
            self._kv(wrapper, "目标位置", plan.kind_label)
        if plan.downloaded_path:
            self._kv(wrapper, "已写入", plan.downloaded_path.name)
        if plan.existing:
            self._kv(wrapper, "目标已有", plan.existing)

        reason_color = color if plan.status not in ("ready", "downloaded") else COLORS["blue"]
        box = tk.Frame(wrapper, bg=mix(reason_color, COLORS["card"], 0.10))
        box.pack(fill="x", pady=(8, 8))
        tk.Label(
            box,
            text="说明",
            bg=box["bg"],
            fg=reason_color,
            font=font(8, "bold"),
            anchor="w",
        ).pack(fill="x", padx=9, pady=(6, 1))
        tk.Label(
            box,
            text=plan.reason or "—",
            bg=box["bg"],
            fg=COLORS["text"],
            font=font(8),
            anchor="w",
            justify="left",
            wraplength=270,
        ).pack(fill="x", padx=9, pady=(0, 7))

        link_row = tk.Frame(wrapper, bg=COLORS["card"])
        link_row.pack(fill="x", pady=(2, 0))
        if plan.project and plan.project.url:
            LinkButton(link_row, f"在 {plan.project.platform_label} 打开", lambda url=plan.project.url: webbrowser.open(url)).pack(anchor="w")
        else:
            search_name = plan.meta.name or plan.meta.mod_id or plan.meta.path.stem
            mr_section = {"resourcepack": "resourcepacks", "shader": "shaders"}.get(
                plan.kind, "mods"
            )
            cf_section = {"resourcepack": "texture-packs", "shader": "shaders"}.get(
                plan.kind
            )
            cf_url = (
                f"https://www.curseforge.com/minecraft/{cf_section}?search={quote(search_name)}"
                if cf_section
                else f"https://www.curseforge.com/minecraft/search?search={quote(search_name)}"
            )
            LinkButton(
                link_row,
                "在 Modrinth 搜索",
                lambda q=search_name, section=mr_section: webbrowser.open(
                    f"https://modrinth.com/{section}?q={quote(q)}"
                ),
            ).pack(anchor="w")
            LinkButton(
                link_row,
                "在 CurseForge 搜索",
                lambda url=cf_url: webbrowser.open(url),
            ).pack(anchor="w")

        if plan.alt_projects:
            tk.Frame(wrapper, bg=COLORS["border_soft"], height=1).pack(fill="x", pady=8)
            field_label(wrapper, "其他平台", bg=COLORS["card"]).pack(fill="x")
            for alt in plan.alt_projects:
                if alt.url:
                    LinkButton(
                        wrapper,
                        f"{alt.platform_label}：{alt.title}",
                        lambda url=alt.url: webbrowser.open(url),
                    ).pack(anchor="w", pady=(2, 0))

        if plan.project and plan.project.description:
            tk.Frame(wrapper, bg=COLORS["border_soft"], height=1).pack(fill="x", pady=8)
            field_label(wrapper, "项目简介", bg=COLORS["card"]).pack(fill="x")
            tk.Label(
                wrapper,
                text=plan.project.description,
                bg=COLORS["card"],
                fg=COLORS["muted"],
                font=font(8),
                anchor="w",
                justify="left",
                wraplength=286,
            ).pack(fill="x", pady=(2, 0))
        self.detail_scroll.canvas.yview_moveto(0)

    def _kv(self, parent: tk.Misc, label: str, value: str) -> None:
        row = tk.Frame(parent, bg=COLORS["card"])
        row.pack(fill="x", pady=1)
        tk.Label(
            row,
            text=label,
            bg=COLORS["card"],
            fg=COLORS["dim"],
            font=font(8),
            width=7,
            anchor="w",
        ).pack(side="left", anchor="n")
        tk.Label(
            row,
            text=value,
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=font(8),
            anchor="w",
            justify="left",
            wraplength=235,
        ).pack(side="left", fill="x", expand=True)

    # ------------------------------------------------------------------ state helpers
    def _set_busy(self, kind: str | None) -> None:
        self.busy = kind
        enabled = kind is None
        self.source_card.set_enabled(enabled)
        self.target_card.set_enabled(enabled)
        self.scan_btn.configure(state="normal" if enabled else "disabled")
        self.cancel_btn.pack_forget()
        if kind:
            self.cancel_btn.configure(state="normal", text="取消")
            self.cancel_btn.pack(side="left", padx=(8, 0))
        self._update_actions()

    def _update_actions(self) -> None:
        ready = sum(1 for p in self.plans if p.status in ("ready", "copy"))
        if self.busy:
            self.migrate_btn.configure(state="disabled", text="开始迁移")
            return
        self.migrate_btn.configure(
            state="normal" if ready else "disabled",
            text=f"开始迁移（{ready}）" if ready else "开始迁移",
        )
        if ready:
            self.scan_btn.configure(style="Secondary.TButton")
            self.migrate_btn.configure(style="Accent.TButton")
        else:
            self.scan_btn.configure(style="Accent.TButton")
            self.migrate_btn.configure(style="Secondary.TButton")

    def _set_status(self, text: str, color: str = "muted") -> None:
        self.status_label.configure(text=text, fg=COLORS.get(color, COLORS["muted"]))

    def _refresh_footer(self) -> None:
        mode = "官方 API" if self.settings.has_cf_key else ("公共数据源" if self.settings.use_cfwidget else "未启用")
        try:
            size = human_size(cache_size())
        except Exception:
            size = "未知"
        self.cf_label.configure(text=f"CurseForge：{mode}   ·   接口缓存 {size}")

    # ------------------------------------------------------------------ messaging
    def _log(self, message: str, level: str = "info") -> None:
        if not hasattr(self, "log_text"):
            return
        stamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{stamp}] ", "muted")
        self.log_text.insert("end", f"{message}\n", level)
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 500:
            self.log_text.delete("1.0", f"{lines - 400}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _toggle_log(self) -> None:
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_card.grid()
            self.log_btn.configure(text="日志 ▴")
        else:
            self.log_card.grid_remove()
            self.log_btn.configure(text="日志 ▾")

    def _drain_queue(self) -> None:
        if self._closing:
            return
        try:
            while True:
                message = self.queue.get_nowait()
                kind = message[0]
                if kind == "progress":
                    _, text, percent = message
                    self.progress.configure(value=max(0, min(100, int(percent))))
                    self.progress_label.configure(text=text[:34])
                elif kind == "scan_done":
                    _, token, plans, error = message
                    self._handle_scan_done(token, plans, error)
                elif kind == "download_progress":
                    _, index, total, name, overall, done, total_bytes = message
                    self.progress.configure(value=overall)
                    if total_bytes:
                        self.progress_label.configure(
                            text=f"下载 {index}/{total}：{name[:16]} {done * 100 // total_bytes}%"
                        )
                    else:
                        self.progress_label.configure(text=f"下载 {index}/{total}：{name[:20]}")
                elif kind == "download_done":
                    self._handle_download_done(message[1])
                elif kind == "deps_done":
                    self._handle_deps_done(message[1])
                elif kind == "log":
                    self._log(message[1], message[2] if len(message) > 2 else "info")
        except queue.Empty:
            pass
        if not self._closing:
            self.after(80, self._drain_queue)

    # ------------------------------------------------------------------ actions
    def _open_settings(self) -> None:
        SettingsDialog(
            self,
            self.settings,
            on_save=self._settings_saved,
            on_log=self._log,
        ).show()

    def _settings_saved(self) -> None:
        self._refresh_footer()
        self._log("设置已保存", "success")
        self._set_status("设置已保存", "accent")

    def _help(self) -> None:
        HelpDialog(self).show()

    def _open_target_folder(self) -> None:
        target = self.target_instance
        if not target:
            return
        path = target.mods_dir
        if not path.exists():
            path = target.game_dir
        try:
            open_local_path(path)
            self._log(f"已打开文件夹：{path}", "info")
        except Exception as exc:
            MessageDialog(self, "无法打开文件夹", f"{path}\n\n{exc}").show()

    def _export_csv(self) -> None:
        if not self.plans:
            return
        default_name = f"mod-migration-{self.target_instance.mc_version if self.target_instance else 'report'}.csv"
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出迁移报告",
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            from ..cli import _write_csv

            _write_csv(self.plans, Path(path))
            self._log(f"已导出 CSV 报告：{path}", "success")
            self._set_status("CSV 报告已导出", "accent")
        except Exception as exc:
            self._log(f"导出 CSV 失败：{exc}", "error")
            MessageDialog(self, "导出失败", f"无法写入 CSV 文件：\n{exc}").show()

    def _save_paths(self) -> None:
        self.settings.last_source = self.source_card.path_var.get().strip() if hasattr(self, "source_card") else ""
        self.settings.last_target = self.target_card.path_var.get().strip() if hasattr(self, "target_card") else ""
        try:
            self.settings.save()
        except Exception:
            pass

    def _on_close(self) -> None:
        if self.busy and not ConfirmDialog(
            self,
            "任务正在进行",
            "扫描或下载尚未完成，退出会中断当前任务。确定要退出吗？",
            confirm_text="退出",
            danger=True,
        ).show():
            return
        self._closing = True
        if self.cancel_event is not None:
            self.cancel_event.set()
        try:
            self.settings.window_geometry = self.geometry()
            self.settings.last_source = self.source_card.path_var.get().strip()
            self.settings.last_target = self.target_card.path_var.get().strip()
            self.settings.save()
        except Exception:
            pass
        self.destroy()


def main() -> None:
    try:
        app = ModMigratorApp()
        app.mainloop()
    except Exception as exc:
        LOGGER.exception("应用启动或运行失败")
        try:
            ctypes.windll.user32.MessageBoxW(
                None,
                f"Mod 迁移助手启动失败：\n\n{exc}\n\n日志：{log_dir() / 'modmigrator.log'}",
                "Mod 迁移助手",
                0x10,
            )
        except Exception:
            pass
        raise
