"""Centralized theme and color constants for the Azmuth desktop UI.

Catppuccin Mocha dark palette applied consistently across every widget.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

CRUST    = "#11111b"
MANTLE   = "#181825"
BASE     = "#1e1e2e"
SURFACE0 = "#313244"
SURFACE1 = "#45475a"
SURFACE2 = "#585b70"
OVERLAY0 = "#6c7086"
OVERLAY1 = "#7f849c"
SUBTEXT1 = "#bac2de"
SUBTEXT0 = "#a6adc8"
TEXT     = "#cdd6f4"

BLUE     = "#89b4fa"
GREEN    = "#a6e3a1"
TEAL     = "#94e2d5"
YELLOW   = "#f9e2af"
ORANGE   = "#fab387"
RED      = "#f38ba8"
PINK     = "#f5c2e7"
PURPLE   = "#cba6f7"
LAVENDER = "#b4befe"

FONT         = ("Segoe UI", 11)
FONT_BOLD    = ("Segoe UI", 11, "bold")
FONT_SMALL   = ("Segoe UI", 9)
FONT_SMALL_B = ("Segoe UI", 9, "bold")
FONT_TINY    = ("Segoe UI", 8)
FONT_MONO    = ("Consolas", 10)
FONT_TITLE   = ("Segoe UI", 14, "bold")
FONT_ENTRY   = ("Segoe UI", 12)


def apply_theme(root: tk.Tk) -> ttk.Style:
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    style.configure(".", background=BASE, foreground=TEXT,
                    fieldbackground=SURFACE0, borderwidth=0, font=FONT)
    style.map(".", background=[("active", SURFACE0)],
              foreground=[("disabled", OVERLAY0)])

    style.configure("TFrame", background=BASE)
    style.configure("TLabel", background=BASE, foreground=TEXT, font=FONT)
    style.configure("Dim.TLabel", foreground=OVERLAY0, font=FONT_SMALL)
    style.configure("Subtext.TLabel", foreground=SUBTEXT0, font=FONT_SMALL)
    style.configure("Accent.TLabel", foreground=BLUE, font=FONT_BOLD)
    style.configure("Title.TLabel", font=FONT_TITLE)

    style.configure("TButton", background=SURFACE0, foreground=TEXT,
                    borderwidth=0, focuscolor=BASE,
                    padding=(12, 6), font=FONT)
    style.map("TButton",
              background=[("active", SURFACE1), ("pressed", SURFACE2), ("disabled", MANTLE)],
              foreground=[("disabled", OVERLAY0)])

    style.configure("Primary.TButton", background=BLUE, foreground=CRUST,
                    padding=(18, 8), font=FONT_BOLD)
    style.map("Primary.TButton",
              background=[("active", LAVENDER), ("pressed", SURFACE2), ("disabled", SURFACE1)],
              foreground=[("disabled", OVERLAY0)])

    style.configure("Ghost.TButton", background=BASE, foreground=OVERLAY1,
                    padding=(8, 4), font=FONT_SMALL)
    style.map("Ghost.TButton",
              background=[("active", SURFACE0)],
              foreground=[("active", TEXT)])

    style.configure("Danger.TButton", background=SURFACE0, foreground=RED,
                    padding=(10, 5), font=FONT_SMALL_B)
    style.map("Danger.TButton", background=[("active", "#3a1a2a")])

    style.configure("TEntry", fieldbackground=SURFACE0, foreground=TEXT,
                    insertcolor=TEXT, borderwidth=0, padding=(12, 8))
    style.map("TEntry",
              fieldbackground=[("focus", SURFACE1)],
              foreground=[("disabled", OVERLAY0)])

    style.configure("TCheckbutton", background=BASE, foreground=TEXT)
    style.map("TCheckbutton", background=[("active", SURFACE0)])

    style.configure("TScrollbar", background=SURFACE0, troughcolor=MANTLE,
                    borderwidth=0, arrowsize=13)
    style.map("TScrollbar", background=[("active", SURFACE1)])

    style.configure("TSeparator", background=SURFACE0)
    root.configure(bg=BASE)
    return style