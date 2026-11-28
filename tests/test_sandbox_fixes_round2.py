"""Round 2 failing-first tests for sandbox, planner, routing, voice, logging."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.brain.planner import (
    AutonomousPlanner,
    ExecutionPlan,
    PlanStep,
    StepStatus,
    is_planning_goal,
)
from app.config.logging_setup import setup_logging
from app.config.settings import load_settings
from app.core.events import EventBus
from app.llm.messages import ChatResponse
from app.security.modes import AssuranceLevel, PermissionMode, Principal
from app.security.permissions import PermissionManager
from app.security.workspace import ProjectWorkspacePolicy
from app.tools.builtin.code_exec import CodeSandbox, RunPythonTool
from app.tools.manager import ToolManager
from app.tools.registry import ToolRegistry


def _creator_principal() -> Principal:
    return Principal(
        user_id="sakthi",
        mode=PermissionMode.CREATOR,
        assurance=AssuranceLevel.HIGH,
        methods=("local",),
        authenticated_at=datetime.now(timezone.utc),
    )


def _anon_principal() -> Principal:
    return Principal(
        user_id=None,
        mode=PermissionMode.RESTRICTED,
        assurance=AssuranceLevel.LOW,
        methods=(),
        authenticated_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# 1 & 2. Structured result, no [runner] in stderr, AZMUTH_SANDBOX_DEBUG=1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sandbox_structured_result_and_no_runner_leak():
    sb = CodeSandbox()
    # Emit > 2500 chars to stderr before raising ZeroDivisionError
    code = (
        "import sys\n"
        "sys.stderr.write('X' * 2500 + '\\n')\n"
        "1 / 0\n"
    )
    res = await sb.execute(code, timeout=15.0)
    assert res["ok"] is False
    assert res["exit_code"] == 1
    assert res["stdout"] == ""
    assert "stderr_tail" in res
    assert len(res["stderr_tail"]) <= 2000
    assert "ZeroDivisionError" in res["stderr_tail"]
    assert res["error_type"] == "ZeroDivisionError"
    assert isinstance(res["files"], list)
    assert "[runner]" not in res["stderr_tail"]
    assert "[runner]" not in (res.get("stderr") or "")


@pytest.mark.asyncio
async def test_sandbox_debug_channel_when_enabled(monkeypatch):
    monkeypatch.setenv("AZMUTH_SANDBOX_DEBUG", "1")
    sb = CodeSandbox()
    res = await sb.execute("1 / 0", timeout=15.0)
    assert res["ok"] is False
    assert "[runner]" not in res["stderr_tail"]
    assert "[runner]" in (res.get("debug") or "")


@pytest.mark.asyncio
async def test_sandbox_mpl_env_uses_temp_dir():
    sb = CodeSandbox()
    code = (
        "import os\n"
        "print('MPLBACKEND=' + os.environ.get('MPLBACKEND', ''))\n"
        "print('MPLCONFIGDIR=' + os.environ.get('MPLCONFIGDIR', ''))\n"
        "print('XDG_CACHE_HOME=' + os.environ.get('XDG_CACHE_HOME', ''))\n"
        "print('CWD=' + os.getcwd())\n"
    )
    res = await sb.execute(code, timeout=15.0)
    assert res["ok"] is True, res.get("stderr_tail")
    lines = dict(line.split("=", 1) for line in res["stdout"].strip().splitlines() if "=" in line)
    assert lines["MPLBACKEND"] == "Agg"
    cwd = Path(lines["CWD"]).resolve()
    assert Path(lines["MPLCONFIGDIR"]).resolve().parent == cwd
    assert Path(lines["XDG_CACHE_HOME"]).resolve().parent == cwd


# ---------------------------------------------------------------------------
# 3. Missing library message + --check-tools
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_library_message_includes_pip():
    sb = CodeSandbox()
    res = await sb.execute("import nonexistent_pkg_xyz_123", timeout=15.0)
    assert res["ok"] is False
    assert res["error_type"] == "ModuleNotFoundError"
    assert "pip install nonexistent_pkg_xyz_123" in (res.get("pip_command") or "")
    assert "pip install nonexistent_pkg_xyz_123" in (res.get("user_message") or "")


def test_check_sandbox_libraries_reports_missing(monkeypatch):
    import importlib.util
    from main import _check_sandbox_libraries

    orig_find_spec = importlib.util.find_spec

    def fake_find_spec(name, *args, **kwargs):
        if name == "matplotlib":
            return None
        return orig_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    ok, msg = _check_sandbox_libraries()
    assert ok is False
    assert "matplotlib" in msg
    assert "pip install" in msg


# ---------------------------------------------------------------------------
# 4. Do not over-plan single-step requests
# ---------------------------------------------------------------------------

def test_single_step_plot_calculate_convert_skip_planner():
    assert is_planning_goal("Plot sin(x) from 0 to 10 using matplotlib and save it to chart.png") is False
    assert is_planning_goal("Calculate the mean and std of [1, 2, 3, 4] and save it to stats.txt") is False
    assert is_planning_goal("Convert sales.csv to sales.xlsx and save it") is False
    # True multi-step compound research goal must still plan
    assert is_planning_goal(
        "Research Django 5.1 release notes and compare with requirements.txt and save a report"
    ) is True


# ---------------------------------------------------------------------------
# 4b & 6. Planner retry: hash dedup, real stderr, max 2 fix attempts, budget
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_planner_retry_stops_on_identical_code_and_counts_budget():
    invocations = []

    class FakeRunPythonTool(RunPythonTool):
        def __init__(self):
            super().__init__(CodeSandbox())

        async def execute(self, *, code: str, files=None):
            invocations.append(code)
            return {
                "ok": False,
                "success": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": "ZeroDivisionError: division by zero",
                "stderr_tail": "Traceback (most recent call last):\nZeroDivisionError: division by zero",
                "error_type": "ZeroDivisionError",
                "timed_out": False,
                "files": [],
            }

    reg = ToolRegistry()
    reg.register(FakeRunPythonTool())
    pm = PermissionManager(workspace_policy=ProjectWorkspacePolicy.from_paths([]))
    tm = ToolManager(pm, reg)

    # LLM returns the exact same failing code on heal
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=ChatResponse(
        content='{"code": "x = 1 / 0"}', tool_calls=(), model="stub", provider="stub"
    ))

    planner = AutonomousPlanner(llm=llm, tools=tm)
    plan = ExecutionPlan(
        id="p1",
        goal="Run python",
        steps=[
            PlanStep(
                id=1, title="Run", description="run",
                action_type="tool", tool_name="run_python",
                arguments={"code": "x = 1 / 0"},
            )
        ],
    )

    async def approve(_req):
        return True

    res = await planner.execute_plan(plan, principal=_creator_principal(), confirmer=approve)
    assert res.steps[0].status == StepStatus.FAILED
    assert "ZeroDivisionError" in (res.steps[0].error or "")
    # Identical code must NOT be re-run
    assert len(invocations) == 1
    assert res.tools_used == 1


@pytest.mark.asyncio
async def test_planner_retry_changes_code_max_2_fixes_and_counts_3_budget():
    invocations = []
    prompts_seen = []

    class FakeRunPythonTool(RunPythonTool):
        def __init__(self):
            super().__init__(CodeSandbox())

        async def execute(self, *, code: str, files=None):
            invocations.append(code)
            return {
                "ok": False,
                "success": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": f"ZeroDivisionError: attempt {len(invocations)}",
                "stderr_tail": f"Traceback (most recent call last):\nZeroDivisionError: attempt {len(invocations)}",
                "error_type": "ZeroDivisionError",
                "timed_out": False,
                "files": [],
            }

    reg = ToolRegistry()
    reg.register(FakeRunPythonTool())
    pm = PermissionManager(workspace_policy=ProjectWorkspacePolicy.from_paths([]))
    tm = ToolManager(pm, reg)

    fix_num = 0

    async def fake_chat(req):
        nonlocal fix_num
        fix_num += 1
        prompts_seen.append(req.messages[-1].content)
        return ChatResponse(
            content=f'{{"code": "x = 1 / 0  # fix {fix_num}"}}',
            tool_calls=(), model="stub", provider="stub",
        )

    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=fake_chat)

    planner = AutonomousPlanner(llm=llm, tools=tm)
    plan = ExecutionPlan(
        id="p2",
        goal="Run python",
        steps=[
            PlanStep(
                id=1, title="Run", description="run",
                action_type="tool", tool_name="run_python",
                arguments={"code": "x = 1 / 0"},
            )
        ],
    )

    async def approve(_req):
        return True

    res = await planner.execute_plan(plan, principal=_creator_principal(), confirmer=approve)
    assert res.steps[0].status == StepStatus.FAILED
    assert len(invocations) == 3  # 1 initial + 2 fix attempts
    assert res.tools_used == 3     # every invocation counted!
    assert any("ZeroDivisionError" in p for p in prompts_seen)


# ---------------------------------------------------------------------------
# 5. Confirmation in planner & anonymous refusal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_planner_run_python_denied_by_confirmer_does_not_run():
    executed = False

    class SpyRunPythonTool(RunPythonTool):
        def __init__(self):
            super().__init__(CodeSandbox())

        async def execute(self, *, code: str, files=None):
            nonlocal executed
            executed = True
            return {"ok": True, "success": True, "exit_code": 0, "stdout": "", "stderr_tail": "", "error_type": None, "files": []}

    reg = ToolRegistry()
    reg.register(SpyRunPythonTool())
    pm = PermissionManager(workspace_policy=ProjectWorkspacePolicy.from_paths([]))
    tm = ToolManager(pm, reg)

    planner = AutonomousPlanner(llm=MagicMock(), tools=tm)
    plan = ExecutionPlan(
        id="p3",
        goal="Run python",
        steps=[
            PlanStep(
                id=1, title="Run", description="run",
                action_type="tool", tool_name="run_python",
                arguments={"code": "print('should not run')"},
            )
        ],
    )

    seen_requests = []

    async def deny(req):
        seen_requests.append(req)
        return False

    res = await planner.execute_plan(plan, principal=_creator_principal(), confirmer=deny)
    assert executed is False
    assert len(seen_requests) == 1
    assert "print('should not run')" in seen_requests[0].description
    assert res.steps[0].status == StepStatus.FAILED


@pytest.mark.asyncio
async def test_planner_run_python_refuses_anonymous():
    executed = False

    class SpyRunPythonTool(RunPythonTool):
        def __init__(self):
            super().__init__(CodeSandbox())

        async def execute(self, *, code: str, files=None):
            nonlocal executed
            executed = True
            return {"ok": True, "success": True, "exit_code": 0, "stdout": "", "stderr_tail": "", "error_type": None, "files": []}

    reg = ToolRegistry()
    reg.register(SpyRunPythonTool())
    pm = PermissionManager(workspace_policy=ProjectWorkspacePolicy.from_paths([]))
    tm = ToolManager(pm, reg)

    planner = AutonomousPlanner(llm=MagicMock(), tools=tm)
    plan = ExecutionPlan(
        id="p4",
        goal="Run python",
        steps=[
            PlanStep(
                id=1, title="Run", description="run",
                action_type="tool", tool_name="run_python",
                arguments={"code": "print(1)"},
            )
        ],
    )

    async def approve(_req):
        return True

    res = await planner.execute_plan(plan, principal=_anon_principal(), confirmer=approve)
    assert executed is False
    assert res.steps[0].status == StepStatus.FAILED


# ---------------------------------------------------------------------------
# 7. Voice must not speak tracebacks
# ---------------------------------------------------------------------------

def test_sanitize_spoken_for_code_failure():
    from app.brain.pipeline import _sanitize_spoken_text

    raw = (
        "Traceback (most recent call last):\n"
        "  File \"<sandbox>\", line 1, in <module>\n"
        "ZeroDivisionError: division by zero"
    )
    spoken = _sanitize_spoken_text(raw)
    assert "Traceback" not in spoken
    assert "ZeroDivisionError" not in spoken
    assert "code failed" in spoken.lower() or "on screen" in spoken.lower()


# ---------------------------------------------------------------------------
# 9. Logging levels for noisy third-party loggers
# ---------------------------------------------------------------------------

def test_logging_noisy_loggers_warning_by_default_and_debug_override(tmp_path):
    s_info = load_settings(
        env_file=tmp_path / "missing.env",
        environ={"AZMUTH_LOG_LEVEL": "INFO", "AZMUTH_LOG_TO_CONSOLE": "false", "AZMUTH_LOG_TO_FILE": "false"},
    )
    setup_logging(s_info, force=True)
    for name in ("websockets", "asyncio", "chromadb", "httpx"):
        assert logging.getLogger(name).level == logging.WARNING

    s_dbg = load_settings(
        env_file=tmp_path / "missing.env",
        environ={"AZMUTH_LOG_LEVEL": "DEBUG", "AZMUTH_LOG_TO_CONSOLE": "false", "AZMUTH_LOG_TO_FILE": "false"},
    )
    setup_logging(s_dbg, force=True)
    for name in ("websockets", "asyncio", "chromadb", "httpx"):
        assert logging.getLogger(name).level == logging.DEBUG


# ---------------------------------------------------------------------------
# 8. Delivery: inline PNG + "Save to Documents" option
# ---------------------------------------------------------------------------

def test_transcript_insert_image_shows_png_and_saves_to_documents(tmp_path, monkeypatch):
    import io
    import tkinter as tk
    from PIL import Image
    from app.ui.components.transcript import TranscriptView
    from app.ui.state import UIState

    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable in headless environment: {exc}")

    try:
        buf = io.BytesIO()
        Image.new("RGB", (20, 20), color=(255, 0, 0)).save(buf, format="PNG")
        png_bytes = buf.getvalue()

        tv = TranscriptView(root, UIState())
        tv.insert_image("chart.png", png_bytes, save_to_documents=True)

        text_content = tv._text.get("1.0", "end")
        assert "Save chart.png to Documents" in text_content
        assert len(tv._image_refs) == 1

        # Verify _save_to_documents writes under fake home's Documents/Azmuth
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        tv._save_to_documents("chart.png", png_bytes)
        saved_file = tmp_path / "Documents" / "Azmuth" / "chart.png"
        assert saved_file.is_file()
        assert saved_file.read_bytes() == png_bytes
    finally:
        root.destroy()
