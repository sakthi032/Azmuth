"""Multi-channel delivery system for Azmuth reminders.

Delivers fired reminders through all enabled channels:
1. Chat transcript: Adds highlighted message in transcript on Tk main thread.
2. Windows toast + Sound + Popup:
   - Plays notification sound (winsound.MessageBeep).
   - Shows native Windows toast notification (via winotify with PowerShell fallback).
   - Flashes taskbar icon (Win32 FlashWindow).
   - Shows an always-on-top popup with "Snooze (5 min)" and "Dismiss" buttons.
3. Voice: Speaks reminder via SpeechQueue / TTS when enabled.

Configured via settings.reminder_channels (e.g. ("chat", "toast", "voice")).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from app.config.settings import Settings
from app.core.events import Event, EventBus, EventName
from app.tasks.persistent_scheduler import PersistentScheduler
from app.tasks.time_parser import format_ist

logger = logging.getLogger("tasks.reminder_delivery")


class ReminderDeliveryService:
    """Subscribes to REMINDER_DUE and dispatches across configured channels."""

    def __init__(
        self,
        *,
        event_bus: EventBus,
        settings: Settings,
        scheduler: PersistentScheduler | None = None,
        ui_state: Any | None = None,
        tts: Any | None = None,
        speech_queue: Any | None = None,
        tk_root: Any | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._settings = settings
        self._scheduler = scheduler
        self._ui_state = ui_state
        self._tts = tts
        self._speech_queue = speech_queue
        self._tk_root = tk_root
        self._unsub: Callable[[], None] | None = None
        self._active_popups: list[Any] = []

    def attach_ui(
        self,
        *,
        ui_state: Any | None = None,
        tk_root: Any | None = None,
        speech_queue: Any | None = None,
    ) -> None:
        """Attach active UI elements after desktop app starts."""
        if ui_state is not None:
            self._ui_state = ui_state
        if tk_root is not None:
            self._tk_root = tk_root
        if speech_queue is not None:
            self._speech_queue = speech_queue

    def subscribe(self) -> None:
        """Subscribe to REMINDER_DUE events on the EventBus."""
        if self._unsub is None:
            self._unsub = self._event_bus.subscribe(
                EventName.REMINDER_DUE, self._on_reminder_due
            )
            logger.info("ReminderDeliveryService subscribed to REMINDER_DUE")

    def unsubscribe(self) -> None:
        """Unsubscribe from the EventBus."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    async def _on_reminder_due(self, event: Event) -> None:
        """Handle REMINDER_DUE event."""
        payload = event.payload or {}
        task_id = str(payload.get("task_id", ""))
        message = str(payload.get("message") or payload.get("prompt", ""))
        due_time = payload.get("due_time")
        is_missed = bool(payload.get("is_missed", False))

        due_time_ist = payload.get("due_time_ist") or (
            format_ist(due_time) if due_time else "now"
        )

        channels = tuple(
            c.lower().strip() for c in getattr(self._settings, "reminder_channels", ("chat", "toast", "voice"))
        )
        logger.info(
            "Delivering reminder %r (channels=%s, missed=%s)",
            message,
            channels,
            is_missed,
            extra={"event": "REMINDER_DELIVERY_START", "task_id": task_id},
        )

        # -------------------------------------------------------------
        # Channel 1: Chat Transcript
        # -------------------------------------------------------------
        if "chat" in channels:
            self._deliver_chat(message, due_time_ist=due_time_ist, is_missed=is_missed)

        # -------------------------------------------------------------
        # Channel 2: Windows Toast & Desktop Popup
        # -------------------------------------------------------------
        if "toast" in channels:
            self._deliver_toast(message, due_time_ist=due_time_ist, is_missed=is_missed)
            self._deliver_desktop_popup(message, due_time_ist=due_time_ist, is_missed=is_missed)

        # -------------------------------------------------------------
        # Channel 3: Voice / TTS
        # -------------------------------------------------------------
        if "voice" in channels:
            await self._deliver_voice(message, is_missed=is_missed)

    # ------------------------------------------------------------------
    # Channel Implementations
    # ------------------------------------------------------------------

    def _deliver_chat(self, message: str, *, due_time_ist: str, is_missed: bool) -> None:
        """Post reminder message into the transcript."""
        if is_missed:
            chat_text = f"⏰ **[Missed Reminder]** {message} *(was due {due_time_ist})*"
        else:
            chat_text = f"⏰ **Reminder:** {message}"

        if self._ui_state is not None:
            try:
                # Add assistant message with special source="reminder" for visual highlighting
                self._ui_state.add_assistant_message(
                    chat_text,
                    source="reminder",
                    model="system",
                    provider="scheduler",
                )
                logger.debug("Reminder posted to chat transcript")
            except Exception as exc:
                logger.warning("Failed to post reminder to UI state: %s", exc)
        else:
            # CLI fallback
            print(f"\n[REMINDER] {chat_text}\n")

    def _deliver_toast(self, message: str, *, due_time_ist: str, is_missed: bool) -> None:
        """Emit native Windows toast notification with sound."""
        # 1. Play sound (best-effort)
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass

        # 2. Toast notification
        title = "Azmuth Missed Reminder" if is_missed else "Azmuth Reminder"
        body = f"{message} (due {due_time_ist})" if is_missed else message

        # Try winotify first
        try:
            from winotify import Notification, audio
            toast = Notification(
                app_id="Azmuth",
                title=title,
                msg=body,
            )
            toast.set_audio(audio.Default, loop=False)
            toast.show()
            logger.debug("Winotify toast notification displayed")
            return
        except Exception as exc:
            logger.debug("winotify toast failed: %s; trying fallback", exc)

        # Fallback: lightweight PowerShell toast
        try:
            import subprocess
            ps_script = (
                f"[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
                f"$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
                f"$text = $template.GetElementsByTagName('text');"
                f"$text[0].AppendChild($template.CreateTextNode('{title}')) | Out-Null;"
                f"$text[1].AppendChild($template.CreateTextNode('{body}')) | Out-Null;"
                f"$toast = [Windows.UI.Notifications.ToastNotification]::new($template);"
                f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Azmuth').Show($toast);"
            )
            subprocess.Popen(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            pass

    def _deliver_desktop_popup(self, message: str, *, due_time_ist: str, is_missed: bool) -> None:
        """Flash taskbar icon and display always-on-top popup with Snooze/Dismiss."""
        if self._tk_root is None:
            return

        def _show_popup() -> None:
            try:
                import tkinter as tk
                from tkinter import ttk

                # Flash taskbar icon if window is not foreground or is minimized
                try:
                    import ctypes
                    hwnd = self._tk_root.winfo_id()
                    ctypes.windll.user32.FlashWindow(hwnd, True)
                except Exception:
                    pass

                popup = tk.Toplevel(self._tk_root)
                popup.title("Azmuth Reminder" if not is_missed else "Missed Reminder")
                popup.attributes("-topmost", True)
                popup.geometry("380x180")
                popup.minsize(360, 160)
                popup.configure(bg="#1e1e2e")

                # Center the popup on screen
                sw = popup.winfo_screenwidth()
                sh = popup.winfo_screenheight()
                px = max(0, (sw - 380) // 2)
                py = max(0, (sh - 180) // 2)
                popup.geometry(f"+{px}+{py}")

                # Content frame
                frame = tk.Frame(popup, bg="#1e1e2e", padx=16, pady=14)
                frame.pack(fill="both", expand=True)

                header_text = "⏰ Missed Reminder" if is_missed else "⏰ Reminder"
                header_color = "#f38ba8" if is_missed else "#f9e2af"
                lbl_header = tk.Label(
                    frame, text=header_text, font=("Segoe UI", 12, "bold"),
                    bg="#1e1e2e", fg=header_color
                )
                lbl_header.pack(anchor="w")

                lbl_msg = tk.Label(
                    frame, text=message, font=("Segoe UI", 11),
                    bg="#1e1e2e", fg="#cdd6f4", wraplength=340, justify="left"
                )
                lbl_msg.pack(anchor="w", pady=(6, 4))

                lbl_time = tk.Label(
                    frame, text=f"Time: {due_time_ist}", font=("Segoe UI", 9),
                    bg="#1e1e2e", fg="#6c7086"
                )
                lbl_time.pack(anchor="w", pady=(0, 10))

                # Buttons frame
                btn_frame = tk.Frame(frame, bg="#1e1e2e")
                btn_frame.pack(fill="x", side="bottom")

                def _snooze() -> None:
                    try:
                        if self._scheduler is not None:
                            self._scheduler.add_task(
                                prompt=message,
                                schedule_type="once",
                                schedule_value="in 5 minutes",
                            )
                            logger.info("Reminder snoozed for 5 minutes: %s", message)
                    except Exception as e:
                        logger.warning("Failed to snooze reminder: %s", e)
                    popup.destroy()

                def _dismiss() -> None:
                    popup.destroy()

                btn_snooze = tk.Button(
                    btn_frame, text="Snooze (5 min)", font=("Segoe UI", 9),
                    bg="#313244", fg="#cdd6f4", activebackground="#45475a",
                    command=_snooze, relief="flat", padx=10, pady=4
                )
                btn_snooze.pack(side="left")

                btn_dismiss = tk.Button(
                    btn_frame, text="Dismiss", font=("Segoe UI", 9, "bold"),
                    bg="#89b4fa", fg="#11111b", activebackground="#b4befe",
                    command=_dismiss, relief="flat", padx=14, pady=4
                )
                btn_dismiss.pack(side="right")

                self._active_popups.append(popup)
            except Exception as exc:
                logger.warning("Could not show popup: %s", exc)

        try:
            self._tk_root.after(0, _show_popup)
        except Exception:
            pass

    async def _deliver_voice(self, message: str, *, is_missed: bool) -> None:
        """Speak reminder via TTS."""
        spoken = f"Missed reminder: {message}" if is_missed else f"Reminder: {message}"
        try:
            if self._speech_queue is not None:
                await self._speech_queue.enqueue(spoken)
                logger.debug("Reminder enqueued to speech queue")
            elif self._tts is not None and getattr(self._tts, "is_available", lambda: False)():
                await self._tts.speak_response(spoken)
                logger.debug("Reminder spoken directly via TTS")
        except Exception as exc:
            logger.warning("Failed to speak reminder: %s", exc)


__all__ = ["ReminderDeliveryService"]
