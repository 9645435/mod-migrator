from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from tkinter import ttk
from typing import Callable

from ..config import AppConfig, cache_size, clear_cache
from ..models import STATUS_LABELS, ModPlan
from ..util import APP_DISPLAY_NAME, APP_VERSION, human_size
from .theme import COLORS, center_window, font
from .widgets import Card, StatusPill, field_label, hint_label, mix


def enable_dark_title_bar(window: tk.Misc) -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())  # type: ignore[attr-defined]
        value = ctypes.c_int(1)
        for attribute in (20, 19):
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(  # type: ignore[attr-defined]
                hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
            )
            if result == 0:
                break
    except Exception:
        pass


class BaseDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        width: int = 560,
        height: int = 420,
        resizable: bool = False,
    ):
        super().__init__(parent)
        self.parent = parent
        self.result = None
        self.title(title)
        self.configure(bg=COLORS["bg"])
        self.resizable(resizable, resizable)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _e: self._close())
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._build_shell(title)
        center_window(self, width, height, parent)
        self.after(10, lambda: enable_dark_title_bar(self))
        self.grab_set()
        self.focus_force()

    def _build_shell(self, title: str) -> None:
        header = tk.Frame(self, bg=COLORS["bg"])
        header.grid(row=0, column=0, sticky="ew", padx=22, pady=(18, 8))
        tk.Label(
            header,
            text=title,
            bg=COLORS["bg"],
            fg=COLORS["text"],
            font=font(15, "bold"),
            anchor="w",
        ).pack(side="left")
        close = ttk.Button(header, text="✕", style="Ghost.TButton", command=self._close)
        close.pack(side="right")
        self.body = tk.Frame(self, bg=COLORS["bg"])
        self.body.grid(row=1, column=0, sticky="nsew", padx=22, pady=(4, 0))
        self.footer = tk.Frame(self, bg=COLORS["bg"])
        self.footer.grid(row=2, column=0, sticky="ew", padx=22, pady=(10, 18))

    def _close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def show(self):
        self.wait_window(self)
        return self.result


class SkipConfirmDialog(BaseDialog):
    def __init__(self, parent: tk.Misc, blocked: list[ModPlan]):
        self.blocked = blocked
        super().__init__(parent, "部分项目无法迁移", 640, 500, resizable=True)

        intro = tk.Label(
            self.body,
            text=f"有 {len(blocked)} 个项目在目标版本中没有可用版本，或未能在模组平台找到。",
            bg=COLORS["bg"],
            fg=COLORS["orange"],
            font=font(10, "bold"),
            anchor="w",
        )
        intro.pack(fill="x", pady=(0, 4))
        tk.Label(
            self.body,
            text="继续迁移会跳过这些项目，其余项目仍会正常下载或复制。",
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=font(9),
            anchor="w",
        ).pack(fill="x", pady=(0, 10))

        list_card = Card(self.body)
        list_card.pack(fill="both", expand=True)
        text = tk.Text(
            list_card,
            bg=COLORS["card"],
            fg=COLORS["text"],
            relief="flat",
            bd=0,
            wrap="word",
            font=font(9),
            height=10,
            padx=12,
            pady=10,
            cursor="arrow",
        )
        scroll = ttk.Scrollbar(list_card, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        for plan in blocked:
            text.insert("end", f"• {plan.meta.display_name}  ", ("name",))
            text.insert("end", f"[{plan.status_label}]\n", ("status",))
            if plan.reason:
                text.insert("end", f"   {plan.reason}\n", ("reason",))
        text.tag_configure("name", foreground=COLORS["text"], font=font(9, "bold"))
        text.tag_configure("status", foreground=COLORS["orange"], font=font(8, "bold"))
        text.tag_configure("reason", foreground=COLORS["muted"], font=font(8))
        text.configure(state="disabled")

        ttk.Button(self.footer, text="取消", style="TButton", command=self._close).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(
            self.footer,
            text=f"继续并跳过 {len(blocked)} 个",
            style="Accent.TButton",
            command=self._continue,
        ).pack(side="right")

    def _continue(self) -> None:
        self.result = True
        self._close()


class ConfirmDialog(BaseDialog):
    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        message: str,
        confirm_text: str = "继续",
        cancel_text: str = "取消",
        danger: bool = False,
    ):
        super().__init__(parent, title, 520, 290, resizable=True)
        tk.Label(
            self.body,
            text=message,
            bg=COLORS["bg"],
            fg=COLORS["text"],
            font=font(9),
            anchor="w",
            justify="left",
            wraplength=460,
        ).pack(fill="both", expand=True, pady=(4, 12))
        ttk.Button(
            self.footer, text=cancel_text, style="TButton", command=self._cancel
        ).pack(side="right", padx=(8, 0))
        ttk.Button(
            self.footer,
            text=confirm_text,
            style="Accent.TButton" if not danger else "Danger.TButton",
            command=self._confirm,
        ).pack(side="right")

    def _confirm(self) -> None:
        self.result = True
        self._close()

    def _cancel(self) -> None:
        self.result = False
        self._close()


