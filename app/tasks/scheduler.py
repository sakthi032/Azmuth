"""Concurrency primitive for the task manager.

Kept as a separate abstraction so the manager reads as a façade and the
admission-control logic stays in one place. It is a thin, deliberately
explicit wrapper around :class:`asyncio.Semaphore` — the wrapper adds
introspection (``in_use``) that the raw semaphore does not expose.
"""

from __future__ import annotations

import asyncio
from types import TracebackType


class ConcurrencyGate:
    """Bounded async admission control.

    Usage::

        async with gate:
            ...  # at most ``limit`` coroutines may be in this block at once
    """

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        self._limit = limit
        self._semaphore = asyncio.Semaphore(limit)
        self._in_use = 0

    # -- introspection -----------------------------------------------------
    @property
    def limit(self) -> int:
        return self._limit

    @property
    def in_use(self) -> int:
        return self._in_use

    # -- context manager ---------------------------------------------------
    async def __aenter__(self) -> "ConcurrencyGate":
        await self._semaphore.acquire()
        self._in_use += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb  # unused
        self._in_use = max(0, self._in_use - 1)
        self._semaphore.release()