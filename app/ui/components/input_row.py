"""The input row: text field, Send, Listen, Clear, Cancel Tasks.

Login and Local toggle live in the Sidebar. This row is focused
purely on message input and quick actions.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

_BASE    = "#1e1e2e"
_SURF0   = "#313244"
_SURF1   = "#45475a"
_OVL1    = "#7f849c"
_TEXT    = "#cdd6f4"
_BLUE    = "#89b4fa"
_ORANGE  = "#fab387"


class InputRow(tk.Frame):

    def __init__(
        self,
        parent: tk.Widget,
        *,
        on_send: Callable[[str], None],
        on_listen: Callable[[], None],
        on_clear: Callable[[], None],
        on_cancel_tasks: Callable[[], None],
    ) -> None:
        super().__init__(parent, bg=_BASE)
        self._on_send = on_send

        # ── Entry ──
        self._entry = tk.Entry(
            self, font=("Segoe UI", 12),
            bg=_SURF0, fg=_TEXT,
            insertbackground=_TEXT,
            selectbackground=_SURF1,
            selectforeground=_TEXT,
            relief="flat", bd=0,
            highlightthickness=1,
            highlightcolor=_BLUE,
            highlightbackground=_SURF0,
        )
        self._entry.pack(side="left", fill="x", expand=True,
                         padx=(12, 8), pady=6, ipady=6)
        self._entry.bind("<Return>", self._handle_return)

        # ── Send ──
        self._send_btn = tk.Button(
            self, text="Send", command=self._handle_send,
            bg=_BLUE, fg=_BASE, activebackground=_SURF1,
            activeforeground=_TEXT, font=("Segoe UI", 11, "bold"),
            relief="flat", bd=0, cursor="hand2",
            padx=20, pady=6,
        )
        self._send_btn.pack(side="left", padx=(0, 6), pady=10)
        self._send_btn.bind(
            "<Enter>", lambda _e: self._send_btn.configure(bg="#9ec5fe"))
        self._send_btn.bind(
            "<Leave>", lambda _e: self._send_btn.configure(bg=_BLUE))

        # ── Divider ──
        tk.Frame(self, bg=_SURF0, width=1).pack(
            side="left", fill="y", padx=4, pady=14)

        # ── Secondary buttons ──
        self._listen_btn = self._ghost_btn(
            "\u266a Listen", on_listen, side="left", padx=2)
        self._clear_btn = self._ghost_btn(
            "Clear", on_clear, side="left", padx=2)

        self._cancel_btn = tk.Button(
            self, text="Cancel Tasks", command=on_cancel_tasks,
            bg=_BASE, fg=_OVL1, activebackground=_SURF0,
            activeforeground=_ORANGE, font=("Segoe UI", 9),
            relief="flat", bd=0, cursor="hand2",
            padx=10, pady=4,
        )
        self._cancel_btn.pack(side="left", padx=(2, 10), pady=10)
        self._cancel_btn.bind(
            "<Enter>", lambda _e: self._cancel_btn.configure(
                bg=_SURF0, fg=_ORANGE))
        self._cancel_btn.bind(
            "<Leave>", lambda _e: self._cancel_btn.configure(
                bg=_BASE, fg=_OVL1))

    def _ghost_btn(self, text, cmd, *, side="left", padx=2):
        btn = tk.Button(
            self, text=text, command=cmd,
            bg=_BASE, fg=_OVL1, activebackground=_SURF0,
            activeforeground=_TEXT, font=("Segoe UI", 9),
            relief="flat", bd=0, cursor="hand2",
            padx=10, pady=4,
        )
        btn.pack(side=side, padx=padx, pady=10)
        btn.bind("<Enter>", lambda _e: btn.configure(bg=_SURF0, fg=_TEXT))
        btn.bind("<Leave>", lambda _e: btn.configure(bg=_BASE, fg=_OVL1))
        return btn

    def focus_entry(self) -> None:
        self._entry.focus_set()

    def set_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self._entry.configure(state=state)
        self._send_btn.configure(state=state)

    def _handle_return(self, _event) -> None:
        self._handle_send()
        return "break"

    def _handle_send(self) -> None:
        text = self._entry.get().strip()
        if not text:
            return
        self._entry.delete(0, "end")
        self._on_send(text)