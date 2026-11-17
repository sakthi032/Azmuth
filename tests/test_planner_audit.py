"""Comprehensive automated tests for Azmuth's Autonomous Execution Pipeline.

Tests required verification cases:
1. Successful file creation with independent stat and content verification.
2. Failed file creation (tool execution error).
3. Incorrect file path (invalid directory / permissions / outside boundary).
4. Empty file creation (0 bytes detected and rejected).
5. Failed verification (e.g. file_stat returns exists: False).
6. Tool timeout or exception handling and retry behavior (max 2 retries).
"""

import asyncio
import inspect
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

# Monkey-patch asyncio.to_thread in test environment so file operations
# can run synchronously without relying on the Windows IOCP thread pool in the sandbox.
async def _sync_to_thread(func, /, *args, **kwargs):
    return func(*args, **kwargs)

asyncio.to_thread = _sync_to_thread

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.automation.files import FileController
from app.automation.policy import PathPolicy
from app.brain.planner import (
    AutonomousPlanner,
    ExecutionPlan,
    PlanStep,
    PlanStatus,
    StepStatus,
    is_planning_goal,
)
from app.core.errors import AutomationError
from app.llm.base import LLMProvider
from app.llm.messages import ChatRequest, ChatResponse
from app.llm.providers.stub import StubLLMProvider
from app.security.capabilities import Capability
from app.security.modes import AssuranceLevel, PermissionMode, Principal
from app.security.permissions import PermissionManager
from app.tools.base import Tool, ToolResult
from app.tools.builtin.files import FileReadTool, FileStatTool, FileWriteTool
from app.tools.manager import ToolManager


def _run_async(coro):
    """Synchronously advance coroutines to prevent Windows IOCP loop hangs."""
    stack = [coro]
    val = None
    while stack:
        curr = stack[-1]
        try:
            nxt = curr.send(val)
            if inspect.isawaitable(nxt):
                stack.append(nxt)
                val = None
            else:
                val = nxt
        except StopIteration as e:
            stack.pop()
            val = e.value
    return val


class DummyPlannerLLM(StubLLMProvider):
    name = "dummy"

    def __init__(self):
        super().__init__(model="dummy-planner-v1")

    async def chat(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            content="# Technical Analysis Report\n\nVerified evaluation of dependencies and updates.",
            model=self.model,
            provider=self.name,
        )


def _creator_principal() -> Principal:
    return Principal(
        user_id="creator",
        mode=PermissionMode.CREATOR,
        assurance=AssuranceLevel.HIGH,
        methods=("pin",),
    )


# --------------------------------------------------------------------------
# Requirement 9.1: Successful file creation + stat + content verification
# --------------------------------------------------------------------------
def test_successful_file_creation(tmp_path: Path):
    async def _run():
        policy = PathPolicy.from_paths([tmp_path])
        fc = FileController(policy)
        tm = ToolManager(PermissionManager())
        tm.register(FileWriteTool(fc))
        tm.register(FileStatTool(fc))
        tm.register(FileReadTool(fc))

        planner = AutonomousPlanner(llm=DummyPlannerLLM(), tools=tm, workspace_root=tmp_path)

        target_file = "valid_report.md"
        content_text = "# Valid Technical Report\n\nThis content is strictly verified on disk."

        step1 = PlanStep(
            id=1,
            title="Write Report",
            description="Write report file",
            action_type="tool",
            tool_name="file_write",
            arguments={"path": target_file, "content": content_text, "overwrite": True},
        )
        step2 = PlanStep(
            id=2,
            title="Stat Report",
            description="Check report metadata",
            action_type="tool",
            tool_name="file_stat",
            arguments={"path": target_file},
            dependencies=[1],
        )

        plan = ExecutionPlan(id="plan-success", goal="Write and verify file", steps=[step1, step2])
        res = await planner.execute_plan(plan, principal=_creator_principal())

        assert res.status == PlanStatus.COMPLETED
        assert step1.status == StepStatus.COMPLETED and step1.verified is True
        assert step2.status == StepStatus.COMPLETED and step2.verified is True

        # Independent verification directly on disk
        disk_path = tmp_path / target_file
        assert disk_path.exists()
        assert disk_path.stat().st_size > 0
        assert disk_path.read_text(encoding="utf-8") == content_text
        assert len(res.deliverables) == 1
        assert "valid_report.md" in res.deliverables[0]

    _run_async(_run())


