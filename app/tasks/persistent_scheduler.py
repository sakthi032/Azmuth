"""Persistent task scheduler backed by SQLite.

Supports three schedule types:
* ``once``     — run at a specific UTC datetime (ISO-8601 string)
* ``interval`` — run every N seconds
* ``cron``     — run on a cron expression (requires ``croniter``)

Tasks survive process restarts: on startup, overdue tasks execute
immediately (or on the next tick, see ``restore_on_start``).

Usage::

    scheduler = PersistentScheduler(db_path="data/scheduler.db")
    scheduler.start(executor=brain_callback)

    task_id = scheduler.add_task(
        prompt="Send morning briefing",
        schedule_type="cron",
        schedule_value="0 8 * * *",
        agent="default",
    )

    scheduler.stop()

The ``executor`` callback is an *async* coroutine::

    async def executor(prompt: str, agent: str, task_id: str) -> None:
        ...
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional croniter
# ---------------------------------------------------------------------------
try:
    from croniter import croniter  # type: ignore[import-untyped]

    HAS_CRONITER = True
except ModuleNotFoundError:
    HAS_CRONITER = False
    logger.warning(
        "croniter not installed — 'cron' schedule type unavailable. "
        "Install with: pip install croniter"
    )

# ---------------------------------------------------------------------------
# SQL schema
# ---------------------------------------------------------------------------
_DDL = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id            TEXT PRIMARY KEY,
    prompt        TEXT NOT NULL,
    schedule_type TEXT NOT NULL CHECK(schedule_type IN ('once','interval','cron')),
    schedule_value TEXT NOT NULL,
    agent         TEXT NOT NULL DEFAULT 'default',
    status        TEXT NOT NULL DEFAULT 'active'
                       CHECK(status IN ('active','paused','completed','error')),
    next_run      REAL,          -- Unix timestamp (UTC)
    last_run      REAL,          -- Unix timestamp (UTC)
    run_count     INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    metadata      TEXT NOT NULL DEFAULT '{}',
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS task_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    ran_at     REAL NOT NULL,
    status     TEXT NOT NULL,
    error      TEXT,
    duration_s REAL
);

CREATE INDEX IF NOT EXISTS idx_tasks_next_run ON scheduled_tasks(next_run)
    WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_history_task   ON task_history(task_id);
"""

ExecutorCallback = Callable[[str, str, str], Awaitable[None]]


