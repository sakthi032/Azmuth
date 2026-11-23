"""Application launch and process management.

Everything here executes an external command -- and every external command
is an **argv list**, never a shell string. There is no ``shell=True`` in
this module and there is no code path that interpolates user input into a
command line.

UWP termination fix (Phase 1.2):
  Previously ``close()`` killed ``ApplicationFrameHost.exe`` wholesale,
  which terminates *all* UWP apps simultaneously. The fix:
  1. Attempt a graceful close via ``WM_CLOSE`` sent to the top-level
     window whose title matches the app alias.
  2. If the window is still alive after a short wait, kill only the
     specific package process (e.g. ``CalculatorApp.exe``), never
     ``ApplicationFrameHost.exe``.
"""

from __future__ import annotations

import asyncio
import csv
import ctypes
import io
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.config.logging_setup import get_logger
from app.core.errors import AutomationError
from app.automation.base import AppLaunchResult, ProcessInfo
from app.automation.policy import ApplicationPolicy
from app.automation.discovery import AppDiscovery
import shutil

logger = get_logger("automation.applications")

#: Hard cap on how long a launch may take before we give up on it. This is
#: a *startup* timeout, not a runtime timeout -- the launched process keeps
#: running after we return.
_LAUNCH_TIMEOUT_SECONDS = 10.0

#: Same idea for the process-list and kill commands.
_PROBE_TIMEOUT_SECONDS = 5.0

#: Maps the user-facing UWP alias to the **specific** package process name.
#: ``ApplicationFrameHost.exe`` is intentionally absent -- killing it would
#: terminate all UWP apps at once.
_UWP_PROCESS_NAMES: dict[str, str] = {
    "settings":   "SystemSettings.exe",
    "calculator": "CalculatorApp.exe",
    "store":      "WinStore.App.exe",
    "photos":     "Microsoft.Photos.exe",
    "camera":     "WindowsCamera.exe",
    "clock":      "Time.exe",
    "maps":       "Maps.exe",
    "weather":    "Weather.exe",
    "mail":       "HxOutlook.exe",
    "calendar":   "HxCalendarAppImm.exe",
}

#: Window title substrings used to find the HWND for a graceful WM_CLOSE.
_UWP_WINDOW_TITLES: dict[str, str] = {
    "settings":   "Settings",
    "calculator": "Calculator",
    "store":      "Microsoft Store",
    "photos":     "Photos",
    "camera":     "Camera",
    "clock":      "Clock",
    "maps":       "Maps",
    "weather":    "Weather",
    "mail":       "Mail",
    "calendar":   "Calendar",
}

_WM_CLOSE = 0x0010


def _find_window_hwnd(title_substring: str) -> int | None:
    """Return the HWND of the first top-level window whose title contains
    ``title_substring`` (case-insensitive), or ``None`` if not found.

    Uses ``EnumWindows`` so we never shell-out for window searching.
    Safe to call from any thread; does not require the event loop.
    """
    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.windll.user32
        needle = title_substring.lower()
        found: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
        def _enum_proc(hwnd: int, _lparam: int) -> bool:
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if needle in buf.value.lower():
                found.append(hwnd)
                return False  # stop enumeration
            return True

        user32.EnumWindows(_enum_proc, 0)
        return found[0] if found else None
    except Exception as exc:
        logger.debug(
            "EnumWindows failed",
            extra={"event": "ENUM_WINDOWS_FAILED", "reason": str(exc)},
        )
        return None


def _send_wm_close(hwnd: int) -> bool:
    """Post WM_CLOSE to *hwnd*. Returns True if PostMessage succeeded."""
    if sys.platform != "win32":
        return False
    try:
        result = ctypes.windll.user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)
        return bool(result)
    except Exception:
        return False


