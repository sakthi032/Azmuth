"""Tests for SQLite-backed persistent task scheduler."""

import asyncio
import time
from pathlib import Path
import pytest

from app.tasks.persistent_scheduler import PersistentScheduler


@pytest.fixture
def scheduler(tmp_path: Path):
    db_file = tmp_path / "scheduler.db"
    sched = PersistentScheduler(db_path=db_file, poll_interval=0.1)
    yield sched
    sched.stop()


def test_scheduler_add_and_list_tasks(scheduler: PersistentScheduler):
    """Adding tasks returns ID and persists to SQLite."""
    task_id = scheduler.add_task(
        prompt="Daily backup",
        schedule_type="interval",
        schedule_value="3600",
        agent="system",
    )
    assert task_id is not None

    tasks = scheduler.list_tasks(status="active")
    assert len(tasks) == 1
    assert tasks[0]["id"] == task_id
    assert tasks[0]["prompt"] == "Daily backup"
    assert tasks[0]["schedule_type"] == "interval"
    assert tasks[0]["schedule_value"] == "3600"
    assert tasks[0]["agent"] == "system"


def test_scheduler_pause_resume_cancel(scheduler: PersistentScheduler):
    """Pause, resume, and cancel state transitions."""
    task_id = scheduler.add_task(
        prompt="Check updates",
        schedule_type="interval",
        schedule_value="60",
    )
    
    # Pause
    assert scheduler.pause_task(task_id) is True
    paused_tasks = scheduler.list_tasks(status="paused")
    assert len(paused_tasks) == 1
    assert paused_tasks[0]["id"] == task_id

    # Resume
    assert scheduler.resume_task(task_id) is True
    active_tasks = scheduler.list_tasks(status="active")
    assert len(active_tasks) == 1
    assert active_tasks[0]["id"] == task_id

    # Cancel
    assert scheduler.cancel_task(task_id) is True
    active_tasks_after = scheduler.list_tasks(status="active")
    assert len(active_tasks_after) == 0
    completed_tasks = scheduler.list_tasks(status="completed")
    assert len(completed_tasks) == 1


def test_scheduler_persistence_after_restart(tmp_path: Path):
    """Tasks survive process restart when a new PersistentScheduler opens same DB."""
    db_file = tmp_path / "restart_test.db"
    
    # Instance 1: add task and shut down
    s1 = PersistentScheduler(db_path=db_file)
    task_id = s1.add_task(
        prompt="Persistent alarm",
        schedule_type="interval",
        schedule_value="120",
    )
    s1.stop()

    # Instance 2: reopen same DB
    s2 = PersistentScheduler(db_path=db_file)
    tasks = s2.list_tasks()
    assert len(tasks) == 1
    assert tasks[0]["id"] == task_id
    assert tasks[0]["prompt"] == "Persistent alarm"
    s2.stop()


def test_scheduler_invalid_type_rejected(scheduler: PersistentScheduler):
    """Invalid schedule_type raises ValueError."""
    with pytest.raises(ValueError):
        scheduler.add_task(
            prompt="Bad task",
            schedule_type="unknown_type",
            schedule_value="10",
        )


def test_scheduler_once_schedule(scheduler: PersistentScheduler):
    """Once schedule sets future timestamp."""
    task_id = scheduler.add_task(
        prompt="One-time reminder",
        schedule_type="once",
        schedule_value="60",  # relative 60 seconds
    )
    tasks = scheduler.list_tasks()
    assert len(tasks) == 1
    assert tasks[0]["next_run"] > time.time()


def test_reminder_due_event_emitted_on_fire(tmp_path: Path):
    """REMINDER_DUE is published when a scheduled reminder reaches its due time."""
    from datetime import datetime

    from app.core.events import EventBus, EventName

    async def _run():
        bus = EventBus()
        received: list[dict] = []

        async def _on_due(event):
            received.append(dict(event.payload))

        bus.subscribe(EventName.REMINDER_DUE, _on_due)

        sched = PersistentScheduler(
            db_path=tmp_path / "reminder.db",
            poll_interval=0.1,
            event_bus=bus,
        )

        async def _noop_executor(prompt, agent, task_id):
            return None

        sched.start(executor=_noop_executor, loop=asyncio.get_running_loop())
        try:
            task_id = sched.schedule_reminder("Test reminder", delay_seconds=2)

            deadline = time.monotonic() + 6.0
            while not received and time.monotonic() < deadline:
                await asyncio.sleep(0.1)

            assert received, "REMINDER_DUE was never emitted"
            payload = received[0]
            assert payload["task_id"] == task_id
            assert payload["message"] == "Test reminder"
            due = payload["due_time"]
            assert isinstance(due, datetime)
            assert due.tzinfo is not None
            assert due.tzinfo.utcoffset(due).total_seconds() == 19800  # IST
        finally:
            sched.stop()

    asyncio.run(_run())
    