class PersistentScheduler:
    """SQLite-backed persistent task scheduler.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file. Created automatically.
    poll_interval:
        How often (seconds) the background thread checks for due tasks.
    """

    def __init__(
        self,
        db_path: str | Path = "data/scheduler.db",
        poll_interval: float = 30.0,
    ) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._poll_interval = poll_interval
        self._executor: ExecutorCallback | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._con = self._connect()
        self._init_db()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(
        self,
        executor: ExecutorCallback,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        """Start the background scheduler thread.

        Parameters
        ----------
        executor:
            Async coroutine called with ``(prompt, agent, task_id)`` for
            each due task.
        loop:
            Running event loop to submit coroutines to. If *None*, the
            current running loop is used (``asyncio.get_event_loop()``).
        """
        self._executor = executor
        self._loop = loop or asyncio.get_event_loop()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="PersistentScheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info("PersistentScheduler started (db=%s)", self._db_path)

    def stop(self) -> None:
        """Signal the background thread to stop and wait for it."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("PersistentScheduler stopped")

    # ------------------------------------------------------------------
    # Task CRUD
    # ------------------------------------------------------------------

    def add_task(
        self,
        prompt: str,
        schedule_type: str,
        schedule_value: str,
        agent: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Create and persist a new scheduled task.

        Returns the new task ID.

        Raises
        ------
        ValueError
            If ``schedule_type`` is ``'cron'`` but ``croniter`` is not
            installed, or if the cron expression is invalid.
        """
        if schedule_type not in {"once", "interval", "cron"}:
            raise ValueError(
                f"Invalid schedule_type={schedule_type!r}. "
                "Must be 'once', 'interval', or 'cron'."
            )
        if schedule_type == "cron" and not HAS_CRONITER:
            raise ValueError(
                "schedule_type='cron' requires croniter. "
                "Install with: pip install croniter"
            )

        task_id = str(uuid.uuid4())
        now = _utcnow_ts()
        next_run = self._compute_next_run(schedule_type, schedule_value, now)

        with self._con:
            self._con.execute(
                """
                INSERT INTO scheduled_tasks
                    (id, prompt, schedule_type, schedule_value, agent,
                     status, next_run, last_run, metadata, created_at)
                VALUES (?,?,?,?,?,  'active',?,NULL,?,?)
                """,
                (
                    task_id,
                    prompt,
                    schedule_type,
                    schedule_value,
                    agent,
                    next_run,
                    json.dumps(metadata or {}),
                    now,
                ),
            )

        logger.info(
            "Scheduled task %s (%s=%r) next_run=%s",
            task_id,
            schedule_type,
            schedule_value,
            datetime.fromtimestamp(next_run, tz=timezone.utc).isoformat()
            if next_run
            else "N/A",
        )
        return task_id

    def cancel_task(self, task_id: str) -> bool:
        """Mark a task as completed (cancelled). Returns True if found."""
        with self._con:
            cur = self._con.execute(
                "UPDATE scheduled_tasks SET status='completed' WHERE id=? AND status='active'",
                (task_id,),
            )
        return cur.rowcount > 0

    def pause_task(self, task_id: str) -> bool:
        """Pause an active task. Returns True if found."""
        with self._con:
            cur = self._con.execute(
                "UPDATE scheduled_tasks SET status='paused' WHERE id=? AND status='active'",
                (task_id,),
            )
        return cur.rowcount > 0

    def resume_task(self, task_id: str) -> bool:
        """Resume a paused task. Returns True if found."""
        row = self._con.execute(
            "SELECT schedule_type, schedule_value FROM scheduled_tasks WHERE id=? AND status='paused'",
            (task_id,),
        ).fetchone()
        if not row:
            return False
        next_run = self._compute_next_run(row[0], row[1], _utcnow_ts())
        with self._con:
            self._con.execute(
                "UPDATE scheduled_tasks SET status='active', next_run=? WHERE id=?",
                (next_run, task_id),
            )
        return True

    def list_tasks(self, status: str | None = None) -> list[dict[str, Any]]:
        """Return tasks as a list of dicts. Filter by ``status`` if given."""
        if status:
            rows = self._con.execute(
                "SELECT * FROM scheduled_tasks WHERE status=? ORDER BY next_run",
                (status,),
            ).fetchall()
        else:
            rows = self._con.execute(
                "SELECT * FROM scheduled_tasks ORDER BY next_run"
            ).fetchall()
        cols = [d[0] for d in self._con.execute("SELECT * FROM scheduled_tasks LIMIT 0").description]
        return [dict(zip(cols, row)) for row in rows]

    def task_history(self, task_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Return execution history for a task (most recent first)."""
        rows = self._con.execute(
            "SELECT * FROM task_history WHERE task_id=? ORDER BY ran_at DESC LIMIT ?",
            (task_id, limit),
        ).fetchall()
        cols = [d[0] for d in self._con.execute("SELECT * FROM task_history LIMIT 0").description]
        return [dict(zip(cols, row)) for row in rows]

    # ------------------------------------------------------------------
    # Private — polling loop
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        """Background thread: check for due tasks every ``poll_interval``."""
        logger.debug("PersistentScheduler poll loop started")
        while not self._stop_event.is_set():
            with contextlib.suppress(Exception):
                self._tick()
            self._stop_event.wait(self._poll_interval)
        logger.debug("PersistentScheduler poll loop exited")

    def _tick(self) -> None:
        """Find all due tasks and submit them to the executor."""
        now = _utcnow_ts()
        rows = self._con.execute(
            """
            SELECT id, prompt, schedule_type, schedule_value, agent
            FROM   scheduled_tasks
            WHERE  status = 'active'
              AND  next_run IS NOT NULL
              AND  next_run <= ?
            ORDER BY next_run
            """,
            (now,),
        ).fetchall()

        for task_id, prompt, stype, sval, agent in rows:
            self._dispatch(task_id, prompt, stype, sval, agent, now)

    def _dispatch(
        self,
        task_id: str,
        prompt: str,
        schedule_type: str,
        schedule_value: str,
        agent: str,
        now: float,
    ) -> None:
        """Submit a due task to the async executor."""
        # Compute the next_run BEFORE dispatching so we don't double-fire
        if schedule_type == "once":
            next_run = None
            new_status = "completed"
        else:
            next_run = self._compute_next_run(schedule_type, schedule_value, now)
            new_status = "active"

        with self._con:
            self._con.execute(
                """
                UPDATE scheduled_tasks
                SET last_run=?, next_run=?, run_count=run_count+1, status=?
                WHERE id=?
                """,
                (now, next_run, new_status, task_id),
            )

        if self._executor and self._loop:
            future = asyncio.run_coroutine_threadsafe(
                self._run_executor(task_id, prompt, agent, now),
                self._loop,
            )
            # Fire-and-forget; errors logged inside _run_executor
            future.add_done_callback(lambda f: f.exception() and None)

    async def _run_executor(
        self,
        task_id: str,
        prompt: str,
        agent: str,
        dispatch_ts: float,
    ) -> None:
        """Call the user-supplied executor and record the result."""
        t0 = _utcnow_ts()
        error: str | None = None
        try:
            assert self._executor is not None
            await self._executor(prompt, agent, task_id)
            status = "ok"
        except Exception as exc:
            error = str(exc)
            status = "error"
            logger.error("Scheduled task %s failed: %s", task_id, exc)
            with self._con:
                self._con.execute(
                    "UPDATE scheduled_tasks SET last_error=? WHERE id=?",
                    (error, task_id),
                )

        duration = _utcnow_ts() - t0
        with self._con:
            self._con.execute(
                "INSERT INTO task_history(task_id,ran_at,status,error,duration_s) VALUES(?,?,?,?,?)",
                (task_id, dispatch_ts, status, error, duration),
            )

    # ------------------------------------------------------------------
    # Private — helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self._db_path), check_same_thread=False)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    def _init_db(self) -> None:
        with self._con:
            self._con.executescript(_DDL)

    @staticmethod
    def _compute_next_run(
        schedule_type: str,
        schedule_value: str,
        from_ts: float,
    ) -> float | None:
        """Return the next run timestamp (UTC) or None for 'once' past events."""
        now_dt = datetime.fromtimestamp(from_ts, tz=timezone.utc)

        if schedule_type == "once":
            try:
                target = datetime.fromisoformat(schedule_value)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=timezone.utc)
                return target.timestamp()
            except ValueError:
                # Treat as relative seconds from now
                return from_ts + float(schedule_value)

        elif schedule_type == "interval":
            seconds = float(schedule_value)
            return from_ts + seconds

        elif schedule_type == "cron":
            if not HAS_CRONITER:
                raise RuntimeError("croniter not installed")
            itr = croniter(schedule_value, now_dt)
            return itr.get_next(datetime).timestamp()

        return None


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _utcnow_ts() -> float:
    return datetime.now(tz=timezone.utc).timestamp()
