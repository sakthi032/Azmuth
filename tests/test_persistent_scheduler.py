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
