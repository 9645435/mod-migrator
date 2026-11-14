from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk


COLORS = {
    "bg": "#0B0F17",
    "bg_alt": "#0E141E",
    "card": "#121926",
    "card_hover": "#172233",
    "border": "#223047",
    "border_soft": "#1A2636",
    "text": "#E6EDF7",
    "muted": "#8B9BB4",
    "dim": "#5F7088",
    "accent": "#3DDC97",
    "accent_hover": "#5CE8AD",
    "accent_dark": "#1E6E50",
    "blue": "#6AA8FF",
    "orange": "#F2B45C",
    "red": "#FF6B6B",
    "purple": "#B28CFF",
    "input": "#0E1621",
    "selection": "#1E3A34",
}

STATUS_COLORS = {
    "ready": COLORS["accent"],
    "downloaded": COLORS["accent"],
    "already": COLORS["blue"],
    "no_target": COLORS["orange"],
    "not_found": COLORS["orange"],
    "unsupported": COLORS["dim"],
    "error": COLORS["red"],
    "download_failed": COLORS["red"],
    "cancelled": COLORS["dim"],
}

FONT_FAMILY = "Microsoft YaHei UI"
MONO_FAMILY = "Consolas"
BASE_SIZE = 10


def _pick_font(root: tk.Misc) -> None:
    global FONT_FAMILY, MONO_FAMILY
    families = set(tkfont.families(root))
    for name in ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Segoe UI"):
        if name in families:
            FONT_FAMILY = name
            break
    for name in ("Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono"):
        if name in families:
            MONO_FAMILY = name
            break


def font(size: int = BASE_SIZE, weight: str = "normal", family: str | None = None) -> tuple:
    return (family or FONT_FAMILY, size, weight)