class SummaryDialog(BaseDialog):
    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        message: str,
        counts: list[tuple[str, int, str]],
        details: list[str],
        on_export: Callable[[], None] | None = None,
        on_open: Callable[[], None] | None = None,
    ):
        super().__init__(parent, title, 600, 450, resizable=True)

        top = tk.Frame(self.body, bg=COLORS["bg"])
        top.pack(fill="x", pady=(0, 8))
        tk.Label(
            top,
            text="✓",
            bg=COLORS["bg"],
            fg=COLORS["accent"],
            font=(font(24)[0], 26, "bold"),
        ).pack(side="left", padx=(0, 10))
        tk.Label(
            top,
            text=message,
            bg=COLORS["bg"],
            fg=COLORS["text"],
            font=font(10, "bold"),
            anchor="w",
            justify="left",
            wraplength=480,
        ).pack(side="left", fill="x", expand=True)

        chips = tk.Frame(self.body, bg=COLORS["bg"])
        chips.pack(fill="x", pady=(2, 10))
        for caption, value, color in counts:
            box = tk.Frame(chips, bg=COLORS["card"], highlightbackground=COLORS["border"], highlightthickness=1)
            box.pack(side="left", padx=(0, 8))
            tk.Label(box, text=str(value), bg=COLORS["card"], fg=color, font=font(14, "bold")).pack(
                padx=10, pady=(6, 0)
            )
            tk.Label(box, text=caption, bg=COLORS["card"], fg=COLORS["muted"], font=font(8)).pack(
                padx=10, pady=(0, 6)
            )

        if details:
            card = Card(self.body)
            card.pack(fill="both", expand=True)
            text = tk.Text(
                card,
                bg=COLORS["card"],
                fg=COLORS["muted"],
                relief="flat",
                bd=0,
                wrap="word",
                font=font(8),
                padx=12,
                pady=10,
                cursor="arrow",
            )
            scroll = ttk.Scrollbar(card, orient="vertical", command=text.yview)
            text.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            text.pack(side="left", fill="both", expand=True)
            text.insert("1.0", "\n".join(details))
            text.configure(state="disabled")

        if on_open:
            ttk.Button(self.footer, text="打开目标文件夹", style="TButton", command=on_open).pack(
                side="right", padx=(8, 0)
            )
        if on_export:
            ttk.Button(self.footer, text="导出 CSV 报告", style="TButton", command=on_export).pack(
                side="right", padx=(8, 0)
            )
        ttk.Button(self.footer, text="完成", style="Accent.TButton", command=self._close).pack(
            side="right"
        )


