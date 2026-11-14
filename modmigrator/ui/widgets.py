from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from .theme import COLORS, font


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def mix(color: str, base: str, amount: float) -> str:
    """Blend color into base; amount 0 = base, 1 = color."""
    r1, g1, b1 = _hex_to_rgb(color)
    r2, g2, b2 = _hex_to_rgb(base)
    r = round(r2 + (r1 - r2) * amount)
    g = round(g2 + (g1 - g2) * amount)
    b = round(b2 + (b1 - b2) * amount)
    return f"#{r:02x}{g:02x}{b:02x}"


class Card(tk.Frame):
    def __init__(self, parent: tk.Misc, **kwargs):
        kwargs.setdefault("bg", COLORS["card"])
        kwargs.setdefault("highlightbackground", COLORS["border"])
        kwargs.setdefault("highlightthickness", 1)
        kwargs.setdefault("bd", 0)
        super().__init__(parent, **kwargs)


class StatusPill(tk.Label):
    def __init__(self, parent: tk.Misc, text: str = "", color: str | None = None):
        color = color or COLORS["muted"]
        super().__init__(
            parent,
            text=text,
            bg=mix(color, COLORS["card"], 0.16),
            fg=color,
            font=font(8, "bold"),
            padx=8,
            pady=3,
            bd=0,
        )

    def set(self, text: str, color: str) -> None:
        self.configure(text=text, bg=mix(color, COLORS["card"], 0.16), fg=color)


class StatChip(tk.Frame):
    def __init__(self, parent: tk.Misc, caption: str, color: str = COLORS["text"]):
        super().__init__(parent, bg=COLORS["card"])
        self.value = tk.Label(
            self,
            text="0",
            bg=COLORS["card"],
            fg=color,
            font=font(15, "bold"),
            bd=0,
        )
        self.value.pack(anchor="w")
        self.caption = tk.Label(
            self,
            text=caption,
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=font(8),
            bd=0,
        )
        self.caption.pack(anchor="w", pady=(1, 0))

    def set(self, value: int | str, color: str | None = None) -> None:
        self.value.configure(text=str(value))
        if color:
            self.value.configure(fg=color)


class LinkButton(ttk.Button):
    def __init__(self, parent: tk.Misc, text: str, command: Callable[[], None]):
        super().__init__(parent, text=text, style="Link.TButton", command=command, cursor="hand2")


class Tooltip:
    def __init__(self, widget: tk.Widget, text: str | Callable[[], str], delay: int = 450):
        self.widget = widget
        self._text = text
        self.delay = delay
        self._after_id: str | None = None
        self._window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    @property
    def text(self) -> str:
        value = self._text() if callable(self._text) else self._text
        return value or ""

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _cancel(self) -> None:
        if self._after_id:
            try:
                self.widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def _show(self) -> None:
        if self._window or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        except tk.TclError:
            return
        win = tk.Toplevel(self.widget)
        win.wm_overrideredirect(True)
        win.wm_geometry(f"+{x}+{y}")
        win.configure(bg=COLORS["border"])
        label = tk.Label(
            win,
            text=self.text,
            bg=COLORS["card_hover"],
            fg=COLORS["text"],
            font=font(8),
            padx=8,
            pady=5,
            justify="left",
        )
        label.pack(padx=1, pady=1)
        self._window = win

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None


class ScrollFrame(tk.Frame):
    """A vertically scrollable frame with a dark scrollbar."""

    def __init__(self, parent: tk.Misc, bg: str | None = None):
        bg = bg or COLORS["bg"]
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind("<Enter>", lambda _e: self._bind_wheel())
        self.canvas.bind("<Leave>", lambda _e: self._unbind_wheel())

    def _on_inner(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _bind_wheel(self) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")


def field_label(parent: tk.Misc, text: str, bg: str | None = None) -> tk.Label:
    return tk.Label(
        parent,
        text=text,
        bg=bg or COLORS["card"],
        fg=COLORS["muted"],
        font=font(8, "bold"),
        anchor="w",
        bd=0,
    )


def hint_label(parent: tk.Misc, text: str, bg: str | None = None) -> tk.Label:
    return tk.Label(
        parent,
        text=text,
        bg=bg or COLORS["card"],
        fg=COLORS["dim"],
        font=font(8),
        anchor="w",
        justify="left",
        bd=0,
    )
