"""The tkinter desktop window — redesigned with sidebar and dark theme."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from app.brain.pipeline import ConfirmationRequest
from app.config.logging_setup import get_logger
from app.language.manager import LanguageManager
from app.ui.bridge import UIController
from app.ui.state import UIState

logger = get_logger("ui.app")

_TKINTER_ERROR = (
    "The Azmuth desktop UI requires tkinter, which is not available on "
    "this Python build."
)


def _import_tkinter():
    try:
        import tkinter as tk
        from tkinter import messagebox, simpledialog, ttk
    except ImportError as exc:
        raise RuntimeError(_TKINTER_ERROR) from exc
    return tk, ttk, messagebox, simpledialog


class AzmuthDesktopApp:
    """The tkinter shell with sidebar + main area layout."""

    def __init__(
        self,
        controller: UIController,
        *,
        title: str = "Azmuth",
        authenticator=None,
        brain=None,
    ) -> None:
        tk, ttk, messagebox, simpledialog = _import_tkinter()
        self._tk = tk
        self._ttk = ttk
        self._messagebox = messagebox
        self._simpledialog = simpledialog
        self._controller = controller
        self._authenticator = authenticator
        self._brain = brain
        self._state: UIState = controller.state

        self._root = tk.Tk()
        self._root.title(title)

        # Size the window to the current screen instead of hardcoding.
        # On a 1536x864 logical screen (common on 1920x1080 @ 125% DPI),
        # a fixed 1200x800 window is taller than the usable area.
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()

        # Cap at 1200x800, but never exceed 90% of the screen in either
        # dimension — leaves room for the taskbar and the window chrome.
        win_w = min(1200, int(screen_w * 0.90))
        win_h = min(800,  int(screen_h * 0.90))
        win_x = (screen_w - win_w) // 2
        win_y = max(0, (screen_h - win_h) // 2 - 20)  # nudge up a touch

        self._root.geometry(f"{win_w}x{win_h}+{win_x}+{win_y}")
        self._root.minsize(700, 480)

        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._run_loop, name="azmuth-ui-loop", daemon=True)
        self._loop_thread.start()

        self._build_widgets(tk, ttk)
        self._controller.set_confirmer(self._confirm_from_dialog)
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

    def run(self) -> None:
        self._root.mainloop()

    def stop(self) -> None:
        try:
            self._root.after(0, self._root.quit)
        except Exception:
            pass

    def _build_widgets(self, tk, ttk) -> None:
        from app.ui.components import InputRow, Sidebar, StatusBar, TranscriptView
        from app.ui.theme import apply_theme, BASE

        apply_theme(self._root)

        outer = tk.Frame(self._root, bg=BASE)
        outer.pack(fill="both", expand=True)

        # ── Sidebar ──
        self._sidebar = Sidebar(
            outer, self._state,
            on_login=self._handle_login,
            on_local_toggle=self._handle_local_toggle,
            on_new_chat=self._handle_clear,
        )
        self._sidebar.pack(side="left", fill="y")

        # ── Main area ──
        main = tk.Frame(outer, bg=BASE)
        main.pack(side="left", fill="both", expand=True)
        main.rowconfigure(0, weight=1)
        main.rowconfigure(1, weight=0)
        main.rowconfigure(2, weight=0)
        main.columnconfigure(0, weight=1)

        self._transcript = TranscriptView(main, self._state)
        self._transcript.grid(row=0, column=0, sticky="nsew",
                              padx=4, pady=(4, 0))

        self._status_bar = StatusBar(main, self._state)
        self._status_bar.grid(row=1, column=0, sticky="ew")

        self._input_row = InputRow(
            main,
            on_send=self._handle_send,
            on_listen=self._handle_listen,
            on_clear=self._handle_clear,
            on_cancel_tasks=self._handle_cancel_tasks,
        )
        self._input_row.grid(row=2, column=0, sticky="ew")

        self._input_row.focus_entry()

        self._state.add_system_message(
            "Azmuth is running. Type a message, or click Login as Creator "
            "to unlock advanced automation.")

    # ── Handlers ───────────────────────────────────────────────────────
    def _handle_send(self, text: str) -> None:
        self._submit(self._controller.send_text(text))

    def _handle_listen(self) -> None:
        self._state.add_system_message(
            "Voice input is not available in this build.")

    def _handle_local_toggle(self, enabled: bool) -> None:
        if self._brain is None:
            self._state.add_system_message(
                "Local routing is not available in this build.")
            return
        self._brain.set_prefer_local(enabled)
        mode = "LOCAL (Ollama)" if enabled else "CLOUD"
        self._state.add_system_message(f"Routing mode: {mode}")

    def _handle_clear(self) -> None:
        self._controller.clear_conversation()

    def _handle_cancel_tasks(self) -> None:
        removed = self._controller.clear_finished_tasks()
        if removed:
            self._state.add_system_message(
                f"Cleared {removed} finished task(s).")

    def _handle_login(self) -> None:
        if self._authenticator is None:
            self._state.add_system_message(
                "Authentication is not configured in this build.",
                level="warning")
            return

        current = self._authenticator.current_principal
        if current.is_creator:
            self._authenticator.logout()
            self._state.set_mode("restricted", user_id=None)
            self._sidebar.set_logged_in(False)
            self._state.add_system_message("Logged out. Mode: RESTRICTED.")
            return

        pin = self._simpledialog.askstring(
            "Creator Login", "Enter the creator PIN:",
            show="*", parent=self._root)
        if not pin:
            return

        try:
            principal = self._authenticator.authenticate("pin", pin=pin)
        except Exception as exc:
            self._messagebox.showerror(
                "Login failed", str(exc), parent=self._root)
            return

        self._state.set_mode(principal.mode.value, user_id=principal.user_id)
        self._sidebar.set_logged_in(True)
        self._state.add_system_message(
            f"Logged in as creator. Mode: {principal.mode.value.upper()}.")

    # ── Confirmation dialog ────────────────────────────────────────────
    async def _confirm_from_dialog(
        self, request: ConfirmationRequest
    ) -> bool:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()

        def show_dialog() -> None:
            try:
                message = (
                    f"Azmuth is requesting permission to:\n\n"
                    f"    {request.description}\n\n"
                    f"Tool: {request.tool_name}\n"
                    f"Arguments: {request.arguments}\n"
                    f"Capability: {request.capability}\n"
                    f"Risk level: {request.risk_level}\n\n"
                    f"Allow this action?")
                answer = self._messagebox.askyesno(
                    "Azmuth needs confirmation", message,
                    parent=self._root)
                loop.call_soon_threadsafe(future.set_result, bool(answer))
            except Exception:
                logger.exception("confirmation dialog failed")
                loop.call_soon_threadsafe(future.set_result, False)

        try:
            self._root.after(0, show_dialog)
        except Exception:
            return False
        return await future

    # ── Thread bridging ────────────────────────────────────────────────
    def _submit(self, coro) -> None:
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _post_to_ui(self, callback, *args: Any) -> None:
        try:
            self._root.after(0, callback, *args)
        except Exception:
            pass

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    # ── Shutdown ───────────────────────────────────────────────────────
    def _on_close(self) -> None:
        try:
            self._controller.detach()
        except Exception:
            pass
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass
        self._root.destroy()


def build_app(
    language: LanguageManager,
    *,
    controller: UIController | None = None,
    authenticator=None,
    brain=None,
    title: str = "Azmuth",
) -> AzmuthDesktopApp:
    if controller is None:
        controller = UIController(language=language)
    return AzmuthDesktopApp(
        controller, title=title,
        authenticator=authenticator, brain=brain)


def run_ui(
    language: LanguageManager, authenticator=None, brain=None
) -> None:
    app = build_app(language, authenticator=authenticator, brain=brain)
    app.run()


__all__ = ["AzmuthDesktopApp", "build_app", "run_ui"]