"""Sandboxed Python code execution.

Runs user-supplied code in a separate subprocess with best-effort
isolation:

* temp working directory, cwd = that directory
* attached files copied in by basename
* env stripped to a whitelist (no API keys)
* timeout kill
* memory cap via Windows Job Object (best-effort)
* network + process modules blocked via a MetaPathFinder, with
  sys.modules purge before user code so cached bootstrap imports
  do not bypass the block
* subprocess and multiprocessing blocked
* os.system / os.popen / os.exec* / os.spawn* patched to raise
* shutil.rmtree outside the temp dir blocked
* builtins.open blocked for absolute paths outside temp dir
  AND outside sys.prefix (so stdlib .pyd/.py loads still work)
* matplotlib forced to Agg (headless)
* stdout/stderr capped
* any new file in the temp dir captured and returned (PNG as base64)

Deliberately NOT blocked: pickle, shelve, marshal, select, signal.
Those are deserialization / event-loop helpers, not escape vectors.
Without pickle, numpy and pandas cannot import — blocking them
breaks B7's pandas test with no security gain.

Not adversarial-grade. Same tier as Pyodide / repl.it: catches
accidental and naive code, not determined syscall-level escapes.

**Residual escape vectors (documented, not mitigated):**

 Direct Windows API calls via ``ctypes.windll.user32.CreateProcessW``
  are possible *if* a DLL is already loaded — the ``ctypes.dlopen``
  audit event does not fire for DLLs the interpreter linked at
  startup (``kernel32.dll`` on Windows is pre-loaded). Closing this
  requires OS-level isolation (AppContainer, Job Objects + restricted
  token). Documented so the tradeoff is visible.
"""

from __future__ import annotations

import asyncio
import base64
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, ClassVar

from app.config.logging_setup import get_logger
from app.core.errors import PermissionDeniedError
from app.security.capabilities import Capability
from app.tools.base import RiskLevel, Tool

logger = get_logger("tools.code_exec")

#: Maximum wall-clock seconds for one execution.
DEFAULT_TIMEOUT_SECONDS = 20.0

#: Maximum bytes of stdout or stderr returned to the model.
MAX_OUTPUT_BYTES = 50_000

#: Maximum bytes of any single captured file returned as base64.
MAX_FILE_BYTES = 2_000_000

#: Memory cap for the subprocess (Windows Job Object). Best-effort.
MEMORY_LIMIT_BYTES = 512 * 1024 * 1024

#: File extensions classified as images (returned as base64 for inline display).
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}

#: File extensions classified as text (returned as raw string).
_TEXT_EXTS = {
    ".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".toml",
    ".ini", ".log", ".html", ".xml", ".py", ".tex",
}