class ApplicationController:
    """Launch, inspect, and terminate applications and processes."""

    def __init__(self, policy: ApplicationPolicy | None = None) -> None:
        from app.automation.discovery import AppDiscovery

        self._policy = policy if policy is not None else ApplicationPolicy.default_windows()
        self._discovery = AppDiscovery()

    # -- discovery ---------------------------------------------------------
    @property
    def policy(self) -> ApplicationPolicy:
        return self._policy

    def known_aliases(self) -> tuple[str, ...]:
        return self._policy.known_aliases()

    # -- launch ------------------------------------------------------------
    async def open(self, alias: str) -> AppLaunchResult:
        """Launch an allowlisted or discovered application."""
        key = alias.strip().lower()
        target = self._policy.aliases.get(key, "")

        # Protocol aliases (ms-settings:, etc.) have their own path.
        if target.startswith("ms-") and target.endswith(":"):
            return await self._open_protocol(alias, target)

        # Not in the policy: try discovery.
        if not target:
            discovered = self._discovery.list_installed()
            match = next(
                (a for a in discovered
                 if a.name.lower() == key and a.target),
                None,
            ) or next(
                (a for a in discovered
                 if key in a.name.lower() and a.target),
                None,
            )
            if match is not None:
                target = match.target
                logger.info(
                    "resolved app via discovery",
                    extra={
                        "event": "APP_RESOLVED_BY_DISCOVERY",
                        "alias": alias,
                        "resolved": target,
                    },
                )

        if not target:
            raise AutomationError(
                f"application {alias!r} is not in the allowlist and was "
                f"not found by discovery. Run list_installed_apps to see "
                f"what is available."
            )

        # Normalise the target into a Path. Absolute paths must exist;
        # bare names resolve against PATH.
        candidate = Path(target)
        if candidate.is_absolute():
            if not candidate.is_file():
                raise AutomationError(
                    f"application {alias!r} resolved to {candidate}, "
                    f"which does not exist"
                )
            executable = candidate
        else:
            located = shutil.which(target)
            if located is None:
                raise AutomationError(
                    f"application {alias!r} (exe {target!r}) is not on PATH"
                )
            executable = Path(located)

        # Extra command-line arguments from the policy, if any.
        extra_args = self._policy.arguments_for(alias)

        try:
            process = await asyncio.to_thread(
                _spawn_detached, str(executable), extra_args
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

        # Wait for the window to become visible. Try the alias as a title
        # needle first; fall back to the executable stem for apps whose
        # window title differs from the alias (e.g. Win11 Notepad shows
        # just "Notepad", not "Untitled - Notepad").
        window_handle: int | None = None
        window_title: str | None = None
        try:
            from app.automation.windows_mgmt import wait_for_window
            needles = [alias]
            stem = executable.stem.lower()
            if stem and stem != alias.lower():
                needles.append(stem)
            # Also try capitalised versions for Win11 UWP-style apps
            needles.append(alias.capitalize())

            win_info = None
            for needle in needles:
                try:
                    win_info = await asyncio.to_thread(wait_for_window, needle, 10.0)
                    break
                except Exception:
                    continue

            if win_info is not None:
                window_handle = win_info.handle
                window_title = win_info.title
                logger.info(
                    "app window ready",
                    extra={
                        "event": "APP_WINDOW_READY",
                        "alias": alias,
                        "handle": window_handle,
                        "title": window_title,
                    },
                )
            else:
                logger.warning(
                    "could not find window for %r within 10s", alias,
                    extra={"event": "APP_WINDOW_NOT_FOUND", "alias": alias},
                )
        except Exception as exc:
            logger.warning("wait_for_window skipped: %s", exc)

        result = AppLaunchResult(
            alias=alias,
            pid=process.pid,
            executable=str(executable),
        )
        # Enrich the result dict with window info without changing the dataclass
        _result_dict = result.as_dict()
        _result_dict["window_handle"] = window_handle
        _result_dict["window_title"] = window_title
        return result


    async def _open_protocol(
        self, alias: str, protocol: str
    ) -> AppLaunchResult:
        """Launch a Windows URI handler (``ms-settings:``, etc.).

        Uses ``os.startfile``, which is Windows' native way to invoke a
        registered protocol handler -- the same mechanism as typing the
        URI into the Start menu or clicking a link.

        There is no PID to return: the protocol handler is launched by
        ``explorer.exe`` and the URI does not map to a single process.
        We return ``pid=0`` to signal "no attributable process".
        """
        import os

        if not hasattr(os, "startfile"):
            raise AutomationError(
                f"protocol launch ({protocol}) is only supported on Windows"
            )

        try:
            await asyncio.to_thread(os.startfile, protocol)
        except OSError as exc:
            raise AutomationError(
                f"failed to launch protocol {protocol!r}: {exc}"
            ) from exc

        logger.info(
            "protocol launched",
            extra={
                "event": "PROTOCOL_LAUNCHED",
                "alias": alias,
                "protocol": protocol,
            },
        )
        return AppLaunchResult(alias=alias, pid=0, executable=protocol)

    async def process_names_by_pid(self) -> dict[int, str]:
        """Return a mapping of PID -> image name for currently running processes."""
        processes = await self.list_processes()
        return {p.pid: p.name for p in processes}

    # -- terminate ---------------------------------------------------------
    async def close(self, identifier: str) -> int:
        identifier = identifier.strip()
        if not identifier:
            raise AutomationError("identifier must be non-empty")

        # UWP apps: graceful close by HWND first, then specific process kill.
        # We NEVER kill ApplicationFrameHost.exe because it hosts all UWP
        # windows and killing it would terminate unrelated apps.
        uwp_process = _UWP_PROCESS_NAMES.get(identifier.lower())
        if uwp_process is not None:
            return await self._close_uwp(identifier, uwp_process)

        if identifier.isdigit():
            argv = ["taskkill", "/PID", identifier, "/T", "/F"]
            image_name = await self._image_name_for_pid(identifier)
        else:
            argv = ["taskkill", "/IM", identifier, "/T", "/F"]
            image_name = identifier

        # Exit code 1 is taskkill's "could not terminate" (packaged apps,
        # protected processes). It is a valid, expected outcome -- the
        # close is best-effort, not a contract.
        stdout = await _run_capture(
            argv,
            timeout=_PROBE_TIMEOUT_SECONDS,
            accept_exit_codes=(0, 1, 2, 128, 255),
        )
        count = stdout.count("SUCCESS:")

        # Verification pass. On Windows 11, launching System32\notepad.exe
        # spawns a redirector that exits and hands off to the packaged
        # Store app under a different PID. Killing the original PID
        # reports success while the window stays open. If we were given a
        # PID, check whether the same image name is still running, and if
        # it is, kill by name as a second pass.
        if identifier.isdigit() and image_name:
            still_running = await self._is_process_running(image_name)
            if still_running:
                logger.info(
                    "PID kill left named process running; "
                    "falling back to image kill",
                    extra={
                        "event": "PROCESS_CLOSE_FALLBACK",
                        "image_name": image_name,
                        "initial_pid": identifier,
                    },
                )
                fallback = await _run_capture(
                    ["taskkill", "/IM", image_name, "/T", "/F"],
                    timeout=_PROBE_TIMEOUT_SECONDS,
                    accept_exit_codes=(0, 1, 2, 128, 255),
                )
                count += fallback.count("SUCCESS:")

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

    async def _close_uwp(self, alias: str, process_name: str) -> int:
        """Close a UWP app gracefully then fall back to its specific process.

        Strategy:
          1. Find the top-level window HWND by title substring.
          2. Post WM_CLOSE to it (graceful).
          3. Wait up to 1.5 s for the window to disappear.
          4. If still alive, kill *only* the specific package process
             (never ApplicationFrameHost.exe).
        """
        title_hint = _UWP_WINDOW_TITLES.get(alias.lower(), alias)

        # Step 1+2: graceful close via WM_CLOSE
        hwnd = await asyncio.to_thread(_find_window_hwnd, title_hint)
        wm_close_sent = False
        if hwnd:
            wm_close_sent = await asyncio.to_thread(_send_wm_close, hwnd)
            if wm_close_sent:
                logger.info(
                    "WM_CLOSE sent to UWP window",
                    extra={
                        "event": "UWP_WM_CLOSE_SENT",
                        "alias": alias,
                        "hwnd": hwnd,
                    },
                )
                # Give the app time to close gracefully
                await asyncio.sleep(1.5)

        # Step 3: check whether the specific process is still running
        still_running = await self._is_process_running(process_name)
        if not still_running:
            # Window closed gracefully
            count = 1 if wm_close_sent else 0
            logger.info(
                "UWP app closed gracefully",
                extra={
                    "event": "UWP_CLOSED_GRACEFULLY",
                    "alias": alias,
                    "target_process": process_name,
                },
            )
            return count

        # Step 4: force-kill only the specific package process
        stdout = await _run_capture(
            ["taskkill", "/IM", process_name, "/T", "/F"],
            timeout=_PROBE_TIMEOUT_SECONDS,
            accept_exit_codes=(0, 1, 2, 128, 255),
        )
        count = stdout.count("SUCCESS:")
        logger.info(
            "UWP app force-killed by specific process",
            extra={
                "event": "UWP_FORCE_KILLED",
                "alias": alias,
                "target_process": process_name,
                "count": count,
            },
        )
        return count

    async def _image_name_for_pid(self, pid: str) -> str | None:
        """Return the image name for a running PID, or None if unknown."""
        try:
            stdout = await _run_capture(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                timeout=_PROBE_TIMEOUT_SECONDS,
            )
        except AutomationError:
            return None
        # tasklist emits an INFO line when nothing matched.
        if not stdout.strip() or "INFO:" in stdout:
            return None
        rows = list(csv.reader(io.StringIO(stdout)))
        if not rows or not rows[0]:
            return None
        return rows[0][0].strip() or None

    async def _is_process_running(self, image_name: str) -> bool:
        """True if any process with this image name is running."""
        try:
            stdout = await _run_capture(
                ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
                timeout=_PROBE_TIMEOUT_SECONDS,
            )
        except AutomationError:
            return False
        return bool(stdout.strip()) and "INFO:" not in stdout

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
def _spawn_detached(
    executable: str, extra_args: tuple[str, ...] = ()
) -> subprocess.Popen[bytes]:
    """Spawn a process with an argv list. Never uses a shell.

    Windows flags ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`` ensure
    the child does not inherit Azmuth's console and is not killed when
    Azmuth exits. On non-Windows the flags are omitted; the module is
    still importable, only launching non-win32 binaries fails.
    """
    argv = [executable, *extra_args]
    kwargs: dict[str, object] = {
        "args": argv,
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
    Callers that perform best-effort operations pass additional codes --
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
    Malformed rows are silently skipped -- the caller gets fewer results
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