class SettingsDialog(BaseDialog):
    def __init__(self, parent: tk.Misc, config: AppConfig, on_save: Callable[[], None], on_log=None):
        self.config = config
        self.on_save = on_save
        self.on_log = on_log
        super().__init__(parent, "设置", 620, 560, resizable=False)

        body = self.body
        field_label(body, "CURSEFORGE API KEY（可选）").pack(fill="x", pady=(0, 4))
        key_row = tk.Frame(body, bg=COLORS["bg"])
        key_row.pack(fill="x")
        self.key_var = tk.StringVar(value=config.curseforge_api_key)
        self.key_entry = ttk.Entry(key_row, textvariable=self.key_var, show="•")
        self.key_entry.pack(side="left", fill="x", expand=True)
        self.show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            key_row,
            text="显示",
            variable=self.show_key,
            command=self._toggle_key,
        ).pack(side="left", padx=(8, 0))
        hint_label(
            body,
            "不填写也能使用：程序会读取 CurseForge 公共数据源。官方 Key 可提高搜索准确度，"
            "并避免部分下载限制。",
        ).pack(fill="x", pady=(4, 12))

        options = Card(body)
        options.pack(fill="x", pady=(0, 12))
        self.cfwidget_var = tk.BooleanVar(value=config.use_cfwidget)
        ttk.Checkbutton(
            options,
            text="允许使用 CurseForge 公共数据源（无需 API Key）",
            variable=self.cfwidget_var,
        ).pack(anchor="w", padx=12, pady=(10, 2))
        self.overwrite_var = tk.BooleanVar(value=config.overwrite)
        ttk.Checkbutton(
            options,
            text="下载时覆盖目标文件夹中的同名文件",
            variable=self.overwrite_var,
        ).pack(anchor="w", padx=12, pady=2)
        self.auto_open_var = tk.BooleanVar(value=config.auto_open_folder)
        ttk.Checkbutton(
            options,
            text="迁移完成后自动打开目标 mods 文件夹",
            variable=self.auto_open_var,
        ).pack(anchor="w", padx=12, pady=(2, 10))

        field_label(body, "缓存").pack(fill="x", pady=(0, 4))
        cache_row = tk.Frame(body, bg=COLORS["bg"])
        cache_row.pack(fill="x", pady=(0, 12))
        self.cache_label = tk.Label(
            cache_row,
            text=f"接口缓存占用：{human_size(cache_size())}",
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=font(9),
        )
        self.cache_label.pack(side="left")
        ttk.Button(
            cache_row, text="清除缓存", style="Ghost.TButton", command=self._clear_cache
        ).pack(side="right")

        info = Card(body)
        info.pack(fill="x")
        tk.Label(
            info,
            text="提示",
            bg=COLORS["card"],
            fg=COLORS["accent"],
            font=font(9, "bold"),
            anchor="w",
        ).pack(fill="x", padx=12, pady=(10, 2))
        tk.Label(
            info,
            text="修改设置后，下次扫描或下载时生效。覆盖同名文件不会删除其他模组，"
            "但可能造成同一模组重复，请谨慎开启。",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=font(8),
            anchor="w",
            justify="left",
            wraplength=520,
        ).pack(fill="x", padx=12, pady=(0, 10))

        ttk.Button(self.footer, text="取消", style="TButton", command=self._close).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(self.footer, text="保存设置", style="Accent.TButton", command=self._save).pack(
            side="right"
        )

    def _toggle_key(self) -> None:
        self.key_entry.configure(show="" if self.show_key.get() else "•")

    def _clear_cache(self) -> None:
        removed = clear_cache()
        self.cache_label.configure(text=f"接口缓存占用：{human_size(cache_size())}")
        if self.on_log:
            self.on_log(f"已清除 {removed} 个缓存文件")

    def _save(self) -> None:
        self.config.curseforge_api_key = self.key_var.get().strip()
        self.config.use_cfwidget = bool(self.cfwidget_var.get())
        self.config.overwrite = bool(self.overwrite_var.get())
        self.config.auto_open_folder = bool(self.auto_open_var.get())
        self.config.save()
        if self.on_save:
            self.on_save()
        self._close()


