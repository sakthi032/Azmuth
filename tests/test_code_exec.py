"""Sandboxed code execution — the 9 B7 test cases."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.security.capabilities import Capability
from app.security.modes import (
    AssuranceLevel, PermissionMode, Principal,
)
from app.security.permissions import PermissionManager
from app.security.workspace import ProjectWorkspacePolicy
from app.tools.builtin.code_exec import CodeSandbox, RunPythonTool


@pytest.fixture
def sandbox():
    return CodeSandbox()


@pytest.mark.asyncio
async def test_simple_math(sandbox):
    result = await sandbox.execute("print(2 + 2)", timeout=15.0)
    assert result["success"] is True, result.get("stderr")
    assert "4" in result["stdout"]


@pytest.mark.asyncio
async def test_pandas_sum_over_attached_csv(tmp_path: Path, sandbox):
    pytest.importorskip("pandas")
    csv = tmp_path / "data.csv"
    csv.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    code = (
        "import pandas as pd\n"
        "df = pd.read_csv('data.csv')\n"
        "print(df['b'].sum())\n"
    )
    result = await sandbox.execute(code, files=[str(csv)], timeout=60.0)
    assert result["success"] is True, result.get("stderr")
    assert "6" in result["stdout"]


@pytest.mark.asyncio
async def test_matplotlib_chart_creates_png(sandbox):
    pytest.importorskip("matplotlib")
    pytest.importorskip("numpy")
    code = (
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "import numpy as np\n"
        "plt.plot(np.arange(10))\n"
        "plt.savefig('chart.png')\n"
        "print('saved')\n"
    )
    result = await sandbox.execute(code, timeout=60.0)
    assert result["success"] is True, result.get("stderr")
    images = [f for f in result["files"] if f["kind"] == "image"]
    assert any(f["name"] == "chart.png" for f in images), result["files"]
    png = next(f for f in images if f["name"] == "chart.png")
    assert png.get("data_b64")


@pytest.mark.asyncio
async def test_infinite_loop_killed_at_timeout(sandbox):
    result = await sandbox.execute("while True: pass", timeout=2.0)
    assert result["timed_out"] is True
    assert result["success"] is False


@pytest.mark.asyncio
async def test_network_call_blocked(sandbox):
    """socket.socket() is allowed (no audit event), but connect() is
    blocked by the socket.connect audit hook."""
    code = (
        "import socket\n"
        "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "s.connect(('1.1.1.1', 80))\n"
    )
    result = await sandbox.execute(code, timeout=15.0)
    assert result["success"] is False, (
        f"network connect succeeded — audit hook bypassed. "
        f"stdout:\n{result.get('stdout')}"
    )
    err = (result.get("stderr") or "").lower()
    assert "blocked" in err, (
        f"expected 'blocked' in stderr, got:\n{result.get('stderr')}"
    )


@pytest.mark.asyncio
async def test_windows_file_blocked(sandbox):
    if sys.platform != "win32":
        pytest.skip("Windows-only path test")
    code = "open(r'C:\\\\Windows\\\\win.ini').read()\n"
    result = await sandbox.execute(code, timeout=15.0)
    assert result["success"] is False
    text = (result.get("stderr") or "").lower()
    assert "blocked" in text or "permissionerror" in text, text


@pytest.mark.asyncio
async def test_api_keys_stripped_from_env(sandbox, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-be-visible")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-should-not-be-visible")
    code = (
        "import os\n"
        "print(os.environ.get('OPENAI_API_KEY', 'MISSING'))\n"
        "print(os.environ.get('GEMINI_API_KEY', 'MISSING'))\n"
    )
    result = await sandbox.execute(code, timeout=15.0)
    assert result["success"] is True, result.get("stderr")
    assert "MISSING" in result["stdout"]
    assert "sk-should-not-be-visible" not in result["stdout"]
    assert "gemini-should-not-be-visible" not in result["stdout"]


def test_anonymous_denied_by_permission_manager():
    pm = PermissionManager(
        workspace_policy=ProjectWorkspacePolicy.from_paths([])
    )
    anon = Principal(
        user_id=None,
        mode=PermissionMode.RESTRICTED,
        assurance=AssuranceLevel.LOW,
        methods=(),
        authenticated_at=datetime.now(timezone.utc),
    )
    from app.core.errors import PermissionDeniedError
    with pytest.raises(PermissionDeniedError):
        pm.require(anon, Capability.RUN_SANDBOXED_CODE)


@pytest.mark.asyncio
async def test_stop_kills_subprocess():
    """Cancelling the tool task must kill the subprocess."""
    import asyncio
    sandbox = CodeSandbox()
    task = asyncio.create_task(
        sandbox.execute("while True: pass", timeout=60.0)
    )
    await asyncio.sleep(0.5)
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=3.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass


@pytest.mark.asyncio
async def test_tool_rejects_file_outside_workspace(tmp_path):
    from app.security.workspace import ProjectWorkspacePolicy as WS
    ws = WS.from_paths([tmp_path])
    tool = RunPythonTool(CodeSandbox(), workspace_policy=ws)
    outside = tmp_path.parent / f"__outside_{tmp_path.name}.txt"
    outside.write_text("x", encoding="utf-8")
    try:
        result = await tool.execute(
            code="print(1)", files=[str(outside)]
        )
        assert result["success"] is False
        assert "outside" in result.get("error", "").lower()
    finally:
        outside.unlink(missing_ok=True)

@pytest.mark.asyncio
async def test_socket_connect_blocked(sandbox):
    """Regression lock: socket.connect must stay blocked. If a future
    edit removes the socket.connect audit event, this test fails."""
    code = (
        "import socket\n"
        "socket.create_connection(('1.1.1.1', 80), timeout=2)\n"
    )
    result = await sandbox.execute(code, timeout=10.0)
    assert result["success"] is False
    assert "blocked" in (result.get("stderr") or "").lower()


@pytest.mark.asyncio
async def test_subprocess_spawn_blocked(sandbox):
    """Regression lock: subprocess may be imported (pandas needs it),
    but Popen must be blocked by the audit hook."""
    code = (
        "import subprocess\n"
        "subprocess.Popen(['cmd', '/c', 'echo', 'hi'])\n"
    )
    result = await sandbox.execute(code, timeout=10.0)
    assert result["success"] is False, (
        f"subprocess spawn succeeded — audit hook bypassed. "
        f"stdout:\n{result.get('stdout')}\n"
        f"stderr:\n{result.get('stderr')}"
    )
    err = (result.get("stderr") or "").lower()
    assert "blocked" in err, (
        f"expected 'blocked' in stderr, got:\n{result.get('stderr')}"
    )


@pytest.mark.asyncio
async def test_urllib_parse_allowed(sandbox):
    """Regression lock: urllib.parse (pure, no network) must be
    importable — pandas depends on it. If a future edit re-adds
    'urllib' to the block list, this test fails."""
    code = (
        "import urllib.parse\n"
        "print(urllib.parse.urlparse('http://example.com/x').netloc)\n"
    )
    result = await sandbox.execute(code, timeout=10.0)
    assert result["success"] is True, result.get("stderr")
    assert "example.com" in result["stdout"]
def test_runner_source_contains_required_guards():
    """Lock the runner script's structure so a future edit cannot
    silently drop the module block list, the audit hook, or the
    event block list."""
    import re
    from app.tools.builtin.code_exec import _RUNNER_SCRIPT

    # Audit event block list must include the critical events
    m = re.search(
        r"_BLOCKED_AUDIT_EVENTS\s*=\s*frozenset\(\{(.*?)\}\)",
        _RUNNER_SCRIPT, re.DOTALL,
    )
    assert m, "could not find _BLOCKED_AUDIT_EVENTS definition"
    body = m.group(1)
    required_events = [
        "subprocess.Popen",
        "socket.connect",
        "socket.bind",
        "os.system",
    ]
    for event in required_events:
        if f'"{event}"' not in body and f"'{event}'" not in body:
            raise AssertionError(
                f"_BLOCKED_AUDIT_EVENTS missing {event!r}:\n{body}"
            )

    # Audit hook must be installed
    assert "sys.addaudithook" in _RUNNER_SCRIPT, (
        "audit hook installation missing"
    )

    # Module blocker must still exist for pty/tty/termios
    assert "class _Blocker" in _RUNNER_SCRIPT
    assert "sys.meta_path.insert" in _RUNNER_SCRIPT
    
    