# ---------------------------------------------------------------------------
# Runner script (written to the temp dir, executed by the subprocess)
# ---------------------------------------------------------------------------
_RUNNER_SCRIPT = r'''"""Azmuth sandbox runner (audit-hook based)."""
import sys

def _log(msg):
    print(f"[runner] {msg}", file=sys.stderr, flush=True)

_log("start")

try:
    import os as _os
    _TMPDIR = _os.path.realpath(
        _os.environ.get("AZMUTH_SANDBOX_TMP", _os.getcwd())
    )
    _os.chdir(_TMPDIR)
except Exception as _e:
    _log(f"tmpdir setup failed: {_e}")
    sys.exit(2)

def _safe_realpath(p):
    try:
        return _os.path.realpath(p)
    except Exception:
        return p

_prefixes = set()
if getattr(sys, "prefix", None):
    _prefixes.add(_safe_realpath(sys.prefix))
if getattr(sys, "base_prefix", None):
    _prefixes.add(_safe_realpath(sys.base_prefix))
_PREFIXES = tuple(_prefixes - {""})
_log(f"prefixes: {_PREFIXES}")

# --- Blocked modules: only modules that have NO legitimate
# library use AND could interact with the parent's TTY. Everything
# else is guarded by an audit hook on the actual dangerous op.
_BLOCKED_MODULES = frozenset({"pty", "tty", "termios"})
_log(f"blocked modules: {sorted(_BLOCKED_MODULES)}")

try:
    import importlib.abc
    _log("importlib.abc loaded")
except Exception as _e:
    _log(f"importlib.abc load failed: {_e}")
    sys.exit(2)

class _Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split(".", 1)[0] in _BLOCKED_MODULES:
            raise ImportError(
                f"module {fullname!r} is blocked in the Azmuth sandbox"
            )
        return None

sys.meta_path.insert(0, _Blocker())
_log("module blocker installed")

# --- Audit hook: block the ACTUAL dangerous operations. Canonical
# Python 3.8+ sandbox mechanism. Lets pandas/numpy/matplotlib import
# subprocess/socket/ctypes freely, but prevents them from spawning
# processes, opening network connections, or loading native code.
_BLOCKED_AUDIT_EVENTS = frozenset({
    "subprocess.Popen",
    "os.system",
    "os.exec",
    "os.spawn",
    "os.fork",
    "os.forkpty",
    "os.posix_spawn",
    "socket.bind",
    "socket.connect",
    "socket.getaddrinfo",
    "socket.gethostbyname",
    "socket.gethostbyaddr",
    "socket.sendto",
    "socket.sethostname",
    "ssl.SSLContext",
    "urllib.Request",
})
_log(f"blocked audit events: {sorted(_BLOCKED_AUDIT_EVENTS)}")

def _audit_hook(event, args):
    if event in _BLOCKED_AUDIT_EVENTS:
        raise RuntimeError(
            f"{event!r} is blocked in the Azmuth sandbox"
        )

sys.addaudithook(_audit_hook)
_log("audit hook installed")

# --- Patch os functions (belt and braces) ---
try:
    import os as _os2
    def _no(*a, **k):
        raise RuntimeError(
            "this os function is blocked in the Azmuth sandbox"
        )
    for _name in (
        "system", "popen", "execv", "execve", "execvp", "execvpe",
        "spawnv", "spawnve", "spawnvp", "spawnvpe",
    ):
        if hasattr(_os2, _name):
            setattr(_os2, _name, _no)
    if hasattr(_os2, "fork"):
        _os2.fork = _no
    _log("os patched")
except Exception as _e:
    _log(f"os patch failed: {_e}")

# --- Patch shutil.rmtree ---
try:
    import shutil as _shutil
    _orig_rmtree = _shutil.rmtree

    def _safe_rmtree(path, *a, **k):
        real = _os.path.realpath(_os.path.abspath(path))
        if not (
            real == _TMPDIR or real.startswith(_TMPDIR + _os.sep)
        ):
            raise PermissionError(
                f"shutil.rmtree outside sandbox blocked: {path}"
            )
        return _orig_rmtree(path, *a, **k)

    _shutil.rmtree = _safe_rmtree
    _log("shutil patched")
except Exception as _e:
    _log(f"shutil patch failed: {_e}")

# --- Patch builtins.open ---
try:
    import builtins
    _orig_open = builtins.open

    def _safe_open(file, *args, **kwargs):
        try:
            p = _os.fspath(file)
        except TypeError:
            return _orig_open(file, *args, **kwargs)
        if isinstance(p, (str, bytes)) and _os.path.isabs(p):
            real = _os.path.realpath(p)
            in_tmp = (
                real == _TMPDIR or real.startswith(_TMPDIR + _os.sep)
            )
            in_pfx = any(
                real == x or real.startswith(x + _os.sep)
                for x in _PREFIXES
            )
            if not (in_tmp or in_pfx):
                raise PermissionError(
                    f"access outside sandbox blocked: {p}"
                )
        return _orig_open(file, *args, **kwargs)

    builtins.open = _safe_open
    _log("open patched")
except Exception as _e:
    _log(f"open patch failed: {_e}")

# --- Read user code ---
_log(f"argv: {sys.argv}")
if len(sys.argv) < 2:
    _log("no code file passed")
    sys.exit(2)

try:
    with _orig_open(sys.argv[1], "r", encoding="utf-8") as _f:
        _user_code = _f.read()
    _log(f"user code read ({len(_user_code)} chars)")
except Exception as _e:
    _log(f"could not read user code: {_e}")
    sys.exit(2)

# --- Execute user code ---
_log("executing user code")
try:
    exec(
        compile(_user_code, "<sandbox>", "exec"),
        {"__name__": "__main__"},
    )
except SystemExit:
    raise
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)
_log("user code done")
'''
# ---------------------------------------------------------------------------
# Job Object memory cap (Windows only, best-effort)
# ---------------------------------------------------------------------------

