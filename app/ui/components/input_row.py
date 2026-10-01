"""The input row: text field, Send, Listen, Clear."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable


class InputRow(ttk.Frame):
    """Text entry and action buttons.

    The component is deliberately passive: it calls the callbacks it is
    given and knows nothing about the controller.
    """

    def __init__(
        self,
        parent: tk.Widget,
        *,
        on_send: Callable[[str], None],
        on_listen: Callable[[], None],
        on_clear: Callable[[], None],
        on_cancel_tasks: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        self._on_send = on_send

        self._entry = ttk.Entry(self, font=("Segoe UI", 10))
        self._entry.grid(row=0, column=0, sticky="ew", padx=(8, 4), pady=6)
        self._entry.bind("<Return>", self._handle_return)

        send_btn = ttk.Button(self, text="Send", command=self._handle_send)
        send_btn.grid(row=0, column=1, padx=4, pady=6)

        listen_btn = ttk.Button(self, text="Listen", command=on_listen)
        listen_btn.grid(row=0, column=2, padx=4, pady=6)

        cancel_btn = ttk.Button(self, text="Cancel Tasks", command=on_cancel_tasks)
        cancel_btn.grid(row=0, column=3, padx=4, pady=6)

        clear_btn = ttk.Button(self, text="Clear", command=on_clear)
        clear_btn.grid(row=0, column=4, padx=(4, 8), pady=6)

        self.columnconfigure(0, weight=1)

    # -- public API --------------------------------------------------------
    def focus_entry(self) -> None:
        self._entry.focus_set()

    def set_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self._entry.configure(state=state)

    # -- internal ----------------------------------------------------------
    def _handle_return(self, _event) -> None:  # noqa: ANN001
        self._handle_send()
        return "break"

    def _handle_send(self) -> None:
        text = self._entry.get().strip()
        if not text:
            return
        self._entry.delete(0, "end")
        self._on_send(text)