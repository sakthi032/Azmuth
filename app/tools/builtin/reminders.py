"""Reminder scheduling tool — deterministic time, LLM extracts only text."""

from __future__ import annotations
import time as _time
from typing import Any, ClassVar

from app.security.capabilities import Capability
from app.tasks.persistent_scheduler import PersistentScheduler
from app.tasks.time_parser import (
    AmbiguousTimeError, parse_schedule_expression,
)
from app.tools.base import RiskLevel, Tool


_PENDING_TTL = 300.0  # seconds

class ScheduleReminderTool(Tool):
    """Schedule a reminder. The LLM extracts only {message, when_text} —
    code parses when_text and computes the due time in IST."""

    name: ClassVar[str] = "schedule_reminder"
    description: ClassVar[str] = (
        "Schedule a reminder. The user says when in their own words — "
        "English, Tanglish, or Tamil. Pass `when_text` VERBATIM (do NOT "
        "convert to seconds, do NOT compute a clock time). If the user "
        "did not state what to remind them about, omit `message` — the "
        "tool will ask. NEVER reuse a message from a prior reminder."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "when_text": {
                "type": "string",
                "maxLength": 200,
                "description": (
                    "The time phrase exactly as the user said it — e.g. "
                    "'naalaikku kaalai 7 manikku', 'in 2 hours', "
                    "'tomorrow at 7 pm'. Do NOT convert units or compute "
                    "clock times."
                ),
            },
            "message": {
                "type": "string",
                "maxLength": 500,
                "description": (
                    "What to remind about. OMIT this parameter if the "
                    "user did not state a message. Never copy a message "
                    "from a previous reminder."
                ),
            },
        },
        "required": [],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.VIEW_SYSTEM_STATUS
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    can_run_async: ClassVar[bool] = True

    def __init__(self, scheduler: PersistentScheduler | None) -> None:
        self._scheduler = scheduler
        self._pending_when_text: str | None = None
        self._pending_ts: float = 0.0

    def is_available(self) -> bool:
        return self._scheduler is not None

    async def execute(
        self,
        *,
        when_text: str | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        if self._scheduler is None:
            raise RuntimeError("reminder scheduler not configured")

        # Expire stale pending state
        if self._pending_when_text and (
            _time.monotonic() - self._pending_ts > _PENDING_TTL
        ):
            self._pending_when_text = None

        cleaned = (message or "").strip()

        # ── Reject reuse of an active reminder's message ────────────────
        if cleaned:
            try:
                active = self._scheduler.list_tasks(status="active")
            except Exception:
                active = []
            for row in active:
                if (
                    row.get("prompt", "").strip().lower()
                    == cleaned.lower()
                ):
                    if when_text:
                        self._pending_when_text = when_text
                        self._pending_ts = _time.monotonic()
                    return {
                        "scheduled": False,
                        "needs_clarification": "message",
                        "reason": (
                            f"the message {cleaned!r} is already an "
                            f"active reminder — do not reuse it"
                        ),
                        "prompt": (
                            "What should I remind you about? "
                            "(I won't reuse an existing reminder's "
                            "message.)"
                        ),
                    }

        # ── Missing message ─────────────────────────────────────────────
        if not cleaned:
            if when_text:
                self._pending_when_text = when_text
                self._pending_ts = _time.monotonic()
            return {
                "scheduled": False,
                "needs_clarification": "message",
                "prompt": "What should I remind you about?",
            }

        # ── Resolve time ────────────────────────────────────────────────
        effective_when = when_text or self._pending_when_text
        if not effective_when:
            return {
                "scheduled": False,
                "needs_clarification": "when",
                "prompt": "When should I remind you?",
            }

        try:
            parsed = parse_schedule_expression(effective_when)
        except AmbiguousTimeError as exc:
            return {
                "scheduled": False,
                "needs_clarification": "when",
                "reason": exc.reason,
                "prompt": (
                    f"I couldn't pin down the time ({exc.reason}). "
                    "Can you say it more specifically — for example, "
                    "'tomorrow morning at 7', 'today evening at 6', "
                    "or 'in 2 hours'?"
                ),
            }

        task_id = self._scheduler.schedule_reminder_at(
            message=cleaned, target_dt_utc=parsed.target_dt_utc,
        )
        self._pending_when_text = None
        return {
            "scheduled": True,
            "task_id": task_id,
            "message": cleaned,
            "when_text": effective_when,
            "resolved_ist": parsed.target_dt_ist.isoformat(),
            "resolved_display": parsed.target_dt_ist.strftime(
                "%a %d %b %Y, %I:%M %p IST"
            ),
        }


__all__ = ["ScheduleReminderTool"]