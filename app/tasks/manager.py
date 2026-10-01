"""Background task façade.

Everything that wants to run work without blocking the conversation goes
through :class:`TaskManager`. Its public surface:

* ``submit(factory, ...)``  — schedule an awaitable; returns immediately.
* ``get(task_id)``          — look up the current state.
* ``list(status=...)``      — enumerate tasks, newest first.
* ``cancel(task_id)``       — request cancellation.
* ``purge(max_age_seconds)`` — drop terminal tasks past a retention window.

The manager is a façade over three collaborators:

* a :class:`~app.tasks.scheduler.ConcurrencyGate` for bounded parallelism,
* the :class:`~app.core.events.EventBus` for lifecycle events,
* a pair of dicts mapping task ids to our :class:`Task` and the underlying
  :class:`asyncio.Task`.

Nothing here ever awaits the submitted work inline. The submission itself
returns in O(1) — that property is what lets the voice subsystem and the UI
keep responding while a long task runs.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from app.config.logging_setup import get_logger
from app.core.events import EventBus, EventName, emit
from app.tasks.scheduler import ConcurrencyGate
from app.tasks.task import Task, TaskStatus, new_task_id

logger = get_logger("tasks")


Factory = Callable[[Task], Awaitable[Any]]


class TaskManager:
    """Schedule, inspect, and cancel background tasks."""

    def __init__(
        self,
        max_concurrent: int,
        *,
        event_bus: EventBus | None = None,
    ) -> None:
        self._gate = ConcurrencyGate(max_concurrent)
        self._event_bus = event_bus
        self._tasks: dict[str, Task] = {}
        self._asyncio_tasks: dict[str, asyncio.Task[None]] = {}

    # -- introspection -----------------------------------------------------
    @property
    def max_concurrent(self) -> int:
        return self._gate.limit

    @property
    def in_use(self) -> int:
        return self._gate.in_use

    def count(self) -> int:
        return len(self._tasks)

    def active_count(self) -> int:
        return sum(
            1
            for t in self._tasks.values()
            if t.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
        )

    # -- submission --------------------------------------------------------
    def submit(
        self,
        factory: Factory,
        *,
        name: str | None = None,
        metadata: dict[str, Any] | None = None,
        cancellable: bool = True,
    ) -> Task:
        """Schedule ``factory`` and return immediately.

        ``factory`` is called with the :class:`Task` as its only argument
        once a concurrency slot is free. It must return an awaitable; the
        manager awaits it and records the result.

        Must be called from within a running asyncio event loop —
        :func:`asyncio.get_running_loop` enforces this with a clear error.
        """
        loop = asyncio.get_running_loop()  # raises RuntimeError outside a loop

        task = Task(
            id=new_task_id(),
            name=name or getattr(factory, "__name__", "task"),
            metadata=dict(metadata or {}),
            cancellable=cancellable,
        )
        self._tasks[task.id] = task

        aio_task = loop.create_task(
            self._run(task, factory),
            name=f"azmuth-task:{task.id}",
        )
        self._asyncio_tasks[task.id] = aio_task
        # The underlying asyncio.Task is only needed while the work is
        # pending/running. Once it finishes, drop the reference; the
        # :class:`Task` itself stays around until purge().
        aio_task.add_done_callback(lambda _t, tid=task.id: self._asyncio_tasks.pop(tid, None))

        logger.info(
            "task submitted",
            extra={
                "event": "TASK_SUBMITTED",
                "task_id": task.id,
                "task_name": task.name,
            },
        )
        return task

    # -- lookup ------------------------------------------------------------
    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def list(
        self, *, status: TaskStatus | None = None
    ) -> tuple[Task, ...]:
        """Return tasks newest first, optionally filtered by status."""
        candidates = [
            t for t in self._tasks.values()
            if status is None or t.status is status
        ]
        candidates.sort(key=lambda t: t.created_at, reverse=True)
        return tuple(candidates)

    # -- cancellation ------------------------------------------------------
    def cancel(self, task_id: str) -> bool:
        """Request cancellation of ``task_id``.

        Returns ``True`` if the request was accepted. Non-cancellable
        *running* tasks refuse cancellation and return ``False``; pending
        tasks are always cancellable (nothing has happened yet).
        """
        task = self._tasks.get(task_id)
        if task is None:
            return False
        if task.is_terminal:
            return False
        if task.status is TaskStatus.RUNNING and not task.cancellable:
            logger.warning(
                "refusing to cancel non-cancellable running task",
                extra={
                    "event": "TASK_CANCEL_REFUSED",
                    "task_id": task.id,
                },
            )
            return False

        task.cancel_requested = True
        aio = self._asyncio_tasks.get(task_id)
        if aio is not None and not aio.done():
            aio.cancel()
        else:
            # No live asyncio task — mark the task terminal directly so the
            # public state stays consistent.
            task._mark_cancelled()
        logger.info(
            "task cancel requested",
            extra={"event": "TASK_CANCEL_REQUESTED", "task_id": task.id},
        )
        return True

    # -- maintenance -------------------------------------------------------
    def purge(self, max_age_seconds: float) -> int:
        """Delete terminal tasks older than ``max_age_seconds``.

        Pending/running tasks are never purged. Returns the number removed.
        """
        if max_age_seconds < 0:
            raise ValueError("max_age_seconds must be non-negative")
        cutoff = [t for t in self._tasks.values()
                  if t.is_terminal and t.age_seconds > max_age_seconds]
        for task in cutoff:
            self._tasks.pop(task.id, None)
            self._asyncio_tasks.pop(task.id, None)
        if cutoff:
            logger.info(
                "tasks purged",
                extra={"event": "TASKS_PURGED", "count": len(cutoff)},
            )
        return len(cutoff)

    def clear(self) -> None:
        """Drop all references. Does not cancel running work. Test aid."""
        self._tasks.clear()
        self._asyncio_tasks.clear()

    # -- internals ---------------------------------------------------------
    async def _run(self, task: Task, factory: Factory) -> None:
        try:
            async with self._gate:
                if task.is_terminal:
                    return  # cancelled before reaching the front of the queue
                task._mark_running()
                await self._emit_started(task)

                try:
                    result = await factory(task)
                except asyncio.CancelledError:
                    task._mark_cancelled()
                except Exception as exc:  # noqa: BLE001 - last line of defence
                    task._mark_failed(exc)
                else:
                    task._mark_completed(result)    
        except asyncio.CancelledError:
            # Cancelled while waiting on the gate, before the factory ran.
            if not task.is_terminal:
                task._mark_cancelled()
        finally:
            if task.status is not TaskStatus.PENDING:
                await self._emit_finished(task)

    async def _emit_started(self, task: Task) -> None:
        await self._emit(
            EventName.TASK_STARTED,
            task_id=task.id,
            task_name=task.name,
            status=task.status.value,
        )

    async def _emit_finished(self, task: Task) -> None:
        # The spec's event vocabulary has COMPLETED and FAILED, not CANCELLED.
        # A cancelled task is emitted as TASK_FAILED with status="cancelled"
        # so listeners see one consistent "this task stopped" signal.
        event = (
            EventName.TASK_COMPLETED
            if task.status is TaskStatus.COMPLETED
            else EventName.TASK_FAILED
        )
        payload: dict[str, Any] = {
            "task_id": task.id,
            "task_name": task.name,
            "status": task.status.value,
        }
        if task.status is TaskStatus.COMPLETED:
            payload["duration_ms"] = round(task.duration_ms or 0.0, 3)
        elif task.status is TaskStatus.FAILED:
            payload["error"] = task.error
        await self._emit(event, **payload)

    async def _emit(self, event: EventName, **payload: Any) -> None:
        if self._event_bus is None:
            return
        try:
            await emit(self._event_bus, event, source="tasks", **payload)
        except Exception:  # noqa: BLE001 - event delivery must never break the pipeline
            logger.exception(
                "failed to emit task event",
                extra={"event": event.value},
            )


# Re-export for callers that prefer importing from the manager module.
__all__ = ["Factory", "TaskManager"]


def is_awaitable(value: object) -> bool:
    """Small helper used only in tests and diagnostics."""
    return inspect.isawaitable(value)