def setup_theme(root: tk.Tk) -> ttk.Style:
    _pick_font(root)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(bg=COLORS["bg"])
    root.option_add("*Font", font())
    root.option_add("*tearOff", False)
    root.option_add("*TCombobox*Listbox.background", COLORS["card"])
    root.option_add("*TCombobox*Listbox.foreground", COLORS["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", COLORS["selection"])
    root.option_add("*TCombobox*Listbox.selectForeground", COLORS["text"])

    style.configure(".", background=COLORS["bg"], foreground=COLORS["text"], font=font())
    style.configure("TFrame", background=COLORS["bg"])
    style.configure("Alt.TFrame", background=COLORS["bg_alt"])
    style.configure("Card.TFrame", background=COLORS["card"])
    style.configure("Inner.TFrame", background=COLORS["card"])

    style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
    style.configure("Card.TLabel", background=COLORS["card"], foreground=COLORS["text"])
    style.configure(
        "Muted.TLabel", background=COLORS["bg"], foreground=COLORS["muted"], font=font(9)
    )
    style.configure(
        "CardMuted.TLabel", background=COLORS["card"], foreground=COLORS["muted"], font=font(9)
    )
    style.configure(
        "Dim.TLabel", background=COLORS["bg"], foreground=COLORS["dim"], font=font(9)
    )
    style.configure(
        "Title.TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=font(20, "bold")
    )
    style.configure(
        "Subtitle.TLabel", background=COLORS["bg"], foreground=COLORS["muted"], font=font(9)
    )
    style.configure(
        "CardTitle.TLabel", background=COLORS["card"], foreground=COLORS["text"], font=font(11, "bold")
    )
    style.configure(
        "Section.TLabel", background=COLORS["bg"], foreground=COLORS["muted"], font=font(9, "bold")
    )
    style.configure(
        "DetailTitle.TLabel", background=COLORS["card"], foreground=COLORS["text"], font=font(12, "bold")
    )
    style.configure(
        "Stat.TLabel", background=COLORS["card"], foreground=COLORS["text"], font=font(13, "bold")
    )
    style.configure(
        "StatCaption.TLabel", background=COLORS["card"], foreground=COLORS["muted"], font=font(8)
    )

    style.configure(
        "TButton",
        background=COLORS["card_hover"],
        foreground=COLORS["text"],
        bordercolor=COLORS["border"],
        lightcolor=COLORS["card_hover"],
        darkcolor=COLORS["card_hover"],
        focusthickness=0,
        focuscolor=COLORS["card_hover"],
        padding=(14, 8),
        relief="flat",
        font=font(9),
    )
    style.map(
        "TButton",
        background=[("active", "#1D2A3C"), ("disabled", "#141C28")],
        foreground=[("disabled", COLORS["dim"])],
        bordercolor=[("active", "#2C3D56")],
    )

    style.configure(
        "Accent.TButton",
        background=COLORS["accent"],
        foreground="#06130D",
        bordercolor=COLORS["accent"],
        lightcolor=COLORS["accent"],
        darkcolor=COLORS["accent"],
        padding=(20, 10),
        font=font(10, "bold"),
    )
    style.map(
        "Accent.TButton",
        background=[("active", COLORS["accent_hover"]), ("disabled", "#1B3A2E")],
        foreground=[("disabled", "#6D8C7F")],
        bordercolor=[("active", COLORS["accent_hover"])],
    )

    style.configure(
        "Danger.TButton",
        background=COLORS["red"],
        foreground="#1A0909",
        bordercolor=COLORS["red"],
        padding=(16, 9),
        font=font(9, "bold"),
    )
    style.map(
        "Danger.TButton",
        background=[("active", "#FF8585"), ("disabled", "#3A2028")],
        foreground=[("disabled", "#8A6A70")],
    )

    style.configure(
        "Secondary.TButton",
        background="#182333",
        foreground=COLORS["text"],
        bordercolor=COLORS["border"],
        padding=(15, 9),
        font=font(9),
    )
    style.map("Secondary.TButton", background=[("active", "#203047"), ("disabled", "#141C28")])

    style.configure(
        "Ghost.TButton",
        background=COLORS["card"],
        foreground=COLORS["muted"],
        bordercolor=COLORS["card"],
        padding=(8, 6),
        font=font(9),
    )
    style.map(
        "Ghost.TButton",
        background=[("active", COLORS["card_hover"])],
        foreground=[("active", COLORS["text"])],
    )

    style.configure(
        "Link.TButton",
        background=COLORS["card"],
        foreground=COLORS["blue"],
        bordercolor=COLORS["card"],
        padding=(4, 2),
        font=font(9),
    )
    style.map("Link.TButton", foreground=[("active", "#9CC4FF")], background=[("active", COLORS["card"])])

    style.configure(
        "TEntry",
        fieldbackground=COLORS["input"],
        foreground=COLORS["text"],
        insertcolor=COLORS["text"],
        bordercolor=COLORS["border"],
        lightcolor=COLORS["border"],
        darkcolor=COLORS["border"],
        padding=(10, 8),
        relief="flat",
    )
    style.map(
        "TEntry",
        bordercolor=[("focus", COLORS["accent_dark"])],
        lightcolor=[("focus", COLORS["accent_dark"])],
        darkcolor=[("focus", COLORS["accent_dark"])],
    )

    style.configure(
        "TCombobox",
        fieldbackground=COLORS["input"],
        background=COLORS["input"],
        foreground=COLORS["text"],
        arrowcolor=COLORS["muted"],
        bordercolor=COLORS["border"],
        lightcolor=COLORS["border"],
        darkcolor=COLORS["border"],
        padding=(8, 5),
        relief="flat",
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", COLORS["input"]), ("disabled", COLORS["bg_alt"])],
        foreground=[("disabled", COLORS["dim"])],
        bordercolor=[("focus", COLORS["accent_dark"])],
    )

    style.configure(
        "Treeview",
        background=COLORS["card"],
        fieldbackground=COLORS["card"],
        foreground=COLORS["text"],
        rowheight=30,
        borderwidth=0,
        relief="flat",
        font=font(9),
    )
    style.configure(
        "Treeview.Heading",
        background="#172131",
        foreground=COLORS["muted"],
        relief="flat",
        borderwidth=0,
        padding=(8, 8),
        font=font(8, "bold"),
    )
    style.map(
        "Treeview.Heading",
        background=[("active", "#1C283A")],
        foreground=[("active", COLORS["text"])],
    )
    style.map(
        "Treeview",
        background=[("selected", COLORS["selection"])],
        foreground=[("selected", COLORS["text"])],
    )
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    style.configure(
        "TProgressbar",
        background=COLORS["accent"],
        troughcolor="#101825",
        bordercolor="#101825",
        lightcolor=COLORS["accent"],
        darkcolor=COLORS["accent"],
        thickness=6,
    )
    style.configure(
        "TCheckbutton",
        background=COLORS["card"],
        foreground=COLORS["text"],
        focuscolor=COLORS["card"],
        font=font(9),
    )
    style.map(
        "TCheckbutton",
        background=[("active", COLORS["card"])],
        indicatorcolor=[("selected", COLORS["accent"]), ("!selected", COLORS["input"])],
    )
    style.configure(
        "TScrollbar",
        background=COLORS["card_hover"],
        troughcolor=COLORS["bg_alt"],
        bordercolor=COLORS["bg_alt"],
        arrowcolor=COLORS["muted"],
        relief="flat",
    )
    style.map("TScrollbar", background=[("active", "#2A3B52")])
    style.configure("TSeparator", background=COLORS["border_soft"])

    return style


def center_window(window: tk.Misc, width: int, height: int, parent: tk.Misc | None = None) -> None:
    window.update_idletasks()
    if parent is not None and parent.winfo_exists():
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        x = px + max(0, (pw - width) // 2)
        y = py + max(0, (ph - height) // 2)
    else:
        x = (window.winfo_screenwidth() - width) // 2
        y = (window.winfo_screenheight() - height) // 2
    window.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
