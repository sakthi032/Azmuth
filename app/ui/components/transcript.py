"""The conversation view.

Read-only. Subscribes to state changes, redraws itself. Only file in
``app.ui.components`` that touches tkinter.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from app.ui.state import Speaker, UIState

_TAG_BY_SPEAKER = {
    Speaker.USER: "user",
    Speaker.AZMUTH: "azmuth",
    Speaker.SYSTEM: "system",
}


class TranscriptView(ttk.Frame):
    """Scrollable conversation transcript."""

    def __init__(self, parent: tk.Widget, state: UIState) -> None:
        super().__init__(parent)
        self._state = state
        self._last_rendered_id = 0

        self._text = tk.Text(
            self,
            wrap="word",
            state="disabled",
            background="#1e1e1e",
            foreground="#e6e6e6",
            insertbackground="#e6e6e6",
            borderwidth=0,
            highlightthickness=0,
            font=("Segoe UI", 10),
        )
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self._text.yview)
        self._text.configure(yscrollcommand=scrollbar.set)

        self._text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self._configure_tags()
        self._render_all()
        self._unsubscribe = state.subscribe(self._on_state_changed)

    # -- lifecycle ---------------------------------------------------------
    def destroy(self) -> None:  # noqa: D401
        try:
            self._unsubscribe()
        except Exception:  # noqa: BLE001
            pass
        super().destroy()

    # -- rendering ---------------------------------------------------------
    def _configure_tags(self) -> None:
        self._text.tag_configure("user", foreground="#7fb3ff", spacing1=6, spacing3=2)
        self._text.tag_configure("azmuth", foreground="#a5e075", spacing1=6, spacing3=2)
        self._text.tag_configure("system", foreground="#aaaaaa", spacing1=4, spacing3=2)
        self._text.tag_configure(
            "system-error", foreground="#ff7b72", spacing1=4, spacing3=2
        )
        self._text.tag_configure("speaker", font=("Segoe UI", 9, "bold"))
        self._text.tag_configure("meta", foreground="#888888", font=("Segoe UI", 8))

    def _render_all(self) -> None:
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._last_rendered_id = 0
        self._text.configure(state="disabled")
        for entry in self._state.conversation:
            self._render_entry(entry)
            self._last_rendered_id = entry.id

    def _render_entry(self, entry) -> None:  # noqa: ANN001
        label = {
            Speaker.USER: "You",
            Speaker.AZMUTH: "Azmuth",
            Speaker.SYSTEM: "System",
        }[entry.speaker]
        tag = _TAG_BY_SPEAKER[entry.speaker]
        if entry.level.value == "error":
            tag = "system-error"

        self._text.configure(state="normal")
        self._text.insert("end", f"{label}\n", (tag, "speaker"))
        self._text.insert("end", f"{entry.text}\n", (tag,))
        meta_parts = [entry.created_at.strftime("%H:%M:%S")]
        if entry.language:
            meta_parts.append(entry.language)
        meta_parts.append(entry.source)
        self._text.insert("end", "  ".join(meta_parts) + "\n\n", ("meta",))
        self._text.configure(state="disabled")
        self._text.see("end")

    def _on_state_changed(self, state: UIState) -> None:
        # Only append entries we have not rendered yet — cheap and avoids
        # a full redraw on every state tick.
        new_entries = [e for e in state.conversation if e.id > self._last_rendered_id]
        if not new_entries:
            return
        for entry in new_entries:
            self._render_entry(entry)
            self._last_rendered_id = entry.id