class HelpDialog(BaseDialog):
    def __init__(self, parent: tk.Misc):
        super().__init__(parent, "使用说明", 760, 650, resizable=True)
        text = tk.Text(
            self.body,
            bg=COLORS["card"],
            fg=COLORS["text"],
            relief="flat",
            bd=0,
            wrap="word",
            font=font(9),
            padx=18,
            pady=14,
            spacing1=2,
            spacing3=5,
            cursor="arrow",
        )
        scroll = ttk.Scrollbar(self.body, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)

        h = {"font": font(11, "bold"), "foreground": COLORS["accent"], "spacing1": 12}
        h2 = {"font": font(9, "bold"), "foreground": COLORS["text"], "spacing1": 8}
        muted = {"foreground": COLORS["muted"]}
        code = {"font": font(8, family="Consolas"), "foreground": COLORS["blue"]}
        text.tag_configure("h", **h)
        text.tag_configure("h2", **h2)
        text.tag_configure("muted", **muted)
        text.tag_configure("code", **code)

        def add(value: str, tag: str | None = None) -> None:
            text.insert("end", value, tag or ())

        add(f"{APP_DISPLAY_NAME} {APP_VERSION}\n", "h")
        add("把源实例中的模组，按目标实例的 Minecraft 版本和加载器重新匹配并下载。\n", "muted")

        add("1. 选择两个实例\n", "h2")
        add(
            "源实例：要迁移模组的旧版本；目标实例：要把模组放进去的新版本。\n"
            "支持 .minecraft 文件夹、versions/<版本> 文件夹、Prism / MultiMC 实例、"
            "CurseForge 实例。程序会自动识别 Minecraft 版本、Fabric / Quilt / Forge / "
            "NeoForge 加载器及 mods 文件夹。\n"
        )
        add(
            "识别错误时，可在实例卡片中手动选择版本和加载器，再点击“重新识别”。\n",
            "muted",
        )

        add("2. 扫描\n", "h2")
        add(
            "扫描会读取每个 jar 的元数据和 SHA1，优先用文件哈希在 Modrinth 精确匹配；"
            "匹配不到时按名称搜索，再到 CurseForge 查找。\n"
        )
        add(
            "状态说明：可迁移（找到目标版本）、已存在（目标实例已有同一模组）、"
            "无目标版本（作者未发布该 MC 版本）、未找到模组、跳过（不是有效模组）、查询失败。\n",
            "muted",
        )

        add("3. 迁移\n", "h2")
        add(
            "点击“开始迁移”后，程序会先列出无法迁移的模组并询问是否跳过；确认后只下载"
            "“可迁移”的模组到目标 mods 文件夹。下载完成后可导出 CSV 报告。\n"
        )
        add(
            "模组依赖不会自动下载；如果启动游戏后提示缺少依赖，请按项目页面说明补充。\n",
            "muted",
        )

        add("4. 常见问题\n", "h2")
        add("• 下载 403：作者关闭了第三方下载，或 CurseForge 需要官方 API Key。\n")
        add("• 搜不到模组：有些模组只发布在整合包或私人渠道，程序会提示并跳过。\n")
        add("• 版本识别失败：在实例卡片中手动指定版本即可。\n")
        add("• 建议迁移前备份目标 mods 文件夹，避免版本混装。\n")

        add("数据位置\n", "h2")
        add("缓存：%LOCALAPPDATA%\\ModMigrator\\cache\n", "code")
        add("设置：%APPDATA%\\ModMigrator\\config.json\n", "code")
        add("日志：%LOCALAPPDATA%\\ModMigrator\\logs\n", "code")

        text.configure(state="disabled")
        ttk.Button(self.footer, text="知道了", style="Accent.TButton", command=self._close).pack(
            side="right"
        )


class MessageDialog(BaseDialog):
    def __init__(self, parent: tk.Misc, title: str, message: str, ok_text: str = "知道了"):
        super().__init__(parent, title, 540, 300, resizable=True)
        tk.Label(
            self.body,
            text=message,
            bg=COLORS["bg"],
            fg=COLORS["text"],
            font=font(9),
            anchor="w",
            justify="left",
            wraplength=480,
        ).pack(fill="both", expand=True, pady=(4, 12))
        ttk.Button(self.footer, text=ok_text, style="Accent.TButton", command=self._close).pack(
            side="right"
        )
