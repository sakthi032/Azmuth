"""Compact status strip: mode badge, task count, subsystem summary."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from app.ui.state import UIState

_SURF0  = "#313244"
_OVL0   = "#6c7086"
_OVL1   = "#7f849c"
_TEXT   = "#cdd6f4"
_GREEN  = "#a6e3a1"
_YELLOW = "#f9e2af"
_ORANGE = "#fab387"
_RED    = "#f38ba8"


class StatusBar(tk.Frame):

    def __init__(self, parent: tk.Widget, state: UIState) -> None:
        super().__init__(parent, bg=_SURF0, height=24)
        self.pack_propagate(False)
        self._state = state

        self._mode_dot = tk.Label(self, text="\u25cf", bg=_SURF0,
                                  fg=_YELLOW, font=("Segoe UI", 8))
        self._mode_dot.pack(side="left", padx=(14, 4), pady=4)

        self._mode_lbl = tk.Label(self, text="RESTRICTED", bg=_SURF0,
                                  fg=_YELLOW, font=("Segoe UI", 9, "bold"))
        self._mode_lbl.pack(side="left", padx=(0, 16), pady=4)

        self._tasks_lbl = tk.Label(self, text="", bg=_SURF0,
                                   fg=_OVL1, font=("Segoe UI", 9))
        self._tasks_lbl.pack(side="left", padx=(0, 16), pady=4)

        self._subsys_lbl = tk.Label(self, text="", bg=_SURF0,
                                    fg=_OVL0, font=("Segoe UI", 8))
        self._subsys_lbl.pack(side="right", padx=(14, 0), pady=4)

        self._unsubscribe = state.subscribe(self._on_state_changed)
        self._on_state_changed(state)

    def destroy(self) -> None:
        try:
            self._unsubscribe()
        except Exception:
            pass
        super().destroy()

    def _on_state_changed(self, state: UIState) -> None:
        mode = state.mode.upper()
        if mode == "CREATOR":
            self._mode_dot.configure(fg=_GREEN)
            self._mode_lbl.configure(text="CREATOR", fg=_GREEN)
        elif mode == "RESTRICTED":
            self._mode_dot.configure(fg=_YELLOW)
            self._mode_lbl.configure(text="RESTRICTED", fg=_YELLOW)
        else:
            self._mode_dot.configure(fg=_OVL0)
            self._mode_lbl.configure(text=mode, fg=_OVL0)

        active = state.active_tasks
        total = len(state.tasks)
        if active:
            self._tasks_lbl.configure(
                text=f"\u26a1 {len(active)} running / {total} total",
                fg=_ORANGE)
        else:
            self._tasks_lbl.configure(
                text=f"{total} task{'s' if total != 1 else ''}",
                fg=_OVL1)

        status = state.status.as_dict()
        non_online = [
            f"{n.upper()}={v}"
            for n, v in status.items()
            if v not in ("online", "unknown")
        ]
        summary = "  \u00b7  ".join(non_online) if non_online else "all systems nominal"
        self._subsys_lbl.configure(
            text=summary, fg=_RED if non_online else _OVL0)