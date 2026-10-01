"""The tkinter desktop window.

Guarded import: on a Python build without tkinter (some minimal Linux
containers), :func:`build_app` raises a clear error instead of failing
at import time. On Windows and standard Python installs it just works.

The app is intentionally minimal:

* transcript above (scrollable),
* status strip in the middle,
* input row at the bottom.

Wiring to the backend goes through :class:`UIController`, and any
background work is scheduled with ``root.after`` so tkinter's single
thread is respected.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from app.config.logging_setup import get_logger
from app.language.manager import LanguageManager
from app.ui.bridge import UIController
from app.ui.state import UIState

logger = get_logger("ui.app")

_TKINTER_ERROR = (
    "The Azmuth desktop UI requires tkinter, which is not available on "
    "this Python build. Install a Python distribution that includes "
    "tkinter (on Windows, the official installer includes it by default)."
)


def _import_tkinter():  # noqa: ANN202
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError(_TKINTER_ERROR) from exc
    return tk, ttk


class AzmuthDesktopApp:
    """The tkinter shell.

    Owns a controller, an ``asyncio`` event loop running on a background
    thread, and the widget tree. Cross-thread calls from the loop back
    into tkinter go through :meth:`_post_to_ui`.
    """

    def __init__(
        self,
        controller: UIController,
        *,
        title: str = "Azmuth",
    ) -> None:
        tk, ttk = _import_tkinter()
        self._tk = tk
        self._ttk = ttk
        self._controller = controller
        self._state: UIState = controller.state

        self._root = tk.Tk()
        self._root.title(title)
        self._root.geometry("900x600")
        self._root.minsize(600, 400)

        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._run_loop, name="azmuth-ui-loop", daemon=True
        )
        self._loop_thread.start()

        self._build_widgets(tk, ttk)

        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- public API --------------------------------------------------------
    def run(self) -> None:
        """Enter the tkinter main loop. Blocks until the window is closed."""
        self._root.mainloop()

    def stop(self) -> None:
        """Request shutdown from any thread."""
        try:
            self._root.after(0, self._root.quit)
        except Exception:  # noqa: BLE001
            pass

    # -- widget construction ----------------------------------------------
    def _build_widgets(self, tk, ttk) -> None:  # noqa: ANN001
        from app.ui.components import InputRow, StatusBar, TranscriptView

        style = ttk.Style(self._root)
        if "vista" in style.theme_names():
            style.theme_use("vista")

        container = ttk.Frame(self._root)
        container.pack(fill="both", expand=True)

        self._transcript = TranscriptView(container, self._state)
        self._transcript.pack(fill="both", expand=True, padx=6, pady=(6, 0))

        sep = ttk.Separator(container, orient="horizontal")
        sep.pack(fill="x", padx=6)

        self._status_bar = StatusBar(container, self._state)
        self._status_bar.pack(fill="x", padx=6, pady=(2, 0))

        self._input_row = InputRow(
            container,
            on_send=self._handle_send,
            on_listen=self._handle_listen,
            on_clear=self._handle_clear,
            on_cancel_tasks=self._handle_cancel_tasks,
        )
        self._input_row.pack(fill="x", padx=6, pady=(0, 6))

        self._input_row.focus_entry()

        self._state.add_system_message(
            "Azmuth is running. Type a message or press Listen."
        )

    # -- handlers ----------------------------------------------------------
    def _handle_send(self, text: str) -> None:
        self._submit(self._controller.send_text(text))

    def _handle_listen(self) -> None:
        self._state.add_system_message(
            "Voice input is not available in this build."
        )

    def _handle_clear(self) -> None:
        self._controller.clear_conversation()

    def _handle_cancel_tasks(self) -> None:
        removed = self._controller.clear_finished_tasks()
        if removed:
            self._state.add_system_message(f"Cleared {removed} finished task(s).")

    # -- thread bridging ---------------------------------------------------
    def _submit(self, coro) -> None:  # noqa: ANN001
        """Schedule a coroutine on the background loop."""
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _post_to_ui(self, callback, *args: Any) -> None:  # noqa: ANN001
        """Run ``callback`` on the tkinter thread."""
        try:
            self._root.after(0, callback, *args)
        except Exception:  # noqa: BLE001 - window may be closing
            pass

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    # -- shutdown ----------------------------------------------------------
    def _on_close(self) -> None:
        try:
            self._controller.detach()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:  # noqa: BLE001
            pass
        self._root.destroy()


def build_app(
    language: LanguageManager,
    *,
    controller: UIController | None = None,
    title: str = "Azmuth",
) -> AzmuthDesktopApp:
    """Construct the desktop app. Does not enter the main loop."""
    if controller is None:
        controller = UIController(language=language)
    return AzmuthDesktopApp(controller, title=title)


def run_ui(language: LanguageManager) -> None:
    """Convenience: build the app and enter the main loop."""
    app = build_app(language)
    app.run()


__all__ = ["AzmuthDesktopApp", "build_app", "run_ui"]