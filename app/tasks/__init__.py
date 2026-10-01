"""Background task subsystem public API.

    from app.tasks import TaskManager, Task, TaskStatus

Concrete work is supplied by callers — the manager accepts any awaitable
factory. This decouples the task subsystem from tools, memory, search and
automation: each of those submits its own work and stays unaware of the
manager internals.
"""

from app.tasks.manager import Factory, TaskManager, is_awaitable
from app.tasks.scheduler import ConcurrencyGate
from app.tasks.task import Task, TaskStatus, new_task_id

__all__ = [
    "ConcurrencyGate",
    "Factory",
    "Task",
    "TaskManager",
    "TaskStatus",
    "is_awaitable",
    "new_task_id",
]