
"""The input row: text field, Send, Listen, Login, Local, Clear."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable


class InputRow(ttk.Frame):
    def __init__(
        self,
        parent: tk.Widget,
        *,
        on_send: Callable[[str], None],
        on_listen: Callable[[], None],
        on_login: Callable[[], None],
        on_clear: Callable[[], None],
        on_cancel_tasks: Callable[[], None],
        on_local_toggle: Callable[[bool], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_send = on_send

        self._entry = ttk.Entry(self, font=("Segoe UI", 12))
        self._entry.grid(row=0, column=0, sticky="ew", padx=(10, 6), pady=8)
        self._entry.bind("<Return>", self._handle_return)

        send_btn = ttk.Button(self, text="Send", command=self._handle_send)
        send_btn.grid(row=0, column=1, padx=4, pady=8)

        listen_btn = ttk.Button(self, text="Listen", command=on_listen)
        listen_btn.grid(row=0, column=2, padx=4, pady=8)

        self._login_btn = ttk.Button(
            self, text="Login as Creator", command=on_login
        )
        self._login_btn.grid(row=0, column=3, padx=4, pady=8)

        self._local_var = tk.BooleanVar(value=False)
        local_check = ttk.Checkbutton(
            self,
            text="Local",
            variable=self._local_var,
            command=(
                lambda: on_local_toggle(self._local_var.get())
                if on_local_toggle is not None
                else None
            ),
        )
        local_check.grid(row=0, column=4, padx=4, pady=8)

        cancel_btn = ttk.Button(
            self, text="Cancel Tasks", command=on_cancel_tasks
        )
        cancel_btn.grid(row=0, column=5, padx=4, pady=8)

        clear_btn = ttk.Button(self, text="Clear", command=on_clear)
        clear_btn.grid(row=0, column=6, padx=(4, 10), pady=8)

        self.columnconfigure(0, weight=1)

    def focus_entry(self) -> None:
        self._entry.focus_set()

    def set_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self._entry.configure(state=state)

    def set_logged_in(self, logged_in: bool) -> None:
        self._login_btn.configure(
            text="Logout (Creator)" if logged_in else "Login as Creator"
        )

    def _handle_return(self, _event) -> None:
        self._handle_send()
        return "break"

    def _handle_send(self) -> None:
        text = self._entry.get().strip()
        if not text:
            return
        self._entry.delete(0, "end")
        self._on_send(text)
