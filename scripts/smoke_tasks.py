"""Manual smoke test for the task manager.

Run:  py -m scripts.smoke_tasks
"""

import asyncio

from app.tasks import TaskManager


async def slow_factory(task) -> str:
    await asyncio.sleep(1)
    return "slow done"


async def fast_factory(task) -> str:
    return "fast done"


async def main() -> None:
    mgr = TaskManager(max_concurrent=2)

    slow = mgr.submit(slow_factory, name="slow")
    fast = mgr.submit(fast_factory, name="fast")

    await asyncio.sleep(0.1)
    print("after 100 ms:")
    print("  slow ->", slow.status.value)
    print("  fast ->", fast.status.value, "|", fast.result)

    mgr.cancel(slow.id)              # <-- cancel on the manager, not the model
    await asyncio.sleep(0.05)
    print("after cancel:")
    print("  slow ->", slow.status.value)


if __name__ == "__main__":
    asyncio.run(main())