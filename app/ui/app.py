"""The tkinter desktop window — redesigned with sidebar and dark theme."""

from __future__ import annotations

import asyncio
import queue
import re
import threading
from typing import Any

from app.brain.pipeline import ConfirmationRequest
from app.config.logging_setup import get_logger
from app.language.manager import LanguageManager
from app.ui.bridge import UIController
from app.ui.state import UIState
from app.voice.manager import VoiceInputManager
from app.voice.tts.manager import TTSManager

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
        voice: VoiceInputManager | None = None,
        tts: TTSManager | None = None,
        event_bus=None,
        scheduler=None,
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
        self._voice = voice
        self._tts = tts
        self._event_bus = event_bus
        self._always_speak = False
        self._speech_queue = None
        self._narrator = None
        self._reminder_notifier = None
        self._scheduler = scheduler
        self._pending_attachment: str | None = None
        self._turn_in_flight = False
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
        win_h = min(800, int(screen_h * 0.90))
        win_x = (screen_w - win_w) // 2
        win_y = max(0, (screen_h - win_h) // 2 - 20)  # nudge up a touch

        self._root.geometry(f"{win_w}x{win_h}+{win_x}+{win_y}")
        self._root.minsize(700, 480)

        # Route UIState listener callbacks onto the Tk thread via a
        # thread-safe queue so background threads never call root.after().
        self._ui_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._ui_pump_after_id: str | None = None
        try:
            self._state.set_dispatcher(self._ui_queue.put)
        except Exception:
            logger.exception("could not install UIState dispatcher")
        self._ui_pump_after_id = self._root.after(15, self._drain_ui_queue)

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

    def _setup_speech(self) -> None:
        """Wire SpeechQueue and Narrator. Runs on the tkinter thread
        after the asyncio loop thread is confirmed running."""
        try:
            from pathlib import Path
            from app.voice.speech_queue import SpeechQueue
            from app.voice.narration import Narrator

            self._speech_queue = SpeechQueue(self._tts)

            # Schedule start() on the loop; safe now because run_forever()
            # has been called for ~50ms. Do NOT block waiting for it.
            asyncio.run_coroutine_threadsafe(
                self._speech_queue.start(), self._loop
            )

            tool_lookup = None
            if self._brain is not None:
                try:
                    tool_lookup = self._brain.tools.metadata
                except Exception:
                    logger.exception("could not access brain.tools.metadata")

            overrides = Path("data/narration_overrides.json")
            self._narrator = Narrator(
                self._speech_queue,
                tool_lookup=tool_lookup,
                overrides_path=overrides if overrides.is_file() else None,
            )
            self._narrator.set_enabled_callback(
                lambda: self._always_speak
            )
            self._narrator.subscribe(self._event_bus)
            logger.info(
                "speech narration wired",
                extra={"event": "SPEECH_NARRATION_READY"},
            )
        except Exception:
            logger.exception(
                "speech queue setup failed — narration disabled",
                extra={"event": "SPEECH_SETUP_FAILED"},
            )
            self._speech_queue = None
            self._narrator = None

    def _build_widgets(self, tk, ttk) -> None:
        from app.ui.components import InputRow, Sidebar, StatusBar, TranscriptView
        from app.ui.theme import apply_theme, BASE

        # Defer speech queue setup until the event loop is running.
        # Scheduling run_coroutine_threadsafe synchronously from __init__
        # races against the loop thread's run_forever() call.
        if self._tts is not None and self._event_bus is not None:
            self._root.after(50, self._setup_speech)
        apply_theme(self._root)

        outer = tk.Frame(self._root, bg=BASE)
        outer.pack(fill="both", expand=True)

        # ── Sidebar ──
        self._sidebar = Sidebar(
            outer, self._state,
            on_login=self._handle_login,
            on_local_toggle=self._handle_local_toggle,
            on_new_chat=self._handle_clear,
            on_always_speak_toggle=self._handle_always_speak_toggle,
        )
        self._sidebar.pack(side="left", fill="y")

        # ── Main area ──
        main = tk.Frame(outer, bg=BASE)
        main.pack(side="left", fill="both", expand=True)
        main.rowconfigure(0, weight=1)
        main.rowconfigure(1, weight=0)
        main.rowconfigure(2, weight=0)
        main.columnconfigure(0, weight=1)

        self._transcript = TranscriptView(
            main,
            self._state,
            on_stream_active_change=self._on_transcript_stream_change,
        )
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
            on_stop=self._handle_stop,
            on_attach=self._handle_attach,
            on_ask_screen=self._handle_ask_screen,
        )
        self._input_row.grid(row=2, column=0, sticky="ew")

        self._input_row.focus_entry()

        self._state.add_system_message(
            "Azmuth is running. Type a message, or click Login as Creator "
            "to unlock advanced automation.")

        # Reminder delivery: chat + toast + optional TTS on REMINDER_DUE.
        # Uses a lazy getter for the speech queue so it works whether or
        # not TTS is enabled.
        if self._event_bus is not None:
            try:
                from app.tasks.reminder_notifier import install_reminder_notifier

                self._reminder_notifier = install_reminder_notifier(
                    event_bus=self._event_bus,
                    ui_state=self._state,
                    speech_queue_getter=lambda: self._speech_queue,
                )
            except Exception:
                logger.exception("reminder notifier install failed")
                self._reminder_notifier = None

        if self._scheduler is not None:
            self._root.after(100, self._start_reminder_scheduler)

    def _start_reminder_scheduler(self) -> None:
        if self._scheduler is None:
            return
        try:
            async def _noop_executor(
                prompt: str, agent: str, task_id: str
            ) -> None:
                return None

            self._scheduler.start(
                executor=_noop_executor, loop=self._loop
            )
            logger.info(
                "reminder scheduler started on UI loop",
                extra={"event": "SCHEDULER_STARTED"},
            )
        except Exception:
            logger.exception("reminder scheduler start failed")

    # ── Handlers ───────────────────────────────────────────────────────
    def _handle_send(self, text: str) -> None:
        if not text.strip() and not self._pending_attachment:
            return
        attachment = self._pending_attachment
        self._pending_attachment = None
        hint: str | None = None
        if attachment:
            hint = (
                f"[User attached file: {attachment}]\n"
                f"Use read_document or ask_document on this path."
            )
            if not text.strip():
                text = "Summarize this file"
        self._submit(self._do_send_and_speak(text, llm_hint=hint))

    def _handle_attach(self) -> None:
        """Open a file picker, allow the chosen file for this session,
        and pre-fill the entry with a 'read <name>' prompt."""
        try:
            from tkinter import filedialog
        except ImportError:
            self._state.add_system_message(
                "File picker is not available in this Python build.",
                level="warning",
            )
            return

        path = filedialog.askopenfilename(
            parent=self._root,
            title="Attach a file to Azmuth",
            filetypes=[
                (
                    "All supported",
                    "*.pdf *.docx *.xlsx *.csv *.txt *.md *.py *.js "
                    "*.ts *.tsx *.jsx *.java *.c *.cpp *.h *.rs *.go "
                    "*.rb *.php *.swift *.kt *.lua *.sh *.ps1 *.sql "
                    "*.json *.yaml *.yml *.toml *.ini *.xml *.html "
                    "*.css *.log *.png *.jpg *.jpeg *.webp *.bmp *.gif",
                ),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return

        try:
            from app.documents.session_files import get_registry
            registry = get_registry()
            resolved = registry.allow(path)
        except Exception:
            logger.exception("could not register attached file")
            self._state.add_system_message(
                "Could not attach the selected file.",
                level="error",
            )
            return

        self._pending_attachment = str(resolved)
        name = resolved.name
        self._state.add_system_message(
            f"\U0001F4CE Attached: {name}"
        )
        try:
            self._input_row.focus_entry()
        except Exception:
            pass

    def _handle_ask_screen(self) -> None:
        """Prompt for a question about the screen, then submit it."""
        question = self._simpledialog.askstring(
            "Ask about my screen",
            "What would you like to know about your screen?",
            initialvalue="What's on my screen right now?",
            parent=self._root,
        )
        if not question or not question.strip():
            return
        hint = '[Use describe_screen with region="active_window".]'
        self._input_row.set_text("")
        # 3-second countdown so the user can arrange windows
        async def _countdown_then_send() -> None:
            for n in (3, 2, 1):
                self._state.add_system_message(
                    f"\U0001F5A5 Capturing screen in {n}\u2026"
                )
                await asyncio.sleep(1.0)
            await self._do_send_and_speak(question.strip(), llm_hint=hint)
        self._submit(_countdown_then_send())

    def _on_transcript_stream_change(self, _active: bool) -> None:
        self._sync_streaming_controls()

    def _sync_streaming_controls(self) -> None:
        streaming = self._turn_in_flight or self._transcript.is_streaming
        try:
            self._input_row.set_streaming(streaming)
        except Exception:
            pass

    def _handle_stop(self) -> None:
        # Reset UI IMMEDIATELY. Do not wait for the worker.
        logger.info(
            "stop button pressed",
            extra={"event": "STOP_PRESSED"},
        )
        self._turn_in_flight = False
        try:
            self._transcript.stop_active_render()
        except Exception:
            logger.exception("stop_active_render failed")
        try:
            self._input_row.set_streaming(False)
        except Exception:
            pass
        self._state.add_system_message("\u23f9 Stopped")

        # Cancel the in-flight task on the loop thread.
        try:
            self._loop.call_soon_threadsafe(
                self._controller.cancel_in_flight
            )
        except Exception:
            logger.exception("cancel_in_flight scheduling failed")

        # Cancel pending TTS for this turn.
        if self._speech_queue is not None:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._speech_queue.cancel_pending(), self._loop
                )
            except Exception:
                logger.exception("cancel_pending TTS failed")

    async def _do_send_and_speak(
        self, text: str, *, llm_hint: str | None = None
    ) -> None:
        if self._speech_queue is not None:
            await self._speech_queue.cancel_pending()

        self._turn_in_flight = True
        self._post_to_ui(self._sync_streaming_controls)

        async def _noop_delta(_delta: str) -> None:
            return None

        try:
            try:
                reply = await self._controller.send_text(
                    text, on_delta=_noop_delta, llm_hint=llm_hint
                )
            except asyncio.CancelledError:
                logger.info(
                    "turn cancelled by Stop",
                    extra={"event": "UI_TURN_CANCELLED"},
                )
                return
        finally:
            self._turn_in_flight = False
            self._post_to_ui(self._sync_streaming_controls)

        if reply and self._always_speak:
            spoken = self._controller.last_spoken_text or reply
            sentences = _split_sentences(spoken)
            if self._speech_queue is not None:
                for sentence in sentences:
                    await self._speech_queue.enqueue(sentence)
            elif self._tts is not None:
                for sentence in sentences:
                    asyncio.create_task(
                        self._tts.speak_response(sentence),
                        name="tts_speak",
                    )

    def _handle_listen(self) -> None:
        if self._voice is None:
            self._state.add_system_message(
                "Voice input is not configured.", level="warning")
            return
        self._submit(self._do_voice_listen())

    async def _do_voice_listen(self) -> None:
        if not self._voice.is_available(require_wake_word=False):
            self._state.add_system_message(
                "Voice input unavailable — check microphone and STT.",
                level="warning")
            return

        if self._speech_queue is not None:
            await self._speech_queue.cancel_pending()

        self._state.add_system_message("Listening…")
        user_input = await self._voice.listen_once(require_wake_word=False)
        if user_input is None:
            self._state.add_system_message("No speech detected.")
            return

        reply = await self._controller.add_voice_input(user_input)
        if reply and self._always_speak:
            spoken = self._controller.last_spoken_text or reply
            if self._speech_queue is not None:
                await self._speech_queue.enqueue(
                    spoken, language=user_input.language
                )
            elif self._tts is not None:
                asyncio.create_task(
                    self._tts.speak_response(
                        spoken, language=user_input.language
                    ),
                    name="tts_speak",
                )

    def _handle_always_speak_toggle(self, enabled: bool) -> None:
        self._always_speak = enabled
        state = "ON" if enabled else "OFF"
        self._state.add_system_message(f"Always-speak: {state}")

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
            # Isolate principals: reset the in-memory session so the
            # next turn cannot see the creator's conversation history.
            self._controller.reset_conversation()
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

        # Reset the anonymous session so creator mode starts fresh.
        self._controller.reset_conversation()
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
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)

        def _log_failure(f):
            try:
                exc = f.exception()
            except (asyncio.CancelledError, Exception):
                return
            if exc is not None:
                logger.exception(
                    "UI coroutine raised",
                    exc_info=exc,
                    extra={"event": "UI_COROUTINE_RAISED"},
                )

        fut.add_done_callback(_log_failure)

    def _drain_ui_queue(self) -> None:
        while True:
            try:
                fn = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                logger.exception("UI queued callback failed")
        try:
            self._ui_pump_after_id = self._root.after(15, self._drain_ui_queue)
        except Exception:
            self._ui_pump_after_id = None

    def _post_to_ui(self, callback, *args: Any) -> None:
        try:
            if args:
                self._ui_queue.put(lambda: callback(*args))
            else:
                self._ui_queue.put(callback)
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
        if self._ui_pump_after_id is not None:
            try:
                self._root.after_cancel(self._ui_pump_after_id)
            except Exception:
                pass
            self._ui_pump_after_id = None
        if self._reminder_notifier is not None:
            try:
                self._reminder_notifier.detach()
            except Exception:
                pass
        if self._scheduler is not None:
            try:
                self._scheduler.stop()
            except Exception:
                logger.exception("scheduler stop failed")
        if self._narrator is not None:
            try:
                self._narrator.unsubscribe()
            except Exception:
                pass
        if self._speech_queue is not None:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._speech_queue.stop(), self._loop
                )
            except Exception:
                pass
        try:
            self._controller.detach()
        except Exception:
            pass
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass
        self._root.destroy()


# ── Sentence splitter (module level, used by _do_send_and_speak) ──────
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")


def _split_sentences(text: str) -> list[str]:
    """Split ``text`` into sentence-ish chunks for sentence-by-sentence TTS."""
    if not text:
        return []
    parts: list[str] = []
    start = 0
    for m in _SENTENCE_END.finditer(text):
        piece = text[start:m.end()].strip()
        if piece:
            parts.append(piece)
        start = m.end()
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts or [text.strip()]


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