def test_schedule_reminder_tool_delegates(tmp_path):
    """ScheduleReminderTool parses when_text and schedules at the resolved time."""
    import asyncio
    from datetime import datetime, timedelta, timezone
    from app.tasks.persistent_scheduler import PersistentScheduler
    from app.tools.builtin.reminders import ScheduleReminderTool

    sched = PersistentScheduler(db_path=tmp_path / "tool.db")
    tool = ScheduleReminderTool(sched)
    before = datetime.now(tz=timezone.utc)
    result = asyncio.run(
        tool.execute(message="hydrate", when_text="in 5 minutes")
    )
    assert result["message"] == "hydrate"
    assert result["when_text"] == "in 5 minutes"
    assert result["task_id"]
    assert "resolved_display" in result
    assert "IST" in result["resolved_display"]

    # Row written; due time ~5 min from now
    rows = sched.list_tasks(status="active")
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == result["task_id"]
    assert row["prompt"] == "hydrate"
    due_ts = row["next_run"]
    delta = due_ts - before.timestamp()
    assert 290 <= delta <= 310, f"expected ~300s, got {delta:.1f}s"

def test_schedule_reminder_tool_unavailable_without_scheduler():
    """is_available returns False when scheduler is None."""
    from app.tools.builtin.reminders import ScheduleReminderTool

    assert ScheduleReminderTool(None).is_available() is False
    
    
def test_schedule_reminder_tool_asks_for_missing_message(tmp_path):
    """No message → needs_clarification='message', nothing scheduled."""
    import asyncio

    from app.tasks.persistent_scheduler import PersistentScheduler
    from app.tools.builtin.reminders import ScheduleReminderTool

    sched = PersistentScheduler(db_path=tmp_path / "tool2.db")
    tool = ScheduleReminderTool(sched)

    result = asyncio.run(tool.execute(when_text="in 5 minutes"))
    assert result["scheduled"] is False
    assert result["needs_clarification"] == "message"
    assert "What should I remind" in result["prompt"]
    assert sched.list_tasks(status="active") == []


def test_schedule_reminder_tool_asks_for_ambiguous_time(tmp_path):
    """Unparseable/ambiguous time → needs_clarification='when', nothing scheduled."""
    import asyncio

    from app.tasks.persistent_scheduler import PersistentScheduler
    from app.tools.builtin.reminders import ScheduleReminderTool

    sched = PersistentScheduler(db_path=tmp_path / "tool3.db")
    tool = ScheduleReminderTool(sched)

    result = asyncio.run(
        tool.execute(message="drink water", when_text="kaalai 7 manikku")
    )
    assert result["scheduled"] is False
    assert result["needs_clarification"] == "when"
    assert sched.list_tasks(status="active") == []
    
    

def test_reminder_rejects_message_reuse(tmp_path):
    """A message matching an active reminder is rejected as reuse."""
    import asyncio
    from app.tasks.persistent_scheduler import PersistentScheduler
    from app.tools.builtin.reminders import ScheduleReminderTool

    sched = PersistentScheduler(db_path=tmp_path / "r.db")
    tool = ScheduleReminderTool(sched)

    # First reminder scheduled
    r1 = asyncio.run(tool.execute(
        message="drink water", when_text="in 1 hour"
    ))
    assert r1["scheduled"] is True

    # Second attempt reuses the same message — must be rejected
    r2 = asyncio.run(tool.execute(
        message="drink water", when_text="in 2 hours"
    ))
    assert r2["scheduled"] is False
    assert r2["needs_clarification"] == "message"
    assert "reuse" in r2["reason"]
    # No second reminder was created
    active = sched.list_tasks(status="active")
    assert len([t for t in active if t["prompt"] == "drink water"]) == 1


def test_reminder_two_turn_flow(tmp_path):
    """Turn 1 has time only; turn 2 has message only — schedules correctly."""
    import asyncio
    from app.tasks.persistent_scheduler import PersistentScheduler
    from app.tools.builtin.reminders import ScheduleReminderTool

    sched = PersistentScheduler(db_path=tmp_path / "r2.db")
    tool = ScheduleReminderTool(sched)

    # Turn 1: user said "naalaikku 10 manikku remind pannu" (no message)
    r1 = asyncio.run(tool.execute(when_text="in 2 hours"))
    assert r1["scheduled"] is False
    assert r1["needs_clarification"] == "message"
    assert sched.list_tasks(status="active") == []

    # Turn 2: user answers "drink water" — pending when_text is used
    r2 = asyncio.run(tool.execute(message="stretch legs"))
    assert r2["scheduled"] is True
    assert r2["message"] == "stretch legs"
    assert r2["when_text"] == "in 2 hours"
    # Pending cleared
    assert tool._pending_when_text is None
    
