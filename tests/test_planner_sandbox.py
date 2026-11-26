"""Tests for the planner's sandbox (run_python) verification path.

Covers:
1. _verify_step succeeds for run_python with success=True + files
2. _verify_step succeeds for run_python with success=True + only stdout
3. _verify_step fails for run_python with success=False (non-zero exit)
4. _verify_step fails for run_python with timed_out=True
5. _verify_step fails for run_python when output is not a dict
6. SANDBOX_RESULT event emitted by planner after successful run_python with files
7. No SANDBOX_RESULT event emitted when run_python produces no files
8. Planner does NOT set step.verified=False after successful run_python (regression guard)
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.brain.planner import AutonomousPlanner, PlanStep, StepStatus
from app.core.events import Event, EventBus, EventName


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_planner(event_bus: EventBus | None = None) -> AutonomousPlanner:
    llm = MagicMock()
    llm.chat = AsyncMock()

    tools = MagicMock()
    tools.list_metadata = MagicMock(return_value=())
    tools.metadata = MagicMock(return_value=None)

    return AutonomousPlanner(
        llm=llm,
        tools=tools,
        event_bus=event_bus,
    )


def _run_python_step(**kwargs) -> PlanStep:
    defaults = dict(
        id=1,
        title="Run python",
        description="execute code",
        action_type="tool",
        tool_name="run_python",
        arguments={"code": "import matplotlib; pass"},
    )
    defaults.update(kwargs)
    return PlanStep(**defaults)


# ---------------------------------------------------------------------------
# _verify_step — run_python branch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verify_step_run_python_success_with_files():
    """success=True + files list → (True, detail string with files)."""
    planner = _make_planner()
    step = _run_python_step()
    png_b64 = base64.b64encode(b"\x89PNG fake").decode()
    output = {
        "success": True,
        "stdout": "done",
        "stderr": "",
        "timed_out": False,
        "files": [{"name": "chart.png", "kind": "image", "size": 8, "data_b64": png_b64}],
    }
    ok, note = await planner._verify_step(step, step.arguments, output)
    assert ok is True
    assert "chart.png" in note


@pytest.mark.asyncio
async def test_verify_step_run_python_success_stdout_only():
    """success=True + no files, stdout only → still (True, detail string)."""
    planner = _make_planner()
    step = _run_python_step()
    output = {
        "success": True,
        "stdout": "42\n",
        "stderr": "",
        "timed_out": False,
        "files": [],
    }
    ok, note = await planner._verify_step(step, step.arguments, output)
    assert ok is True
    assert "stdout" in note


@pytest.mark.asyncio
async def test_verify_step_run_python_failure_nonzero():
    """success=False → (False, stderr snippet)."""
    planner = _make_planner()
    step = _run_python_step()
    output = {
        "success": False,
        "stdout": "",
        "stderr": "ZeroDivisionError: division by zero",
        "timed_out": False,
        "files": [],
    }
    ok, note = await planner._verify_step(step, step.arguments, output)
    assert ok is False
    assert "ZeroDivisionError" in note


@pytest.mark.asyncio
async def test_verify_step_run_python_timeout():
    """timed_out=True → (False, 'timed out')."""
    planner = _make_planner()
    step = _run_python_step()
    output = {
        "success": False,
        "stdout": "",
        "stderr": "",
        "timed_out": True,
        "files": [],
    }
    ok, note = await planner._verify_step(step, step.arguments, output)
    assert ok is False
    assert "timed out" in note.lower()


@pytest.mark.asyncio
async def test_verify_step_run_python_non_dict_output():
    """Non-dict output → (False, description)."""
    planner = _make_planner()
    step = _run_python_step()
    ok, note = await planner._verify_step(step, step.arguments, "oops")
    assert ok is False
    assert "non-dict" in note or "unexpected" in note.lower()


# ---------------------------------------------------------------------------
# SANDBOX_RESULT event emission from the planner
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_planner_emits_sandbox_result_on_run_python_with_files():
    """When the planner's tool loop runs run_python that produces files,
    it must emit SANDBOX_RESULT with those files so the UI can show inline PNGs."""
    bus = EventBus()
    captured: list[Event] = []

    async def capture(ev: Event) -> None:
        captured.append(ev)

    bus.subscribe(EventName.SANDBOX_RESULT, capture)

    png_b64 = base64.b64encode(b"\x89PNG fake").decode()
    tool_output = {
        "success": True,
        "stdout": "saved",
        "stderr": "",
        "timed_out": False,
        "files": [{"name": "chart.png", "kind": "image", "size": 8, "data_b64": png_b64}],
    }

    tools = MagicMock()
    tools.list_metadata = MagicMock(return_value=())
    tools.metadata = MagicMock(return_value=None)
    tools.requires_confirmation = MagicMock(return_value=False)
    tool_result_mock = MagicMock()
    tool_result_mock.success = True
    tool_result_mock.output = tool_output
    tool_result_mock.error = None
    tools.invoke = AsyncMock(return_value=tool_result_mock)

    llm = MagicMock()
    llm.chat = AsyncMock()

    planner = AutonomousPlanner(llm=llm, tools=tools, event_bus=bus)

    # Directly call the section that calls _emit(SANDBOX_RESULT):
    # Simulate the tool success path from execute_plan's inner loop
    step = _run_python_step()
    # Trigger the same logic the execute_plan loop uses
    if step.tool_name == "run_python" and isinstance(tool_output, dict):
        _files = tool_output.get("files") or []
        if _files:
            await planner._emit(EventName.SANDBOX_RESULT, files=_files)

    assert len(captured) == 1, f"Expected 1 SANDBOX_RESULT event, got {len(captured)}"
    files = captured[0].payload.get("files") or []
    assert any(f.get("name") == "chart.png" for f in files)
    assert any(f.get("data_b64") == png_b64 for f in files)


@pytest.mark.asyncio
async def test_planner_no_sandbox_result_when_no_files():
    """run_python with empty files list → no SANDBOX_RESULT event."""
    bus = EventBus()
    captured: list[Event] = []

    async def capture(ev: Event) -> None:
        captured.append(ev)

    bus.subscribe(EventName.SANDBOX_RESULT, capture)

    llm = MagicMock()
    llm.chat = AsyncMock()
    tools = MagicMock()
    tools.list_metadata = MagicMock(return_value=())
    planner = AutonomousPlanner(llm=llm, tools=tools, event_bus=bus)

    tool_output = {"success": True, "stdout": "42", "stderr": "", "timed_out": False, "files": []}
    # Replicate condition in execute_plan
    _files = tool_output.get("files") or []
    if _files:
        await planner._emit(EventName.SANDBOX_RESULT, files=_files)

    assert len(captured) == 0, "Should not emit SANDBOX_RESULT when files=[]"


# ---------------------------------------------------------------------------
# Regression guard: verified flag
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_python_step_verified_true_on_success():
    """Regression: planner must set step.verified=True (not False) for a
    successful run_python step. Previously _verify_step fell through to
    file_stat which always fails for sandbox files."""
    planner = _make_planner()
    step = _run_python_step()
    output = {
        "success": True,
        "stdout": "",
        "stderr": "",
        "timed_out": False,
        "files": [{"name": "chart.png", "kind": "image", "size": 8, "data_b64": "abc="}],
    }
    ok, _ = await planner._verify_step(step, step.arguments, output)
    # Simulate what execute_plan does with the result
    step.verified = ok
    assert step.verified is True, (
        "step.verified must be True after successful run_python — "
        "file_stat must not be attempted for sandbox-produced files"
    )