# --------------------------------------------------------------------------
# Requirement 9.2: Failed file creation (tool execution error)
# --------------------------------------------------------------------------
def test_failed_file_creation(tmp_path: Path):
    async def _run():
        class FailingFileWriteTool(Tool):
            name = "file_write"
            description = "Failing file write"
            parameters = {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}
            capability = Capability.WRITE_FILE

            async def execute(self, *, path: str, content: str, overwrite: bool = True):
                raise OSError("Disk I/O failure: write permission denied by OS")

        tm = ToolManager(PermissionManager())
        tm.register(FailingFileWriteTool())

        planner = AutonomousPlanner(llm=DummyPlannerLLM(), tools=tm, workspace_root=tmp_path)

        step = PlanStep(
            id=1,
            title="Write Failing File",
            description="Attempt to write",
            action_type="tool",
            tool_name="file_write",
            arguments={"path": "fail.txt", "content": "some content"},
        )

        plan = ExecutionPlan(id="plan-fail-write", goal="Failing write", steps=[step])
        res = await planner.execute_plan(plan, principal=_creator_principal())

        assert res.status == PlanStatus.FAILED
        assert step.status == StepStatus.FAILED
        assert step.verified is False
        assert step.retries == 2  # Attempted initial + 2 retries
        assert "Disk I/O failure" in (step.error or "")
        assert len(res.deliverables) == 0

    _run_async(_run())


# --------------------------------------------------------------------------
# Requirement 9.3: Incorrect file path (outside sandbox / invalid)
# --------------------------------------------------------------------------
def test_incorrect_file_path(tmp_path: Path):
    async def _run():
        policy = PathPolicy.from_paths([tmp_path])
        fc = FileController(policy)
        tm = ToolManager(PermissionManager())
        tm.register(FileWriteTool(fc))

        # A planner whose workspace_root is deliberately set outside the policy allowed roots
        forbidden_root = Path("C:/Unauthorized_Root/test_dir")
        planner = AutonomousPlanner(llm=DummyPlannerLLM(), tools=tm, workspace_root=forbidden_root)

        step_invalid = PlanStep(
            id=1,
            title="Write to unauthorized workspace root",
            description="Write outside sandbox root",
            action_type="tool",
            tool_name="file_write",
            arguments={"path": "forbidden_file.txt", "content": "illegal payload"},
        )
        plan_invalid = ExecutionPlan(id="plan-inv-fail", goal="Unauthorized path", steps=[step_invalid])
        res_invalid = await planner.execute_plan(plan_invalid, principal=_creator_principal())

        assert res_invalid.status == PlanStatus.FAILED
        assert step_invalid.status == StepStatus.FAILED
        assert step_invalid.verified is False
        assert len(res_invalid.deliverables) == 0

    _run_async(_run())


# --------------------------------------------------------------------------
# Requirement 9.4: Empty file (0 bytes triggers verification failure)
# --------------------------------------------------------------------------
def test_empty_file_creation(tmp_path: Path):
    async def _run():
        class EmptyFileWriteTool(Tool):
            name = "file_write"
            description = "Writes 0 bytes"
            parameters = {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}
            capability = Capability.WRITE_FILE

            def __init__(self, root: Path):
                self._root = root

            async def execute(self, *, path: str, content: str = "", overwrite: bool = True):
                dest = self._root / path
                dest.write_text("", encoding="utf-8")  # Explicitly write empty file
                return {"path": str(dest), "is_dir": False, "size": 0}

        tm = ToolManager(PermissionManager())
        tm.register(EmptyFileWriteTool(tmp_path))

        planner = AutonomousPlanner(llm=DummyPlannerLLM(), tools=tm, workspace_root=tmp_path)

        step = PlanStep(
            id=1,
            title="Write Empty File",
            description="Test empty file verification",
            action_type="tool",
            tool_name="file_write",
            arguments={"path": "empty.md", "content": ""},
        )

        plan = ExecutionPlan(id="plan-empty", goal="Empty file test", steps=[step])
        res = await planner.execute_plan(plan, principal=_creator_principal())

        # Must fail because size is 0 bytes
        assert res.status == PlanStatus.FAILED
        assert step.status == StepStatus.FAILED
        assert step.verified is False
        assert "empty (0 bytes)" in (step.error or "")
        assert len(res.deliverables) == 0

    _run_async(_run())


