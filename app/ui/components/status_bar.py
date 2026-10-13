
"""The status strip: mode, subsystems, active tasks."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from app.ui.state import UIState


class StatusBar(ttk.Frame):
    def __init__(self, parent: tk.Widget, state: UIState) -> None:
        super().__init__(parent)
        self._state = state

        self._mode_var = tk.StringVar(value="restricted")
        self._tasks_var = tk.StringVar(value="tasks: 0")
        self._subsystems_var = tk.StringVar(value="")

        self._mode_label = ttk.Label(
            self, textvariable=self._mode_var,
            font=("Segoe UI", 10, "bold"), foreground="#cba6f7",
        )
        self._tasks_label = ttk.Label(
            self, textvariable=self._tasks_var,
            font=("Segoe UI", 10),
        )
        self._subsystems_label = ttk.Label(
            self, textvariable=self._subsystems_var,
            font=("Segoe UI", 9), foreground="#6c7086",
        )

        self._mode_label.grid(row=0, column=0, padx=(10, 14), pady=4, sticky="w")
        self._tasks_label.grid(row=0, column=1, padx=(0, 14), pady=4, sticky="w")
        self._subsystems_label.grid(row=0, column=2, padx=0, pady=4, sticky="w")
        self.columnconfigure(2, weight=1)

        self._unsubscribe = state.subscribe(self._on_state_changed)
        self._on_state_changed(state)

    def destroy(self) -> None:
        try:
            self._unsubscribe()
        except Exception:  # noqa: BLE001
            pass
        super().destroy()

    def _on_state_changed(self, state: UIState) -> None:
        self._mode_var.set(state.mode.upper())

        active = state.active_tasks
        total = len(state.tasks)
        if active:
            self._tasks_var.set(f"tasks: {len(active)} running / {total}")
        else:
            self._tasks_var.set(f"tasks: {total}")

        status = state.status.as_dict()
        non_online = [
            f"{name.upper()}={value}"
            for name, value in status.items()
            if value not in ("online", "unknown")
        ]
        self._subsystems_var.set(
            "  ".join(non_online) if non_online else "all subsystems online"
        )
