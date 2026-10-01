from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from app.core.events import Event, EventBus, EventName
from app.tasks import (
    ConcurrencyGate,
    Task,
    TaskManager,
    TaskStatus,
    new_task_id,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _wait_until_terminal(
    manager: TaskManager, task_id: str, *, timeout: float = 2.0
) -> Task:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = manager.get(task_id)
        if task is not None and task.is_terminal:
            return task
        await asyncio.sleep(0)
    raise TimeoutError(f"task {task_id} did not finish within {timeout}s")


# ---------------------------------------------------------------------------
# Task model
# ---------------------------------------------------------------------------
def test_new_task_id_is_unique() -> None:
    assert new_task_id() != new_task_id()


def test_task_starts_pending() -> None:
    t = Task(id="t1", name="t1")
    assert t.status is TaskStatus.PENDING
    assert t.is_terminal is False
    assert t.started_at is None
    assert t.finished_at is None
    assert t.duration_ms is None
    assert t.progress is None


def test_task_terminal_states_are_recognised() -> None:
    for status, expected in [
        (TaskStatus.PENDING, False),
        (TaskStatus.RUNNING, False),
        (TaskStatus.COMPLETED, True),
        (TaskStatus.FAILED, True),
        (TaskStatus.CANCELLED, True),
    ]:
        t = Task(id="x", name="x", status=status)
        assert t.is_terminal is expected


def test_task_report_progress_validates_range() -> None:
    t = Task(id="t1", name="t1")
    with pytest.raises(ValueError, match="progress must be"):
        t.report_progress(1.5)
    with pytest.raises(ValueError, match="progress must be"):
        t.report_progress(-0.1)


def test_task_report_progress_sets_values() -> None:
    t = Task(id="t1", name="t1")
    t.report_progress(0.5, "halfway")
    assert t.progress == 0.5
    assert t.progress_message == "halfway"


def test_task_as_dict_is_json_ready() -> None:
    import json
    t = Task(id="t1", name="name", metadata={"k": "v"})
    payload = json.loads(json.dumps(t.as_dict()))
    assert payload["id"] == "t1"
    assert payload["status"] == "pending"
    assert payload["metadata"] == {"k": "v"}


# ---------------------------------------------------------------------------
# ConcurrencyGate
# ---------------------------------------------------------------------------
def test_gate_rejects_limit_below_one() -> None:
    with pytest.raises(ValueError, match="limit must be"):
        ConcurrencyGate(0)


def test_gate_tracks_in_use_and_releases() -> None:
    async def main() -> None:
        gate = ConcurrencyGate(2)
        assert gate.in_use == 0
        async with gate:
            assert gate.in_use == 1
            async with gate:
                assert gate.in_use == 2
            assert gate.in_use == 1
        assert gate.in_use == 0

    asyncio.run(main())


def test_gate_blocks_above_limit() -> None:
    order: list[str] = []

    async def worker(gate: ConcurrencyGate, tag: str) -> None:
        async with gate:
            order.append(f"enter:{tag}")
            await asyncio.sleep(0.02)
            order.append(f"leave:{tag}")

    async def main() -> None:
        gate = ConcurrencyGate(1)
        await asyncio.gather(worker(gate, "a"), worker(gate, "b"), worker(gate, "c"))

    asyncio.run(main())

    # Serialised: enter/leave alternate.
    assert order == [
        "enter:a", "leave:a", "enter:b", "leave:b", "enter:c", "leave:c",
    ]


# ---------------------------------------------------------------------------
# TaskManager — submit / complete
# ---------------------------------------------------------------------------
def test_submit_requires_running_loop() -> None:
    mgr = TaskManager(max_concurrent=1)
    with pytest.raises(RuntimeError, match="no running event loop|running loop"):
        mgr.submit(lambda _t: asyncio.sleep(0))


def test_successful_task_ends_completed() -> None:
    async def factory(task: Task) -> str:
        return "done"

    async def main() -> Task:
        mgr = TaskManager(max_concurrent=2)
        task = mgr.submit(factory, name="simple")
        assert task.status is TaskStatus.PENDING
        return await _wait_until_terminal(mgr, task.id)

    result = asyncio.run(main())
    assert result.status is TaskStatus.COMPLETED
    assert result.result == "done"
    assert result.started_at is not None
    assert result.finished_at is not None
    assert result.duration_ms is not None and result.duration_ms >= 0


def test_failing_task_ends_failed() -> None:
    async def factory(task: Task) -> None:
        raise RuntimeError("boom")

    async def main() -> Task:
        mgr = TaskManager(max_concurrent=2)
        task = mgr.submit(factory, name="fail")
        return await _wait_until_terminal(mgr, task.id)

    result = asyncio.run(main())
    assert result.status is TaskStatus.FAILED
    assert "boom" in (result.error or "")
    assert result.error_traceback is not None
    assert "RuntimeError" in result.error_traceback


def test_factory_receives_the_task_object() -> None:
    seen: dict[str, Any] = {}

    async def factory(task: Task) -> str:
        seen["id"] = task.id
        seen["name"] = task.name
        return "ok"

    async def main() -> Task:
        mgr = TaskManager(max_concurrent=2)
        task = mgr.submit(factory, name="identity")
        return await _wait_until_terminal(mgr, task.id)

    result = asyncio.run(main())
    assert seen["id"] == result.id
    assert seen["name"] == "identity"


def test_factory_can_report_progress() -> None:
    async def factory(task: Task) -> str:
        task.report_progress(0.3, "first")
        await asyncio.sleep(0)
        task.report_progress(0.7, "second")
        return "ok"

    async def main() -> Task:
        mgr = TaskManager(max_concurrent=2)
        task = mgr.submit(factory, name="progress")
        return await _wait_until_terminal(mgr, task.id)

    result = asyncio.run(main())
    # _mark_completed forces progress to 1.0
    assert result.progress == 1.0
    assert result.status is TaskStatus.COMPLETED


def test_non_awaitable_factory_result_fails_task() -> None:
    def factory(task: Task) -> str:  # type: ignore[return-value]
        return "not an awaitable"

    async def main() -> Task:
        mgr = TaskManager(max_concurrent=2)
        task = mgr.submit(factory, name="bad")  # type: ignore[arg-type]
        return await _wait_until_terminal(mgr, task.id)

    result = asyncio.run(main())
    assert result.status is TaskStatus.FAILED
    assert result.error is not None


# ---------------------------------------------------------------------------
# TaskManager — lookup
# ---------------------------------------------------------------------------
def test_get_returns_none_for_missing() -> None:
    mgr = TaskManager(max_concurrent=1)
    assert mgr.get("nope") is None


def test_list_returns_newest_first() -> None:
    async def main() -> list[Task]:
        mgr = TaskManager(max_concurrent=2)
        for i in range(3):
            mgr.submit(lambda _t: asyncio.sleep(0), name=f"t{i}")
            await asyncio.sleep(0.001)  # ensure distinct created_at
        return list(mgr.list())

    tasks = asyncio.run(main())
    assert [t.name for t in tasks] == ["t2", "t1", "t0"]


def test_list_filtered_by_status() -> None:
    async def ok_factory(task: Task) -> str:
        return "ok"

    async def fail_factory(task: Task) -> None:
        raise RuntimeError("x")

    async def main() -> TaskManager:
        mgr = TaskManager(max_concurrent=2)
        a = mgr.submit(ok_factory, name="a")
        b = mgr.submit(fail_factory, name="b")
        await _wait_until_terminal(mgr, a.id)
        await _wait_until_terminal(mgr, b.id)
        return mgr

    mgr = asyncio.run(main())
    completed = mgr.list(status=TaskStatus.COMPLETED)
    failed = mgr.list(status=TaskStatus.FAILED)
    assert [t.name for t in completed] == ["a"]
    assert [t.name for t in failed] == ["b"]


def test_count_and_active_count() -> None:
    async def main() -> tuple[int, int]:
        mgr = TaskManager(max_concurrent=1)
        mgr.submit(lambda _t: asyncio.sleep(0.05), name="a")
        mgr.submit(lambda _t: asyncio.sleep(0.05), name="b")
        await asyncio.sleep(0)  # let a move to RUNNING, b stays PENDING
        return mgr.count(), mgr.active_count()

    total, active = asyncio.run(main())
    assert total == 2
    assert active >= 1


# ---------------------------------------------------------------------------
# TaskManager — cancellation
# ---------------------------------------------------------------------------
def test_cancel_missing_returns_false() -> None:
    mgr = TaskManager(max_concurrent=1)
    assert mgr.cancel("nope") is False


def test_cancel_pending_task() -> None:
    async def slow(task: Task) -> None:
        await asyncio.sleep(0.2)

    async def main() -> Task:
        mgr = TaskManager(max_concurrent=1)
        first = mgr.submit(slow, name="first")
        second = mgr.submit(slow, name="second")

        # First is running; second is pending.
        await asyncio.sleep(0)
        assert mgr.cancel(second.id) is True

        await _wait_until_terminal(mgr, second.id)
        mgr.cancel(first.id)  # cleanup
        await _wait_until_terminal(mgr, first.id)
        return second

    second = asyncio.run(main())
    assert second.status is TaskStatus.CANCELLED


def test_cancel_running_task() -> None:
    async def slow(task: Task) -> None:
        await asyncio.sleep(10)

    async def main() -> Task:
        mgr = TaskManager(max_concurrent=2)
        task = mgr.submit(slow, name="slow")
        await asyncio.sleep(0)
        assert mgr.cancel(task.id) is True
        return await _wait_until_terminal(mgr, task.id)

    result = asyncio.run(main())
    assert result.status is TaskStatus.CANCELLED
    assert result.cancel_requested is True


def test_cancel_terminal_returns_false() -> None:
    async def fast(task: Task) -> str:
        return "done"

    async def main() -> TaskManager:
        mgr = TaskManager(max_concurrent=2)
        task = mgr.submit(fast, name="t")
        await _wait_until_terminal(mgr, task.id)
        assert mgr.cancel(task.id) is False
        return mgr

    asyncio.run(main())


def test_cancel_non_cancellable_running_refused() -> None:
    async def slow(task: Task) -> None:
        await asyncio.sleep(0.05)

    async def main() -> Task:
        mgr = TaskManager(max_concurrent=2)
        task = mgr.submit(slow, name="t", cancellable=False)
        await asyncio.sleep(0)
        assert mgr.cancel(task.id) is False
        return await _wait_until_terminal(mgr, task.id)

    result = asyncio.run(main())
    assert result.status is TaskStatus.COMPLETED  # ran to completion


def test_cancel_non_cancellable_pending_works() -> None:
    async def slow(task: Task) -> None:
        await asyncio.sleep(0.2)

    async def main() -> Task:
        mgr = TaskManager(max_concurrent=1)
        blocker = mgr.submit(slow, name="blocker")
        pending = mgr.submit(slow, name="pending", cancellable=False)
        await asyncio.sleep(0)

        assert mgr.cancel(pending.id) is True
        await _wait_until_terminal(mgr, pending.id)
        mgr.cancel(blocker.id)  # cleanup
        await _wait_until_terminal(mgr, blocker.id)
        return pending

    result = asyncio.run(main())
    assert result.status is TaskStatus.CANCELLED


# ---------------------------------------------------------------------------
# TaskManager — concurrency
# ---------------------------------------------------------------------------
def test_parallelism_respects_limit() -> None:
    observed = {"current": 0, "max": 0}

    async def worker(task: Task) -> None:
        observed["current"] += 1
        observed["max"] = max(observed["max"], observed["current"])
        await asyncio.sleep(0.02)
        observed["current"] -= 1

    async def main() -> None:
        mgr = TaskManager(max_concurrent=2)
        tasks = [mgr.submit(worker, name=f"t{i}") for i in range(6)]
        for t in tasks:
            await _wait_until_terminal(mgr, t.id)

    asyncio.run(main())
    assert observed["max"] <= 2
    # Sanity: it did run more than one at a time.
    assert observed["max"] >= 2


def test_serial_execution_when_limit_is_one() -> None:
    order: list[str] = []

    async def worker(task: Task) -> None:
        order.append(f"enter:{task.name}")
        await asyncio.sleep(0.01)
        order.append(f"leave:{task.name}")

    async def main() -> None:
        mgr = TaskManager(max_concurrent=1)
        tasks = [mgr.submit(worker, name=chr(ord("a") + i)) for i in range(3)]
        for t in tasks:
            await _wait_until_terminal(mgr, t.id)

    asyncio.run(main())
    assert order == [
        "enter:a", "leave:a", "enter:b", "leave:b", "enter:c", "leave:c",
    ]


def test_second_request_not_blocked_by_slow_first() -> None:
    async def slow(task: Task) -> None:
        await asyncio.sleep(0.2)

    async def fast(task: Task) -> str:
        return "quick"

    async def main() -> tuple[Task, Task]:
        mgr = TaskManager(max_concurrent=2)
        first = mgr.submit(slow, name="slow")
        second = mgr.submit(fast, name="fast")

        # Give the fast one just enough time to complete while slow runs.
        await asyncio.sleep(0.05)

        assert second.status is TaskStatus.COMPLETED
        assert first.status is TaskStatus.RUNNING

        mgr.cancel(first.id)  # cleanup
        await _wait_until_terminal(mgr, first.id)
        return first, second

    first, second = asyncio.run(main())
    assert second.result == "quick"


# ---------------------------------------------------------------------------
# TaskManager — purge
# ---------------------------------------------------------------------------
def test_purge_removes_old_terminal_tasks() -> None:
    async def fast(task: Task) -> str:
        return "ok"

    async def main() -> tuple[TaskManager, Task, Task]:
        mgr = TaskManager(max_concurrent=2)
        a = mgr.submit(fast, name="a")
        b = mgr.submit(fast, name="b")
        await _wait_until_terminal(mgr, a.id)
        await _wait_until_terminal(mgr, b.id)
        # Backdate b so a purge with a zero threshold removes both? No —
        # age_seconds is computed against "now". Instead just purge
        # everything: threshold 0 removes any terminal task whose age is
        # strictly greater than 0, which is essentially all of them after
        # a small pause.
        await asyncio.sleep(0.01)
        return mgr, a, b

    mgr, _, _ = asyncio.run(main())
    assert mgr.count() == 2
    removed = mgr.purge(max_age_seconds=0)
    assert removed == 2
    assert mgr.count() == 0


def test_purge_keeps_pending_and_running() -> None:
    async def slow(task: Task) -> None:
        await asyncio.sleep(0.1)

    async def main() -> tuple[TaskManager, Task, Task]:
        mgr = TaskManager(max_concurrent=1)
        running = mgr.submit(slow, name="running")
        pending = mgr.submit(slow, name="pending")
        await asyncio.sleep(0)
        # Purge must not remove pending or running tasks.
        mgr.purge(max_age_seconds=0)
        assert mgr.count() == 2
        mgr.cancel(pending.id)
        mgr.cancel(running.id)
        await _wait_until_terminal(mgr, pending.id)
        await _wait_until_terminal(mgr, running.id)
        return mgr, running, pending

    asyncio.run(main())


def test_purge_rejects_negative_age() -> None:
    mgr = TaskManager(max_concurrent=1)
    with pytest.raises(ValueError, match="non-negative"):
        mgr.purge(max_age_seconds=-1)


# ---------------------------------------------------------------------------
# TaskManager — events
# ---------------------------------------------------------------------------
def test_events_started_and_completed() -> None:
    async def factory(task: Task) -> str:
        return "ok"

    async def main() -> list[Event]:
        bus = EventBus()
        seen: list[Event] = []
        bus.subscribe(EventName.TASK_STARTED, seen.append)
        bus.subscribe(EventName.TASK_COMPLETED, seen.append)

        mgr = TaskManager(max_concurrent=2, event_bus=bus)
        task = mgr.submit(factory, name="evented")
        await _wait_until_terminal(mgr, task.id)
        await asyncio.sleep(0)
        return seen

    events = asyncio.run(main())
    names = [e.name for e in events]
    assert EventName.TASK_STARTED in names
    assert EventName.TASK_COMPLETED in names
    assert names.index(EventName.TASK_STARTED) < names.index(EventName.TASK_COMPLETED)


def test_events_failed() -> None:
    async def factory(task: Task) -> None:
        raise RuntimeError("bad")

    async def main() -> list[Event]:
        bus = EventBus()
        seen: list[Event] = []
        bus.subscribe(EventName.TASK_FAILED, seen.append)

        mgr = TaskManager(max_concurrent=2, event_bus=bus)
        task = mgr.submit(factory, name="failing")
        await _wait_until_terminal(mgr, task.id)
        await asyncio.sleep(0)
        return seen

    events = asyncio.run(main())
    assert events and events[0].payload["status"] == "failed"


def test_event_payload_has_task_id_and_status() -> None:
    async def factory(task: Task) -> str:
        return "ok"

    async def main() -> list[Event]:
        bus = EventBus()
        seen: list[Event] = []
        bus.subscribe(EventName.TASK_STARTED, seen.append)

        mgr = TaskManager(max_concurrent=2, event_bus=bus)
        task = mgr.submit(factory, name="payload")
        await _wait_until_terminal(mgr, task.id)
        await asyncio.sleep(0)
        return seen

    events = asyncio.run(main())
    payload = events[0].payload
    assert payload["task_name"] == "payload"
    assert payload["status"] == "running"
    assert "task_id" in payload


def test_event_bus_failure_does_not_break_task() -> None:
    async def factory(task: Task) -> str:
        return "ok"

    class BrokenBus(EventBus):
        async def publish(self, event: Event) -> None:  # type: ignore[override]
            raise RuntimeError("bus down")

    async def main() -> Task:
        mgr = TaskManager(max_concurrent=2, event_bus=BrokenBus())
        task = mgr.submit(factory, name="resilient")
        return await _wait_until_terminal(mgr, task.id)

    result = asyncio.run(main())
    assert result.status is TaskStatus.COMPLETED
    assert result.result == "ok"


# ---------------------------------------------------------------------------
# Integration — anything awaitable can be submitted
# ---------------------------------------------------------------------------
def test_can_submit_a_tool_invocation() -> None:
    """The task manager can host a ToolManager.invoke without modification."""
    from app.security import Capability, PermissionMode, Principal
    from app.security.modes import AssuranceLevel
    from app.security.permissions import PermissionManager
    from app.tools import EchoTool, ToolManager

    async def main() -> Task:
        permissions = PermissionManager()
        tools = ToolManager(permissions)
        tools.register(EchoTool())

        principal = Principal(
            user_id="creator",
            mode=PermissionMode.CREATOR,
            assurance=AssuranceLevel.HIGH,
        )

        mgr = TaskManager(max_concurrent=2)
        task = mgr.submit(
            lambda _t: tools.invoke(
                "echo", {"message": "background"}, principal=principal
            ),
            name="run echo",
            metadata={"tool": "echo"},
        )
        return await _wait_until_terminal(mgr, task.id)

    result = asyncio.run(main())
    assert result.status is TaskStatus.COMPLETED
    assert result.result.output == "background"
    assert result.metadata["tool"] == "echo"