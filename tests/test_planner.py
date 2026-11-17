"""Tests for the Autonomous Agent Planner."""

import asyncio
import sys
from pathlib import Path
import json

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.brain.planner import (
    AutonomousPlanner,
    ExecutionPlan,
    PlanStep,
    PlanStatus,
    StepStatus,
    is_planning_goal,
)
from app.llm.providers.stub import StubLLMProvider
from app.llm.messages import ChatRequest, ChatResponse, Usage
from app.security.capabilities import Capability
from app.security.modes import AssuranceLevel, PermissionMode, Principal
from app.tools.base import Tool
from app.tools.manager import ToolManager
from app.security.permissions import PermissionManager


# --------------------------------------------------------------------------
# Dummy LLM & Tools for Testing
# --------------------------------------------------------------------------
class DummyLLM(StubLLMProvider):
    name = "dummy"

    def __init__(self):
        super().__init__(model="dummy-planner-v1")

    async def chat(self, request: ChatRequest) -> ChatResponse:
        user_content = request.messages[-1].content
        if "Autonomous Planning Engine" in user_content:
            sample_plan = {
                "goal": "Test Goal",
                "steps": [
                    {
                        "id": 1,
                        "title": "Search Info",
                        "description": "Search test data",
                        "action_type": "tool",
                        "tool_name": "search_web",
                        "arguments": {"query": "django rest framework updates"},
                        "dependencies": [],
                        "verification": "has_results",
                    },
                    {
                        "id": 2,
                        "title": "Synthesize Report",
                        "description": "Draft comparative report",
                        "action_type": "synthesize",
                        "tool_name": None,
                        "arguments": {},
                        "dependencies": [1],
                        "verification": "content_not_empty",
                    },
                    {
                        "id": 3,
                        "title": "Save Report",
                        "description": "Save report to file",
                        "action_type": "tool",
                        "tool_name": "file_write",
                        "arguments": {"path": "test_report.md", "content": "{{report_content}}"},
                        "dependencies": [2],
                        "verification": "file_exists",
                    },
                ],
            }
            return ChatResponse(content=json.dumps(sample_plan), model=self.model, provider=self.name)

        return ChatResponse(
            content="# Technical Report: Django REST Framework Updates\n\nComparative analysis completed successfully.",
            model=self.model,
            provider=self.name,
        )


class MockSearchTool(Tool):
    name = "search_web"
    description = "Mock search"
    parameters = {"type": "object", "properties": {"query": {"type": "string"}}}
    capability = Capability.WEB_SEARCH

    async def execute(self, *, query: str):
        return {
            "query": query,
            "results": [{"title": "DRF 3.15 Released", "url": "https://example.com/drf", "snippet": "New features"}],
            "reference_block": "DRF 3.15 adds Python 3.13 support and schema updates.",
        }


class MockFileWriteTool(Tool):
    name = "file_write"
    description = "Mock file write"
    parameters = {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}
    capability = Capability.WRITE_FILE

    def __init__(self, tmp_path: Path):
        self._tmp = tmp_path

    async def execute(self, *, path: str, content: str, overwrite: bool = True):
        target = self._tmp / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": str(target), "bytes_written": len(content)}


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------
def test_is_planning_goal_detector():
    """Verify plan goal detector distinguishes simple vs complex requests."""
    assert is_planning_goal("what is the time?") is False
    assert is_planning_goal("hello azmuth") is False
    assert is_planning_goal("tell me a joke") is False
    assert is_planning_goal("what is the weather today?") is False

    assert is_planning_goal("Research the latest Django REST Framework updates, compare them with my project, prepare a report and save it in my project folder.") is True
    assert is_planning_goal("investigate the error in auth module, analyze and generate a patch, then save it") is True
    assert is_planning_goal("find all customer records, compare with sales report, and export summary") is True


def test_heuristic_plan_generation():
    """Verify fallback plan generator creates structured steps."""
    llm = DummyLLM()
    tm = ToolManager(PermissionManager())
    planner = AutonomousPlanner(llm=llm, tools=tm)

    goal = "Research Django REST Framework updates, compare with my project and save report"
    steps = planner._build_heuristic_plan(goal)

    assert len(steps) >= 4
    assert steps[0].tool_name == "file_read"
    assert steps[1].tool_name == "search_web"
    assert steps[2].action_type == "synthesize"
    assert steps[3].tool_name == "file_write"


def _run_async(coro):
    import inspect
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


def test_planner_create_plan():
    """Verify planner parses LLM plan into ExecutionPlan object."""
    async def _run():
        llm = DummyLLM()
        tm = ToolManager(PermissionManager())
        planner = AutonomousPlanner(llm=llm, tools=tm)

        plan = await planner.create_plan("Research Django REST Framework and prepare report")
        assert plan.status is PlanStatus.PENDING
        assert len(plan.steps) == 3
        assert plan.steps[0].tool_name == "search_web"
        assert plan.steps[1].action_type == "synthesize"
        assert plan.steps[2].tool_name == "file_write"

    _run_async(_run())


def test_planner_full_execution_and_verification(tmp_path: Path):
    """Verify execution of all steps, argument resolution, and file verification."""
    async def _run():
        llm = DummyLLM()
        tm = ToolManager(PermissionManager())
        tm.register(MockSearchTool())
        tm.register(MockFileWriteTool(tmp_path))

        planner = AutonomousPlanner(llm=llm, tools=tm, workspace_root=tmp_path)
        plan = await planner.create_plan("Research Django REST Framework and save report")

        # Set target file
        plan.steps[2].arguments["path"] = "output_report.md"

        creator_principal = Principal(
            user_id="creator",
            mode=PermissionMode.CREATOR,
            assurance=AssuranceLevel.HIGH,
            methods=("pin",),
        )
        executed_plan = await planner.execute_plan(plan, principal=creator_principal)

        assert executed_plan.status is PlanStatus.COMPLETED
        assert all(s.status is StepStatus.COMPLETED for s in executed_plan.steps)
        assert all(s.verified for s in executed_plan.steps)

        # Verify physical file was written and non-empty
        target_file = tmp_path / "output_report.md"
        assert target_file.exists()
        assert target_file.stat().st_size > 0
        assert "Django REST Framework" in target_file.read_text(encoding="utf-8")
        assert len(executed_plan.deliverables) >= 1

    _run_async(_run())


if __name__ == "__main__":
    print("Running planner tests directly...", flush=True)
    test_is_planning_goal_detector()
    print("1. test_is_planning_goal_detector PASSED", flush=True)
    test_heuristic_plan_generation()
    print("2. test_heuristic_plan_generation PASSED", flush=True)
    test_planner_create_plan()
    print("3. test_planner_create_plan PASSED", flush=True)
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as td:
        test_planner_full_execution_and_verification(Path(td))
    print("4. test_planner_full_execution_and_verification PASSED", flush=True)
    print("ALL PLANNER TESTS PASSED SUCCESSFULLY!", flush=True)
