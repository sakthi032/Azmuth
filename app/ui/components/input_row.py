"""The input row: attach, text field, Send, Stop, Listen, Clear, Cancel Tasks.

Login and Local toggle live in the Sidebar. This row is focused
purely on message input and quick actions.
"""

from __future__ import annotations

import tkinter as tk
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
        on_stop: Callable[[], None] | None = None,
        on_attach: Callable[[], None] | None = None,
        on_ask_screen: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent, bg=_BASE)
        self._on_send = on_send

        # ── Attach ──
        self._attach_btn = tk.Button(
            self, text="\U0001F4CE",
            command=(on_attach or (lambda: None)),
            bg=_BASE, fg=_OVL1,
            activebackground=_SURF0, activeforeground=_BLUE,
            font=("Segoe UI Emoji", 14),
            relief="flat", bd=0, cursor="hand2",
            padx=8, pady=2,
        )
        self._attach_btn.pack(side="left", padx=(8, 0), pady=10)
        self._attach_btn.bind(
            "<Enter>",
            lambda _e: self._attach_btn.configure(bg=_SURF0, fg=_BLUE))
        self._attach_btn.bind(
            "<Leave>",
            lambda _e: self._attach_btn.configure(bg=_BASE, fg=_OVL1))

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
                         padx=(6, 8), pady=6, ipady=6)
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

        # ── Stop ──
        self._stop_btn = tk.Button(
            self, text="\u25a0 Stop",
            command=(on_stop or (lambda: None)),
            bg=_SURF0, fg=_TEXT,
            activebackground="#f38ba8", activeforeground=_BASE,
            font=("Segoe UI", 10, "bold"),
            relief="flat", bd=0, cursor="hand2",
            padx=12, pady=6, state="disabled",
        )
        self._stop_btn.pack(side="left", padx=(0, 6), pady=10)

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

        self._screen_btn = tk.Button(
            self, text="\U0001F5A5 Screen",
            command=(on_ask_screen or (lambda: None)),
            bg=_BASE, fg=_OVL1, activebackground=_SURF0,
            activeforeground=_BLUE, font=("Segoe UI Emoji", 10),
            relief="flat", bd=0, cursor="hand2",
            padx=10, pady=4,
        )
        self._screen_btn.pack(side="left", padx=(2, 10), pady=10)
        self._screen_btn.bind(
            "<Enter>",
            lambda _e: self._screen_btn.configure(bg=_SURF0, fg=_BLUE))
        self._screen_btn.bind(
            "<Leave>",
            lambda _e: self._screen_btn.configure(bg=_BASE, fg=_OVL1))

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

    def set_streaming(self, streaming: bool) -> None:
        if streaming:
            self._send_btn.configure(state="disabled")
            self._entry.configure(state="disabled")
            self._stop_btn.configure(state="normal", text="\u25a0 Stop")
        else:
            self._send_btn.configure(state="normal")
            self._entry.configure(state="normal")
            self._stop_btn.configure(state="disabled", text="\u25a0 Stop")

    def set_stopping(self) -> None:
        """Visual state while the cancel is being processed."""
        self._stop_btn.configure(state="disabled", text="\u25a0 Stopping")
        self._send_btn.configure(state="disabled")
        self._entry.configure(state="disabled")

    def set_text(self, text: str) -> None:
        """Replace the entry contents (used after attaching a file)."""
        self._entry.delete(0, "end")
        self._entry.insert(0, text)

    def _handle_return(self, _event) -> None:
        self._handle_send()
        return "break"

    def _handle_send(self) -> None:
        text = self._entry.get().strip()
        self._entry.delete(0, "end")
        self._on_send(text)