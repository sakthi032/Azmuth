
"""The tkinter desktop window."""

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
    """The tkinter shell."""

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
        self._root.geometry("1150x780")
        self._root.minsize(700, 500)

        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._run_loop, name="azmuth-ui-loop", daemon=True
        )
        self._loop_thread.start()

        self._build_widgets(tk, ttk)

        # Install a confirmer on the controller so dangerous tools show
        # a dialog before running. Wired here because the dialog needs
        # the tk root.
        self._controller.set_confirmer(self._confirm_from_dialog)

        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- public API --------------------------------------------------------
    def run(self) -> None:
        self._root.mainloop()

    def stop(self) -> None:
        try:
            self._root.after(0, self._root.quit)
        except Exception:  # noqa: BLE001
            pass

    # -- widget construction ----------------------------------------------
    def _build_widgets(self, tk, ttk) -> None:
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
            on_login=self._handle_login,
            on_clear=self._handle_clear,
            on_cancel_tasks=self._handle_cancel_tasks,
            on_local_toggle=self._handle_local_toggle,
        )
        self._input_row.pack(fill="x", padx=6, pady=(0, 6))

        self._input_row.focus_entry()

        self._state.add_system_message(
            "Azmuth is running. Type a message, or click Login as Creator "
            "to unlock advanced automation."
        )

    # -- handlers ----------------------------------------------------------
    def _handle_send(self, text: str) -> None:
        self._submit(self._controller.send_text(text))

    def _handle_listen(self) -> None:
        self._state.add_system_message(
            "Voice input is not available in this build."
        )

    def _handle_local_toggle(self, enabled: bool) -> None:
        if self._brain is None:
            self._state.add_system_message(
                "Local routing is not available in this build."
            )
            return
        self._brain.set_prefer_local(enabled)
        mode = "LOCAL (Ollama)" if enabled else "CLOUD"
        self._state.add_system_message(f"Routing mode: {mode}")

    def _handle_clear(self) -> None:
        self._controller.clear_conversation()

    def _handle_cancel_tasks(self) -> None:
        removed = self._controller.clear_finished_tasks()
        if removed:
            self._state.add_system_message(f"Cleared {removed} finished task(s).")

    def _handle_login(self) -> None:
        if self._authenticator is None:
            self._state.add_system_message(
                "Authentication is not configured in this build.",
                level="warning",  # type: ignore[arg-type]
            )
            return

        current = self._authenticator.current_principal
        if current.is_creator:
            self._authenticator.logout()
            self._state.set_mode("restricted", user_id=None)
            self._input_row.set_logged_in(False)
            self._state.add_system_message("Logged out. Mode: RESTRICTED.")
            return

        pin = self._simpledialog.askstring(
            "Creator Login",
            "Enter the creator PIN:",
            show="*",
            parent=self._root,
        )
        if not pin:
            return

        try:
            principal = self._authenticator.authenticate("pin", pin=pin)
        except Exception as exc:  # noqa: BLE001
            self._messagebox.showerror(
                "Login failed", str(exc), parent=self._root
            )
            return

        self._state.set_mode(principal.mode.value, user_id=principal.user_id)
        self._input_row.set_logged_in(True)
        self._state.add_system_message(
            f"Logged in as creator. Mode: {principal.mode.value.upper()}."
        )

    # -- confirmation dialog ----------------------------------------------
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
                    f"Allow this action?"
                )
                answer = self._messagebox.askyesno(
                    "Azmuth needs confirmation",
                    message,
                    parent=self._root,
                )
                loop.call_soon_threadsafe(future.set_result, bool(answer))
            except Exception:  # noqa: BLE001
                logger.exception("confirmation dialog failed")
                loop.call_soon_threadsafe(future.set_result, False)

        try:
            self._root.after(0, show_dialog)
        except Exception:  # noqa: BLE001
            return False
        return await future

    # -- thread bridging ---------------------------------------------------
    def _submit(self, coro) -> None:
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _post_to_ui(self, callback, *args: Any) -> None:
        try:
            self._root.after(0, callback, *args)
        except Exception:  # noqa: BLE001
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
    authenticator=None,
    brain=None,
    title: str = "Azmuth",
) -> AzmuthDesktopApp:
    if controller is None:
        controller = UIController(language=language)
    return AzmuthDesktopApp(
        controller,
        title=title,
        authenticator=authenticator,
        brain=brain,
    )


def run_ui(
    language: LanguageManager, authenticator=None, brain=None
) -> None:
    app = build_app(language, authenticator=authenticator, brain=brain)
    app.run()


__all__ = ["AzmuthDesktopApp", "build_app", "run_ui"]
