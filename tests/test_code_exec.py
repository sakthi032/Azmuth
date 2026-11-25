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
    code = "import socket\nsocket.socket()\n"
    result = await sandbox.execute(code, timeout=15.0)
    assert result["success"] is False
    text = (result.get("stderr") or "").lower()
    assert "blocked" in text or "importerror" in text, text


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