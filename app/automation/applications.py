"""Application launch and process management.

Everything here executes an external command — and every external command
is an **argv list**, never a shell string. There is no ``shell=True`` in
this module and there is no code path that interpolates user input into a
command line.
"""

from __future__ import annotations

import asyncio
import csv
import io
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.config.logging_setup import get_logger
from app.core.errors import AutomationError
from app.automation.base import AppLaunchResult, ProcessInfo
from app.automation.policy import ApplicationPolicy

logger = get_logger("automation.applications")

#: Hard cap on how long a launch may take before we give up on it. This is
#: a *startup* timeout, not a runtime timeout — the launched process keeps
#: running after we return.
_LAUNCH_TIMEOUT_SECONDS = 10.0

#: Same idea for the process-list and kill commands.
_PROBE_TIMEOUT_SECONDS = 5.0


class ApplicationController:
    """Launch, inspect, and terminate applications and processes."""

    def __init__(self, policy: ApplicationPolicy | None = None) -> None:
        self._policy = policy if policy is not None else ApplicationPolicy.default_windows()

    # -- discovery ---------------------------------------------------------
    @property
    def policy(self) -> ApplicationPolicy:
        return self._policy

    def known_aliases(self) -> tuple[str, ...]:
        return self._policy.known_aliases()

    # -- launch ------------------------------------------------------------
    async def open(self, alias: str) -> AppLaunchResult:
        """Launch the allowlisted application ``alias``.

        Raises :class:`AutomationError` if the alias is unknown, the
        resolved executable does not exist, or the process fails to start.
        """
        executable: Path = self._policy.resolve(alias)

        try:
            process = await asyncio.to_thread(
                _spawn_detached, str(executable)
            )
        except FileNotFoundError as exc:
            raise AutomationError(
                f"executable for {alias!r} disappeared: {exc}"
            ) from exc
        except OSError as exc:
            raise AutomationError(
                f"failed to launch {alias!r}: {exc}"
            ) from exc

        logger.info(
            "application launched",
            extra={
                "event": "APPLICATION_LAUNCHED",
                "alias": alias,
                "pid": process.pid,
                "executable": str(executable),
            },
        )
        return AppLaunchResult(
            alias=alias,
            pid=process.pid,
            executable=str(executable),
        )

    # -- terminate ---------------------------------------------------------
    async def close(self, identifier: str) -> int:
        """Terminate by PID (``"1234"``) or image name (``"notepad.exe"``).

        Returns the number of processes the OS reported as terminated.
        """
        identifier = identifier.strip()
        if not identifier:
            raise AutomationError("identifier must be non-empty")

        if identifier.isdigit():
            argv = ["taskkill", "/PID", identifier, "/F"]
        else:
            # Image-name termination. No `/T` (tree kill) and no user
            # escalation — we are only allowed to kill processes we own.
            argv = ["taskkill", "/IM", identifier, "/F"]

        # Exit code 1 is taskkill's "could not terminate" (packaged apps,
        # protected processes). It is a valid, expected outcome — the
        # close is best-effort, not a contract.
        stdout = await _run_capture(
            argv,
            timeout=_PROBE_TIMEOUT_SECONDS,
            accept_exit_codes=(0, 1, 128),
        )
        count = stdout.count("SUCCESS:")
        if count == 0:
            # taskkill returns 0 even when nothing matched; treat that as
            # "no processes terminated" so the caller can distinguish.
            logger.info(
                "no processes terminated",
                extra={
                    "event": "PROCESS_CLOSE_NO_MATCH",
                    "identifier": identifier,
                },
            )
        else:
            logger.info(
                "processes terminated",
                extra={
                    "event": "PROCESS_CLOSED",
                    "identifier": identifier,
                    "count": count,
                },
            )
        return count

 
    # -- list --------------------------------------------------------------
    async def list_processes(self) -> list[ProcessInfo]:
        """Return visible processes as parsed from ``tasklist``.

        The command is issued as an argv list; its CSV output is parsed
        with :mod:`csv`. No shell, no regex-against-untrusted-text.
        """
        stdout = await _run_capture(
            ["tasklist", "/FO", "CSV", "/NH"],
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
        return _parse_tasklist_csv(stdout)


# --------------------------------------------------------------------------
# Blocking helpers (run under to_thread)
# --------------------------------------------------------------------------
def _spawn_detached(executable: str) -> subprocess.Popen[bytes]:
    """Spawn a process with an argv list. Never uses a shell.

    Windows flags ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`` ensure
    the child does not inherit Azmuth's console and is not killed when
    Azmuth exits. On non-Windows the flags are omitted; the module is
    still importable, only launching non-win32 binaries fails.
    """
    kwargs: dict[str, object] = {
        "args": [executable],
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "shell": False,
    }
    if sys.platform == "win32":
        # Values from the Windows headers; stdlib exposes them under
        # subprocess on Windows builds only.
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    process = subprocess.Popen(**kwargs)  # type: ignore[arg-type]
    return process


async def _run_capture(
    argv: list[str],
    *,
    timeout: float,
    accept_exit_codes: tuple[int, ...] = (0, 128),
) -> str:
    """Run ``argv`` and return stdout as text. argv only, no shell.

    ``accept_exit_codes`` lists exit codes that should be treated as
    "command ran, output is valid" instead of raising. The default accepts
    ``0`` (normal) and ``128`` (taskkill's "no processes matched").
    Callers that perform best-effort operations pass additional codes —
    see :meth:`ApplicationController.close`.
    """

    def _blocking() -> str:
        try:
            completed = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
                shell=False,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError as exc:
            raise AutomationError(
                f"command {argv[0]!r} is not available on this system"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AutomationError(
                f"command {argv[0]!r} timed out after {timeout}s"
            ) from exc
        except OSError as exc:
            raise AutomationError(
                f"failed to run {argv[0]!r}: {exc}"
            ) from exc

        if completed.returncode not in accept_exit_codes:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise AutomationError(
                f"command {argv[0]!r} exited with {completed.returncode}: {detail}"
            )
        return completed.stdout

    return await asyncio.to_thread(_blocking)


def _parse_tasklist_csv(text: str) -> list[ProcessInfo]:
    """Parse ``tasklist /FO CSV /NH`` output.

    Row layout: ``"Image Name","PID","Session Name","Session#","Mem Usage"``.
    Malformed rows are silently skipped — the caller gets fewer results
    rather than an exception.
    """
    reader = csv.reader(io.StringIO(text))
    results: list[ProcessInfo] = []
    for row in reader:
        if len(row) < 2:
            continue
        name = row[0].strip()
        pid_text = row[1].strip().replace(",", "")
        session = row[2].strip() if len(row) >= 3 else None
        memory_kb: int | None = None
        if len(row) >= 5:
            mem_text = row[4].strip().replace(",", "").replace(" K", "")
            try:
                memory_kb = int(mem_text)
            except ValueError:
                memory_kb = None
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        results.append(
            ProcessInfo(pid=pid, name=name, session=session, memory_kb=memory_kb)
        )
    return results