# --------------------------------------------------------------------------
# Requirement 9.5: Failed verification (file_stat reports exists: False)
# --------------------------------------------------------------------------
def test_failed_verification_stat_missing(tmp_path: Path):
    async def _run():
        policy = PathPolicy.from_paths([tmp_path])
        fc = FileController(policy)
        tm = ToolManager(PermissionManager())
        tm.register(FileStatTool(fc))

        planner = AutonomousPlanner(llm=DummyPlannerLLM(), tools=tm, workspace_root=tmp_path)

        # Checking a file that does not exist
        missing_target = "non_existent_report.md"
        step = PlanStep(
            id=1,
            title="Verify Missing File",
            description="Stat non-existent file",
            action_type="tool",
            tool_name="file_stat",
            arguments={"path": missing_target},
        )

        plan = ExecutionPlan(id="plan-stat-missing", goal="Missing file stat test", steps=[step])
        res = await planner.execute_plan(plan, principal=_creator_principal())

        # Must NOT be marked COMPLETED; must fail because exists is False!
        assert res.status == PlanStatus.FAILED
        assert step.status == StepStatus.FAILED
        assert step.verified is False
        assert "does NOT exist on disk" in (step.error or "")
        assert len(res.deliverables) == 0

    _run_async(_run())


# --------------------------------------------------------------------------
# Requirement 9.6: Tool timeout or exception handling & retry behavior (max 2 retries)
# --------------------------------------------------------------------------
def test_tool_timeout_and_retry_behavior(tmp_path: Path):
    async def _run():
        invocation_count = 0

        class FlakyTool(Tool):
            name = "flaky_tool"
            description = "Fails twice then succeeds"
            parameters = {"type": "object", "properties": {"val": {"type": "string"}}}
            capability = Capability.READ_PERSONAL_FILE

            async def execute(self, *, val: str = "default"):
                nonlocal invocation_count
                invocation_count += 1
                if invocation_count < 3:
                    raise TimeoutError(f"Network / IO Timeout attempt {invocation_count}")
                return {"status": "ok", "recovered": True}

        tm = ToolManager(PermissionManager())
        tm.register(FlakyTool())

        planner = AutonomousPlanner(llm=DummyPlannerLLM(), tools=tm, workspace_root=tmp_path)

        step = PlanStep(
            id=1,
            title="Flaky Step",
            description="Step that fails twice before recovering",
            action_type="tool",
            tool_name="flaky_tool",
            arguments={"val": "test"},
        )

        plan = ExecutionPlan(id="plan-flaky", goal="Flaky recovery test", steps=[step])
        res = await planner.execute_plan(plan, principal=_creator_principal())

        # With max_retries = 2, it executes attempt 0 (fail), attempt 1 (fail), attempt 2 (succeeds)
        assert res.status == PlanStatus.COMPLETED
        assert step.status == StepStatus.COMPLETED
        assert step.verified is True
        assert step.retries == 2
        assert invocation_count == 3

    _run_async(_run())


# --------------------------------------------------------------------------
# Main test suite runner
# --------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60, flush=True)
    print("STARTING AZMUTH AUTONOMOUS EXECUTION AUDIT TEST SUITE", flush=True)
    print("=" * 60, flush=True)

    with TemporaryDirectory() as td:
        tpath = Path(td)
        print("1. Running test_successful_file_creation...", flush=True)
        test_successful_file_creation(tpath)
        print("   -> PASSED: Real file created, independently stat'd & content verified.", flush=True)

        print("2. Running test_failed_file_creation...", flush=True)
        test_failed_file_creation(tpath)
        print("   -> PASSED: Tool failure caught, retried 2x, task marked FAILED.", flush=True)

        print("3. Running test_incorrect_file_path...", flush=True)
        test_incorrect_file_path(tpath)
        print("   -> PASSED: Path policy violation rejected, marked FAILED.", flush=True)

        print("4. Running test_empty_file_creation...", flush=True)
        test_empty_file_creation(tpath)
        print("   -> PASSED: 0-byte file rejected during verification, marked FAILED.", flush=True)

        print("5. Running test_failed_verification_stat_missing...", flush=True)
        test_failed_verification_stat_missing(tpath)
        print("   -> PASSED: Missing file in file_stat rejected, marked FAILED.", flush=True)

        print("6. Running test_tool_timeout_and_retry_behavior...", flush=True)
        test_tool_timeout_and_retry_behavior(tpath)
        print("   -> PASSED: Timeout recovered on retry 2, maximum retries adhered.", flush=True)

    print("=" * 60, flush=True)
    print("ALL 6 SPECIFICATION TESTS PASSED SUCCESSFULLY!", flush=True)
    print("=" * 60, flush=True)
