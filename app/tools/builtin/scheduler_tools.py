"""Scheduler Tool wrappers for PersistentScheduler.

Exposes three tools:
  - schedule_task   : add a task to the SQLite-backed scheduler
  - list_scheduled_tasks : list all tasks
  - cancel_scheduled_task : cancel a task by id

All use Capability.USE_BASIC_TOOLS so they work in RESTRICTED mode.
execute() only writes to the DB; it does NOT start the scheduler loop.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, ClassVar

from app.security.capabilities import Capability
from app.tasks.persistent_scheduler import PersistentScheduler
from app.tasks.time_parser import format_ist, parse_schedule_expression
from app.tools.base import RiskLevel, Tool


def _parse_when(when: str) -> tuple[str, str, str]:
    """Parse a human-readable or ISO time string into (schedule_type, schedule_value, display_ist)."""
    parsed = parse_schedule_expression(when)
    return parsed.schedule_type, parsed.schedule_value, parsed.display_ist


class ScheduleTaskTool(Tool):
    """Schedule a reminder or task to run at a future time."""

    name: ClassVar[str] = "schedule_task"
    description: ClassVar[str] = (
        "Schedule a reminder or task to run at a future time. "
        "Use natural time like 'in 2 minutes', 'in 1 hour', or an ISO datetime. "
        "schedule_type is 'once' (default), 'interval' (every N seconds), or 'cron'."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The message or action to execute when the task fires.",
            },
            "when": {
                "type": "string",
                "description": (
                    "When to run. Natural: 'in 2 minutes', 'in 1 hour'. "
                    "Or ISO-8601: '2026-11-18T15:00:00Z'. "
                    "For interval: number of seconds (e.g. '300'). "
                    "For cron: a cron expression (e.g. '0 8 * * *')."
                ),
            },
            "schedule_type": {
                "type": "string",
                "enum": ["once", "interval", "cron"],
                "description": "How to schedule: 'once' (default), 'interval', or 'cron'.",
            },
        },
        "required": ["prompt", "when"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.USE_BASIC_TOOLS
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    can_run_async: ClassVar[bool] = False

    def __init__(self, scheduler: PersistentScheduler) -> None:
        self._scheduler = scheduler

    def is_available(self) -> bool:
        return True

    async def execute(self, *, prompt: str, when: str, schedule_type: str = "once") -> dict[str, Any]:
        # Parse expression to determine type, value, and IST formatted display
        parsed = parse_schedule_expression(when)
        actual_type = parsed.schedule_type if schedule_type == "once" else schedule_type
        actual_value = parsed.schedule_value if schedule_type == "once" else when

        task_id = self._scheduler.add_task(
            prompt=prompt,
            schedule_type=actual_type,
            schedule_value=actual_value,
        )

        # Look up next_run to report back
        tasks = self._scheduler.list_tasks()
        next_run_ts = next(
            (t["next_run"] for t in tasks if t["id"] == task_id), None
        )
        if next_run_ts is not None:
            next_run_iso = datetime.fromtimestamp(next_run_ts, tz=timezone.utc).isoformat()
            next_run_ist = format_ist(next_run_ts)
        else:
            next_run_iso = None
            next_run_ist = parsed.display_ist

        confirmation = f"Reminder scheduled for {next_run_ist}: '{prompt}'"

        return {
            "task_id": task_id,
            "prompt": prompt,
            "schedule_type": actual_type,
            "schedule_value": actual_value,
            "next_run": next_run_iso,
            "next_run_ist": next_run_ist,
            "confirmation": confirmation,
            "status": "scheduled",
        }


class ListScheduledTasksTool(Tool):
    """List all scheduled tasks."""

    name: ClassVar[str] = "list_scheduled_tasks"
    description: ClassVar[str] = (
        "List all scheduled tasks (reminders, automations) stored in the scheduler. "
        "Returns id, prompt, schedule_type, next_run, and status for each task."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.USE_BASIC_TOOLS
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    can_run_async: ClassVar[bool] = True

    def __init__(self, scheduler: PersistentScheduler) -> None:
        self._scheduler = scheduler

    def is_available(self) -> bool:
        return True

    async def execute(self) -> dict[str, Any]:
        tasks = self._scheduler.list_tasks()
        # Convert timestamps to ISO strings for readability
        result = []
        for t in tasks:
            entry = {
                "id": t["id"],
                "prompt": t["prompt"],
                "schedule_type": t["schedule_type"],
                "schedule_value": t["schedule_value"],
                "status": t["status"],
                "run_count": t["run_count"],
                "next_run": (
                    datetime.fromtimestamp(t["next_run"], tz=timezone.utc).isoformat()
                    if t["next_run"] is not None
                    else None
                ),
                "next_run_ist": (
                    format_ist(t["next_run"])
                    if t["next_run"] is not None
                    else None
                ),
                "last_run": (
                    datetime.fromtimestamp(t["last_run"], tz=timezone.utc).isoformat()
                    if t["last_run"] is not None
                    else None
                ),
            }
            result.append(entry)
        return {"tasks": result, "count": len(result)}


class CancelScheduledTaskTool(Tool):
    """Cancel a scheduled task by its ID."""

    name: ClassVar[str] = "cancel_scheduled_task"
    description: ClassVar[str] = (
        "Cancel a scheduled task by its task_id. "
        "Use list_scheduled_tasks to find the task_id. "
        "Returns whether the cancellation succeeded."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The UUID of the task to cancel.",
            }
        },
        "required": ["task_id"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.USE_BASIC_TOOLS
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    can_run_async: ClassVar[bool] = False

    def __init__(self, scheduler: PersistentScheduler) -> None:
        self._scheduler = scheduler

    def is_available(self) -> bool:
        return True

    async def execute(self, *, task_id: str) -> dict[str, Any]:
        cancelled = self._scheduler.cancel_task(task_id)
        return {
            "task_id": task_id,
            "cancelled": cancelled,
            "message": (
                f"Task {task_id} cancelled." if cancelled
                else f"Task {task_id} not found or already completed."
            ),
        }


__all__ = [
    "ScheduleTaskTool",
    "ListScheduledTasksTool",
    "CancelScheduledTaskTool",
]