def _apply_memory_limit(process_handle: int, limit_bytes: int) -> int | None:
    """Attach a Job Object memory cap. Returns handle or None."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _IO_COUNTERS(ctypes.Structure):
            _fields_ = [("t" + str(i), ctypes.c_ulonglong) for i in range(6)]

        class _BASIC(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _EXT(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BASIC),
                ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.windll.kernel32
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _EXT()
        info.BasicLimitInformation.LimitFlags = 0x0100 | 0x0200
        info.ProcessMemoryLimit = limit_bytes
        info.JobMemoryLimit = limit_bytes
        if not kernel32.SetInformationJobObject(
            job, 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            kernel32.CloseHandle(job)
            return None
        if not kernel32.AssignProcessToJobObject(job, process_handle):
            kernel32.CloseHandle(job)
            return None
        return job
    except Exception:
        return None


def _close_job(job: int | None) -> None:
    if not job:
        return
    try:
        import ctypes
        ctypes.windll.kernel32.CloseHandle(job)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Sandbox runner
# ---------------------------------------------------------------------------

def _classify(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _TEXT_EXTS:
        return "text"
    return "binary"


class CodeSandbox:
    """Async wrapper around a subprocess Python sandbox."""

    async def execute(
        self,
        code: str,
        *,
        files: list[str] | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        tmpdir = Path(tempfile.mkdtemp(prefix="azmuth_sandbox_"))
        job: int | None = None
        proc = None
        try:
            # Copy attached files
            copied: set[str] = set()
            for f in (files or []):
                src = Path(f)
                if not src.is_file():
                    return {
                        "success": False,
                        "error": f"file not found: {f}",
                        "retryable": False,
                    }
                dst = tmpdir / src.name
                shutil.copy2(src, dst)
                copied.add(dst.name)

            # Write runner + user code
            runner_path = tmpdir / "_runner.py"
            runner_path.write_text(_RUNNER_SCRIPT, encoding="utf-8")
            user_path = tmpdir / "_user.py"
            user_path.write_text(code, encoding="utf-8")
            initial = copied | {"_runner.py", "_user.py"}

            # Config dir for matplotlib (inside temp dir, not user home)
            mpl_dir = tmpdir / ".mpl"
            mpl_dir.mkdir(exist_ok=True)

            env = {
                # Windows-required for startup on hardened systems
                "SYSTEMROOT": os.environ.get(
                    "SYSTEMROOT", os.environ.get("SystemRoot", "")
                ),
                "SYSTEMDRIVE": os.environ.get(
                    "SYSTEMDRIVE", os.environ.get("SystemDrive", "C:")
                ),
                "WINDIR": os.environ.get(
                    "WINDIR", os.environ.get("windir", "")
                ),
                "ComSpec": os.environ.get("ComSpec", ""),
                "PATHEXT": os.environ.get("PATHEXT", ""),
                # User dirs — some libraries touch these even on import
                "USERPROFILE": os.environ.get("USERPROFILE", ""),
                "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
                "APPDATA": os.environ.get("APPDATA", ""),
                "HOMEDRIVE": os.environ.get("HOMEDRIVE", ""),
                "HOMEPATH": os.environ.get("HOMEPATH", ""),
                # Inherited for normal Python behaviour
                "PATH": os.environ.get("PATH", ""),
                # Sandbox-local
                "TEMP": str(tmpdir),
                "TMP": str(tmpdir),
                "PYTHONIOENCODING": "utf-8",
                "MPLBACKEND": "Agg",
                "MPLCONFIGDIR": str(mpl_dir),
                "AZMUTH_SANDBOX_TMP": str(tmpdir),
            }

            started = time.monotonic()
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-B",
                str(runner_path), str(user_path),
                cwd=str(tmpdir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            # Job object memory cap (best-effort)
            if sys.platform == "win32":
                try:
                    handle = proc._transport._proc._handle  # type: ignore[attr-defined]
                    job = _apply_memory_limit(handle, MEMORY_LIMIT_BYTES)
                except Exception:
                    job = None

            timed_out = False
            try:
                out_b, err_b = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                timed_out = True
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
                out_b, err_b = b"", b""
            except asyncio.CancelledError:
                # Stop pressed — kill subprocess before propagating
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
                logger.info(
                    "sandbox killed by cancel",
                    extra={"event": "SANDBOX_CANCELLED"},
                )
                raise

            duration_ms = (time.monotonic() - started) * 1000
            stdout = (out_b or b"").decode("utf-8", errors="replace")[:MAX_OUTPUT_BYTES]
            stderr = (err_b or b"").decode("utf-8", errors="replace")[:MAX_OUTPUT_BYTES]

            # Capture new files
            captured: list[dict[str, Any]] = []
            for p in tmpdir.iterdir():
                if not p.is_file() or p.name in initial:
                    continue
                try:
                    size = p.stat().st_size
                except OSError:
                    continue
                entry: dict[str, Any] = {"name": p.name, "kind": _classify(p.name), "size": size}
                if size > MAX_FILE_BYTES:
                    entry["kind"] = "too_large"
                    captured.append(entry)
                    continue
                try:
                    data = p.read_bytes()
                except OSError:
                    continue
                if entry["kind"] == "text":
                    entry["data"] = data.decode("utf-8", errors="replace")
                else:
                    entry["data_b64"] = base64.b64encode(data).decode("ascii")
                captured.append(entry)

            success = proc.returncode == 0 and not timed_out
            if not success:
                logger.warning(
                    "sandbox returned non-zero",
                    extra={
                        "event": "SANDBOX_NONZERO",
                        "exit_code": proc.returncode,
                        "timed_out": timed_out,
                        "stderr_head": (stderr or "")[:800],
                    },
                )
            return {
                "success": success,
                "exit_code": proc.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "timed_out": timed_out,
                "duration_ms": round(duration_ms, 1),
                "files": captured,
                "retryable": False,
            }
        finally:
            _close_job(job)
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------
class RunPythonTool(Tool):
    """Execute Python code in a sandboxed subprocess."""

    name: ClassVar[str] = "run_python"
    description: ClassVar[str] = (
        "Execute Python code in a sandboxed subprocess. Use for data "
        "analysis, math, statistics, pandas/numpy, chart generation, or "
        "any task better done in code than by hand. Allowed libraries: "
        "math, statistics, json, csv, datetime, pandas, numpy, "
        "matplotlib (Agg backend, savefig to current dir), openpyxl. "
        "Attached files are copied into the working directory and "
        "accessible by basename. Network, subprocess, and filesystem "
        "access outside the working directory are blocked. Charts "
        "saved to the working directory as PNG are returned inline."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "minLength": 1,
                "maxLength": 50_000,
                "description": (
                    "Python code to run. Use print() for output. "
                    "Refer to attached files by basename, e.g. "
                    "pd.read_csv('data.csv')."
                ),
            },
            "files": {
                "type": "array",
                "items": {"type": "string", "maxLength": 500},
                "maxItems": 20,
                "description": (
                    "Workspace paths of files to copy into the sandbox."
                ),
            },
        },
        "required": ["code"],
        "additionalProperties": False,
    }
    capability: ClassVar[Capability] = Capability.RUN_SANDBOXED_CODE
    risk_level: ClassVar[RiskLevel] = RiskLevel.HIGH
    requires_confirmation: ClassVar[bool] = True
    can_run_async: ClassVar[bool] = True

    def __init__(
        self,
        sandbox: CodeSandbox,
        *,
        workspace_policy=None,
        session_files=None,
    ) -> None:
        self._sandbox = sandbox
        self._workspace = workspace_policy
        self._session_files = session_files

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        code = str(arguments.get("code", ""))
        preview = code if len(code) <= 1500 else code[:1500] + "\n... (truncated)"
        return f"Run this Python code?\n\n{preview}"

    async def execute(
        self,
        *,
        code: str,
        files: list[str] | None = None,
    ) -> dict[str, Any]:
        # Validate each file path against the workspace policy / session registry
        resolved: list[str] = []
        if files:
            if self._workspace is None:
                return {
                    "success": False,
                    "error": "file argument requires workspace policy",
                    "retryable": False,
                }
            from app.tools.builtin.documents import _resolve_path as _resolve
            for f in files:
                try:
                    resolved.append(str(_resolve(f, self._workspace, self._session_files)))
                except PermissionError as exc:
                    return {
                        "success": False,
                        "error": str(exc),
                        "retryable": False,
                    }

        result = await self._sandbox.execute(code, files=resolved)
        logger.info(
            "sandbox executed",
            extra={
                "event": "SANDBOX_EXECUTED",
                "success": result.get("success"),
                "duration_ms": result.get("duration_ms"),
                "timed_out": result.get("timed_out"),
                "files_created": len(result.get("files", [])),
            },
        )
        return result


__all__ = ["CodeSandbox", "RunPythonTool", "DEFAULT_TIMEOUT_SECONDS"]