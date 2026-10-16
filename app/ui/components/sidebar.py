"""Left sidebar: agent identity, mode badge, active tasks, settings."""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from app.ui.state import UIState

_MANTLE  = "#181825"
_SURF0   = "#313244"
_SURF1   = "#45475a"
_OVL0    = "#6c7086"
_OVL1    = "#7f849c"
_SUB0    = "#a6adc8"
_TEXT    = "#cdd6f4"
_BLUE    = "#89b4fa"
_GREEN   = "#a6e3a1"
_YELLOW  = "#f9e2af"
_ORANGE  = "#fab387"
_CRUST   = "#11111b"


class Sidebar(tk.Frame):

    WIDTH = 240

    def __init__(
        self,
        parent: tk.Widget,
        state: UIState,
        *,
        on_login: Callable[[], None],
        on_local_toggle: Callable[[bool], None] | None = None,
        on_new_chat: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent, bg=_MANTLE, width=self.WIDTH)
        self._state = state
        self._on_login = on_login
        self._on_local_toggle = on_local_toggle
        self._on_new_chat = on_new_chat
        self._logged_in = False

        self.pack_propagate(False)
        self.grid_propagate(False)

        self._build_header()
        self._build_mode_badge()
        self._sep(pady=(12, 0))
        self._build_tasks()
        self._sep(pady=(12, 0))
        self._build_settings()

        self._unsubscribe = state.subscribe(self._on_state_changed)
        self._on_state_changed(state)

    def destroy(self) -> None:
        try:
            self._unsubscribe()
        except Exception:
            pass
        super().destroy()

    def _sep(self, *, pady=(12, 12)) -> None:
        tk.Frame(self, bg=_SURF0, height=1).pack(fill="x", padx=18, pady=pady)

    def _build_header(self) -> None:
        frm = tk.Frame(self, bg=_MANTLE)
        frm.pack(fill="x", padx=18, pady=(22, 6))

        av = tk.Canvas(frm, width=40, height=40,
                       bg=_MANTLE, highlightthickness=0)
        av.pack(side="left", padx=(0, 12))
        av.create_oval(2, 2, 38, 38, fill=_BLUE, outline="")
        av.create_text(20, 20, text="A", fill=_CRUST,
                       font=("Segoe UI", 16, "bold"))

        info = tk.Frame(frm, bg=_MANTLE)
        info.pack(side="left", fill="y")
        tk.Label(info, text="Azmuth", bg=_MANTLE, fg=_TEXT,
                 font=("Segoe UI", 14, "bold")).pack(anchor="w")
        self._status_lbl = tk.Label(info, text="Ready", bg=_MANTLE,
                                    fg=_GREEN, font=("Segoe UI", 8))
        self._status_lbl.pack(anchor="w")

        if self._on_new_chat:
            btn = tk.Label(frm, text="+", bg=_MANTLE, fg=_OVL1,
                           font=("Segoe UI", 18, "bold"), cursor="hand2")
            btn.pack(side="right", padx=(8, 0))
            btn.bind("<Button-1>", lambda _e: self._on_new_chat())
            btn.bind("<Enter>", lambda _e: btn.configure(fg=_TEXT))
            btn.bind("<Leave>", lambda _e: btn.configure(fg=_OVL1))

    def _build_mode_badge(self) -> None:
        frm = tk.Frame(self, bg=_MANTLE)
        frm.pack(fill="x", padx=18, pady=(4, 0))

        self._mode_dot = tk.Label(frm, text="\u25cf", bg=_MANTLE,
                                  fg=_YELLOW, font=("Segoe UI", 9))
        self._mode_dot.pack(side="left", padx=(0, 6))

        self._mode_lbl = tk.Label(frm, text="RESTRICTED", bg=_MANTLE,
                                  fg=_YELLOW, font=("Segoe UI", 9, "bold"))
        self._mode_lbl.pack(side="left")

    def _build_tasks(self) -> None:
        hdr = tk.Frame(self, bg=_MANTLE)
        hdr.pack(fill="x", padx=18, pady=(0, 6))
        tk.Label(hdr, text="ACTIVE TASKS", bg=_MANTLE, fg=_OVL0,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        self._tasks_cnt = tk.Label(hdr, text="0", bg=_MANTLE, fg=_OVL0,
                                   font=("Segoe UI", 8))
        self._tasks_cnt.pack(side="right")
        self._tasks_frm = tk.Frame(self, bg=_MANTLE)
        self._tasks_frm.pack(fill="both", expand=True, padx=18)

    def _render_tasks(self, tasks) -> None:
        for w in self._tasks_frm.winfo_children():
            w.destroy()
        active = [t for t in tasks if t.is_active]
        self._tasks_cnt.configure(text=str(len(active)))
        if not active:
            tk.Label(self._tasks_frm, text="No active tasks",
                     bg=_MANTLE, fg=_OVL0,
                     font=("Segoe UI", 8)).pack(anchor="w")
            return
        for task in active[:6]:
            row = tk.Frame(self._tasks_frm, bg=_MANTLE)
            row.pack(fill="x", pady=2)
            tk.Label(row, text="\u25cb", bg=_MANTLE, fg=_ORANGE,
                     font=("Segoe UI", 8)).pack(side="left", padx=(0, 8))
            tk.Label(row, text=task.name, bg=_MANTLE, fg=_SUB0,
                     font=("Segoe UI", 9)).pack(side="left")
            bar = tk.Frame(row, bg=_SURF0, height=3, width=44)
            bar.pack(side="right", padx=(4, 0))
            bar.pack_propagate(False)
            if task.progress is not None:
                w = max(1, int(44 * min(task.progress, 1.0)))
                tk.Frame(bar, bg=_ORANGE, height=3, width=w).pack(side="left")

    def _build_settings(self) -> None:
        sett = tk.Frame(self, bg=_MANTLE)
        sett.pack(fill="x", padx=18, pady=(0, 18), side="bottom")

        if self._on_local_toggle is not None:
            lf = tk.Frame(sett, bg=_MANTLE)
            lf.pack(fill="x", pady=(0, 10))
            tk.Label(lf, text="Local Mode (Ollama)", bg=_MANTLE,
                     fg=_SUB0, font=("Segoe UI", 9)).pack(side="left")
            self._local_var = tk.BooleanVar(value=False)
            tk.Checkbutton(
                lf, variable=self._local_var,
                bg=_MANTLE, fg=_TEXT, selectcolor=_SURF0,
                activebackground=_MANTLE, activeforeground=_TEXT,
                highlightthickness=0, bd=0,
                command=lambda: self._on_local_toggle(self._local_var.get()),
            ).pack(side="right")

        self._login_btn = tk.Button(
            sett, text="  Login as Creator  ", command=self._on_login,
            bg=_SURF0, fg=_TEXT, activebackground=_SURF1,
            activeforeground=_TEXT, font=("Segoe UI", 9, "bold"),
            bd=0, relief="flat", cursor="hand2", pady=7)
        self._login_btn.pack(fill="x")
        self._login_btn.bind(
            "<Enter>",
            lambda _e: self._login_btn.configure(
                bg=_SURF1 if not self._logged_in else "#3a2a1a"))
        self._login_btn.bind(
            "<Leave>",
            lambda _e: self._login_btn.configure(bg=_SURF0))

    def set_logged_in(self, logged_in: bool) -> None:
        self._logged_in = logged_in
        if logged_in:
            self._login_btn.configure(text="  Logout (Creator)  ", fg=_ORANGE)
        else:
            self._login_btn.configure(text="  Login as Creator  ", fg=_TEXT)

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
        self._render_tasks(state.tasks)
        active = state.active_tasks
        if active:
            self._status_lbl.configure(text="Working...", fg=_ORANGE)
        else:
            self._status_lbl.configure(text="Ready", fg=_GREEN)