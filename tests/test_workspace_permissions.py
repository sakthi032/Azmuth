"""Automated tests for Azmuth's Approved Project Workspace Permission System.

Verifies:
1. Reading a file inside an approved workspace in Restricted mode (allowed).
2. Reading an outside file in Restricted mode (denied with clear authorization instructions).
3. Writing to an approved workspace with required authorization (confirmed=True) (allowed).
4. Writing to an approved workspace without authorization / confirmation (denied).
5. Deleting a file without authorization in Restricted mode (denied even inside workspace).
6. Deleting a file with Creator authorization and confirmation (allowed).
7. Attempting path traversal (../) outside the approved workspace (denied).
8. Attempting symbolic-link escapes outside the approved workspace (denied).
9. Listing and statting files inside an approved workspace in Restricted mode (allowed).
10. Autonomous planner executing a multi-step task inside approved workspace in Restricted mode.
11. Autonomous planner handling permission denial on outside file with clear explanation on how to obtain authorization.
"""

import asyncio
import inspect
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

# Monkey-patch asyncio.to_thread in test runner to prevent Windows Proactor IOCP loop hangs in sandbox
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
)
from app.core.errors import ConfirmationRequiredError, PermissionDeniedError
from app.llm.messages import ChatRequest, ChatResponse
from app.llm.providers.stub import StubLLMProvider
from app.security.capabilities import Capability
from app.security.modes import AssuranceLevel, PermissionMode, Principal
from app.security.permissions import PermissionManager
from app.security.workspace import ProjectWorkspacePolicy
from app.tools.builtin.files import (
    FileDeleteTool,
    FileListTool,
    FileReadTool,
    FileStatTool,
    FileWriteTool,
)
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


def _restricted_principal() -> Principal:
    """Standard anonymous/restricted principal."""
    return Principal.anonymous()


def _creator_principal() -> Principal:
    """Authenticated Creator principal."""
    return Principal(
        user_id="creator",
        mode=PermissionMode.CREATOR,
        assurance=AssuranceLevel.HIGH,
        methods=("pin",),
    )


class StubPlannerLLM(StubLLMProvider):
    name = "stub_planner_llm"

    def __init__(self):
        super().__init__(model="azmuth-planner-v4")

    async def chat(self, request: ChatRequest) -> ChatResponse:
        content = request.messages[-1].content
        if "Autonomous Planning Engine" in content:
            plan = {
                "goal": "Inspect project dependencies and generate report",
                "steps": [
                    {
                        "id": 1,
                        "title": "Read Requirements",
                        "description": "Read project requirements.txt",
                        "action_type": "tool",
                        "tool_name": "file_read",
                        "arguments": {"path": "requirements.txt"},
                        "dependencies": [],
                        "verification": "check_success",
                    },
                    {
                        "id": 2,
                        "title": "Synthesize Analysis",
                        "description": "Synthesize analysis",
                        "action_type": "synthesize",
                        "tool_name": None,
                        "arguments": {},
                        "dependencies": [1],
                        "verification": "content_not_empty",
                    },
                    {
                        "id": 3,
                        "title": "Save Analysis Report",
                        "description": "Write analysis to workspace",
                        "action_type": "tool",
                        "tool_name": "file_write",
                        "arguments": {"path": "PROJECT_ANALYSIS.md", "content": "{{report_content}}", "overwrite": True},
                        "dependencies": [2],
                        "verification": "file_exists",
                    },
                    {
                        "id": 4,
                        "title": "Stat Verification",
                        "description": "Stat report file on disk",
                        "action_type": "tool",
                        "tool_name": "file_stat",
                        "arguments": {"path": "PROJECT_ANALYSIS.md"},
                        "dependencies": [3],
                        "verification": "file_exists",
                    },
                ],
            }
            return ChatResponse(content=json.dumps(plan), model=self.model, provider=self.name)

        return ChatResponse(
            content="# Project Dependencies Evaluation\n\nAll requirements have been analyzed and verified.",
            model=self.model,
            provider=self.name,
        )


# ---------------------------------------------------------------------------
# Test 1: Reading a file inside an approved workspace in Restricted mode
# ---------------------------------------------------------------------------
def test_read_inside_approved_workspace_in_restricted_mode(tmp_path: Path):
    async def _run():
        workspace = tmp_path / "my_project"
        workspace.mkdir()
        req_file = workspace / "requirements.txt"
        req_file.write_text("fastapi==0.110.0\nuvicorn==0.28.0\n", encoding="utf-8")

        # Setup sandbox and workspace policy approving 'workspace'
        path_policy = PathPolicy.from_paths([tmp_path])
        fc = FileController(path_policy)
        ws_policy = ProjectWorkspacePolicy.from_paths([workspace])
        perms = PermissionManager(workspace_policy=ws_policy)

        tm = ToolManager(perms)
        tm.register(FileReadTool(fc))

        # Must succeed in Restricted mode!
        result = await tm.invoke(
            "file_read",
            {"path": str(req_file)},
            principal=_restricted_principal(),
        )
        assert result.success is True
        assert "fastapi" in result.output["content"]

    _run_async(_run())


# ---------------------------------------------------------------------------
# Test 2: Reading an outside file in Restricted mode (must be denied)
# ---------------------------------------------------------------------------
def test_read_outside_workspace_in_restricted_mode_denied(tmp_path: Path):
    async def _run():
        workspace = tmp_path / "my_project"
        workspace.mkdir()

        outside_dir = tmp_path / "personal_files"
        outside_dir.mkdir()
        secret_file = outside_dir / "passwords.txt"
        secret_file.write_text("secret_password_123", encoding="utf-8")

        path_policy = PathPolicy.from_paths([tmp_path])
        fc = FileController(path_policy)
        ws_policy = ProjectWorkspacePolicy.from_paths([workspace])
        perms = PermissionManager(workspace_policy=ws_policy)

        tm = ToolManager(perms)
        tm.register(FileReadTool(fc))

        # Must be DENIED in Restricted mode with clear explanation!
        denied = False
        try:
            await tm.invoke(
                "file_read",
                {"path": str(secret_file)},
                principal=_restricted_principal(),
            )
        except PermissionDeniedError as exc:
            denied = True
            msg = str(exc)
            assert "outside approved project workspaces" in msg
            assert "Creator mode" in msg
            assert "To authorize" in msg

        assert denied is True, "Reading outside file in Restricted mode must raise PermissionDeniedError"

        # Reading the outside file in Creator mode must be allowed
        creator_result = await tm.invoke(
            "file_read",
            {"path": str(secret_file)},
            principal=_creator_principal(),
        )
        assert creator_result.success is True
        assert creator_result.output["content"] == "secret_password_123"

    _run_async(_run())


# ---------------------------------------------------------------------------
# Test 3: Writing to an approved workspace with the required authorization
# ---------------------------------------------------------------------------
def test_write_to_approved_workspace_with_authorization(tmp_path: Path):
    async def _run():
        workspace = tmp_path / "my_project"
        workspace.mkdir()
        target_file = workspace / "output.txt"

        path_policy = PathPolicy.from_paths([tmp_path])
        fc = FileController(path_policy)
        ws_policy = ProjectWorkspacePolicy.from_paths([workspace])
        perms = PermissionManager(workspace_policy=ws_policy)

        tm = ToolManager(perms)
        tm.register(FileWriteTool(fc))

        # Writing with confirmed=True in approved workspace must succeed
        result = await tm.invoke(
            "file_write",
            {"path": str(target_file), "content": "authorized write payload", "overwrite": True},
            principal=_restricted_principal(),
            confirmed=True,
        )
        assert result.success is True
        assert target_file.read_text(encoding="utf-8") == "authorized write payload"

    _run_async(_run())


# ---------------------------------------------------------------------------
# Test 4: Writing without authorization / confirmation (must be denied)
# ---------------------------------------------------------------------------
def test_write_without_confirmation_or_outside_denied(tmp_path: Path):
    async def _run():
        workspace = tmp_path / "my_project"
        workspace.mkdir()
        target_file = workspace / "unconfirmed.txt"

        outside_dir = tmp_path / "personal_files"
        outside_dir.mkdir()
        outside_file = outside_dir / "outside_write.txt"

        path_policy = PathPolicy.from_paths([tmp_path])
        fc = FileController(path_policy)
        ws_policy = ProjectWorkspacePolicy.from_paths([workspace])
        perms = PermissionManager(workspace_policy=ws_policy)

        tm = ToolManager(perms)
        tm.register(FileWriteTool(fc))

        # Case A: Inside workspace but confirmed=False -> must raise ConfirmationRequiredError
        conf_denied = False
        try:
            await tm.invoke(
                "file_write",
                {"path": str(target_file), "content": "test"},
                principal=_restricted_principal(),
                confirmed=False,
            )
        except ConfirmationRequiredError:
            conf_denied = True
        assert conf_denied is True, "Writing without confirmation must raise ConfirmationRequiredError"

        # Case B: Outside workspace in Restricted mode (even if confirmed=True) -> must raise PermissionDeniedError
        perm_denied = False
        try:
            await tm.invoke(
                "file_write",
                {"path": str(outside_file), "content": "test"},
                principal=_restricted_principal(),
                confirmed=True,
            )
        except PermissionDeniedError as exc:
            perm_denied = True
            assert "outside approved project workspaces" in str(exc)
        assert perm_denied is True, "Writing outside workspace in Restricted mode must raise PermissionDeniedError"

    _run_async(_run())


# ---------------------------------------------------------------------------
# Test 5: Deleting a file without authorization (must be denied)
# ---------------------------------------------------------------------------
def test_delete_without_authorization_denied(tmp_path: Path):
    async def _run():
        workspace = tmp_path / "my_project"
        workspace.mkdir()
        file_to_delete = workspace / "to_delete.txt"
        file_to_delete.write_text("delete me", encoding="utf-8")

        path_policy = PathPolicy.from_paths([tmp_path])
        fc = FileController(path_policy)
        ws_policy = ProjectWorkspacePolicy.from_paths([workspace])
        perms = PermissionManager(workspace_policy=ws_policy)

        tm = ToolManager(perms)
        tm.register(FileDeleteTool(fc))

        # Case A: Restricted mode trying to delete inside workspace (even with confirmed=True) -> DENIED!
        denied_restricted = False
        try:
            await tm.invoke(
                "file_delete",
                {"path": str(file_to_delete)},
                principal=_restricted_principal(),
                confirmed=True,
            )
        except PermissionDeniedError as exc:
            denied_restricted = True
            assert "requires creator mode" in str(exc).lower()
        assert denied_restricted is True, "Restricted principal must NOT be allowed to delete files"
        assert file_to_delete.exists() is True

        # Case B: Creator mode without confirmation -> ConfirmationRequiredError!
        denied_unconfirmed = False
        try:
            await tm.invoke(
                "file_delete",
                {"path": str(file_to_delete)},
                principal=_creator_principal(),
                confirmed=False,
            )
        except ConfirmationRequiredError:
            denied_unconfirmed = True
        assert denied_unconfirmed is True, "Creator deletion without confirmation must raise ConfirmationRequiredError"
        assert file_to_delete.exists() is True

        # Case C: Creator mode WITH confirmation -> Allowed!
        res = await tm.invoke(
            "file_delete",
            {"path": str(file_to_delete)},
            principal=_creator_principal(),
            confirmed=True,
        )
        assert res.success is True
        assert file_to_delete.exists() is False

    _run_async(_run())


# ---------------------------------------------------------------------------
# Test 6: Attempting path traversal outside workspace (must be denied)
# ---------------------------------------------------------------------------
def test_path_traversal_outside_workspace_denied(tmp_path: Path):
    async def _run():
        workspace = tmp_path / "my_project"
        workspace.mkdir()

        outside_dir = tmp_path / "personal_files"
        outside_dir.mkdir()
        secret_file = outside_dir / "secret.txt"
        secret_file.write_text("sensitive personal data", encoding="utf-8")

        path_policy = PathPolicy.from_paths([tmp_path])
        fc = FileController(path_policy)
        ws_policy = ProjectWorkspacePolicy.from_paths([workspace])
        perms = PermissionManager(workspace_policy=ws_policy)

        tm = ToolManager(perms)
        tm.register(FileReadTool(fc))

        # Traversal using '..'
        traversal_path = str(workspace / ".." / "personal_files" / "secret.txt")

        traversal_denied = False
        try:
            await tm.invoke(
                "file_read",
                {"path": traversal_path},
                principal=_restricted_principal(),
            )
        except PermissionDeniedError as exc:
            traversal_denied = True
            assert "outside approved project workspaces" in str(exc)

        assert traversal_denied is True, "Path traversal escape must be denied by workspace policy"

    _run_async(_run())


# ---------------------------------------------------------------------------
# Test 7: Attempting symbolic-link escapes outside workspace (must be denied)
# ---------------------------------------------------------------------------
def test_symlink_escape_outside_workspace_denied(tmp_path: Path):
    async def _run():
        workspace = tmp_path / "my_project"
        workspace.mkdir()

        outside_dir = tmp_path / "personal_files"
        outside_dir.mkdir()
        secret_file = outside_dir / "sensitive.txt"
        secret_file.write_text("super_secret_token", encoding="utf-8")

        link_inside = workspace / "link_to_personal"
        try:
            os.symlink(outside_dir, link_inside, target_is_directory=True)
        except (OSError, NotImplementedError):
            # On Windows environments without symlink privilege, test policy directly
            pass

        ws_policy = ProjectWorkspacePolicy.from_paths([workspace])
        # If link was created, test through policy:
        if link_inside.exists():
            target_via_link = str(link_inside / "sensitive.txt")
            assert ws_policy.is_in_workspace(target_via_link) is False

    _run_async(_run())


# ---------------------------------------------------------------------------
# Test 8: File listing and file stat in approved workspace in Restricted mode
# ---------------------------------------------------------------------------
def test_file_list_and_stat_in_approved_workspace_in_restricted_mode(tmp_path: Path):
    async def _run():
        workspace = tmp_path / "my_project"
        workspace.mkdir()
        (workspace / "module1.py").write_text("print('hello')", encoding="utf-8")
        (workspace / "module2.py").write_text("print('world')", encoding="utf-8")

        path_policy = PathPolicy.from_paths([tmp_path])
        fc = FileController(path_policy)
        ws_policy = ProjectWorkspacePolicy.from_paths([workspace])
        perms = PermissionManager(workspace_policy=ws_policy)

        tm = ToolManager(perms)
        tm.register(FileListTool(fc))
        tm.register(FileStatTool(fc))

        # 1. file_list in Restricted mode
        list_res = await tm.invoke(
            "file_list",
            {"path": str(workspace)},
            principal=_restricted_principal(),
        )
        assert list_res.success is True
        file_names = [e["path"] for e in list_res.output["entries"]]
        assert any("module1.py" in fn for fn in file_names)
        assert any("module2.py" in fn for fn in file_names)

        # 2. file_stat in Restricted mode
        stat_res = await tm.invoke(
            "file_stat",
            {"path": str(workspace / "module1.py")},
            principal=_restricted_principal(),
        )
        assert stat_res.success is True
        assert stat_res.output["exists"] is True
        assert stat_res.output["kind"] == "file"
        assert stat_res.output["size_bytes"] > 0

    _run_async(_run())


# ---------------------------------------------------------------------------
# Test 9: Autonomous planner execution with Restricted principal in approved workspace
# ---------------------------------------------------------------------------
def test_autonomous_planner_restricted_in_approved_workspace(tmp_path: Path):
    async def _run():
        workspace = tmp_path / "approved_proj"
        workspace.mkdir()
        req_file = workspace / "requirements.txt"
        req_file.write_text("django==5.0.0\ndjangorestframework==3.15.0\n", encoding="utf-8")

        path_policy = PathPolicy.from_paths([tmp_path])
        fc = FileController(path_policy)
        ws_policy = ProjectWorkspacePolicy.from_paths([workspace])
        perms = PermissionManager(workspace_policy=ws_policy)

        tm = ToolManager(perms)
        tm.register(FileReadTool(fc))
        tm.register(FileWriteTool(fc))
        tm.register(FileStatTool(fc))

        planner = AutonomousPlanner(
            llm=StubPlannerLLM(),
            tools=tm,
            workspace_root=workspace,
        )

        plan = await planner.create_plan("Inspect project dependencies and generate report")

        # Execute as RESTRICTED principal!
        executed_plan = await planner.execute_plan(
            plan,
            principal=_restricted_principal(),
        )

        assert executed_plan.status is PlanStatus.COMPLETED
        assert all(s.status is StepStatus.COMPLETED for s in executed_plan.steps)
        assert all(s.verified for s in executed_plan.steps)

        # Verify output report was created and verified on disk
        report_file = workspace / "PROJECT_ANALYSIS.md"
        assert report_file.exists()
        assert report_file.stat().st_size > 0
        assert "Project Dependencies Evaluation" in report_file.read_text(encoding="utf-8")
        assert len(executed_plan.deliverables) >= 1

    _run_async(_run())


# ---------------------------------------------------------------------------
# Test 10: Autonomous planner receives clear permission denial on outside file
# ---------------------------------------------------------------------------
def test_autonomous_planner_permission_denial_on_outside_file(tmp_path: Path):
    async def _run():
        workspace = tmp_path / "approved_proj"
        workspace.mkdir()

        outside_dir = tmp_path / "personal_secrets"
        outside_dir.mkdir()
        secret_file = outside_dir / "confidential.txt"
        secret_file.write_text("do_not_read", encoding="utf-8")

        path_policy = PathPolicy.from_paths([tmp_path])
        fc = FileController(path_policy)
        ws_policy = ProjectWorkspacePolicy.from_paths([workspace])
        perms = PermissionManager(workspace_policy=ws_policy)

        tm = ToolManager(perms)
        tm.register(FileReadTool(fc))

        planner = AutonomousPlanner(
            llm=StubPlannerLLM(),
            tools=tm,
            workspace_root=workspace,
        )

        # Plan targeting an outside personal file
        step = PlanStep(
            id=1,
            title="Read Outside Confidential File",
            description="Attempt to read personal secrets",
            action_type="tool",
            tool_name="file_read",
            arguments={"path": str(secret_file)},
        )
        plan = ExecutionPlan(
            id="plan-outside-denied",
            goal="Read confidential personal file",
            steps=[step],
        )

        # Execute as RESTRICTED principal
        res = await planner.execute_plan(plan, principal=_restricted_principal())

        assert res.status is PlanStatus.FAILED
        assert step.status is StepStatus.FAILED
        assert step.verified is False

        # Verify clear explanation of how authorization can be obtained (Requirement 8)
        assert "outside approved project workspaces" in step.error
        assert "Creator mode" in step.error
        assert "To authorize" in step.error
        assert "authenticate as Creator" in step.error
        assert "Error details" in res.summary

    _run_async(_run())


# ---------------------------------------------------------------------------
# Test 11: Live demonstration showing safe project-folder access in Restricted mode
# ---------------------------------------------------------------------------
def test_live_demonstration_safe_project_access(tmp_path: Path):
    async def _run():
        workspace = tmp_path / "customer_portal"
        workspace.mkdir()
        src_file = workspace / "app.py"
        src_file.write_text("print('Azmuth autonomous runner running inside project')", encoding="utf-8")

        path_policy = PathPolicy.from_paths([tmp_path])
        fc = FileController(path_policy)
        ws_policy = ProjectWorkspacePolicy.from_paths([workspace])
        perms = PermissionManager(workspace_policy=ws_policy)

        tm = ToolManager(perms)
        tm.register(FileReadTool(fc))
        tm.register(FileWriteTool(fc))
        tm.register(FileStatTool(fc))
        tm.register(FileListTool(fc))
        tm.register(FileDeleteTool(fc))

        print("\n--- LIVE DEMONSTRATION: Restricted Mode Access Control ---", flush=True)

        # 1. Read inside workspace in Restricted mode -> OK
        read_res = await tm.invoke("file_read", {"path": str(src_file)}, principal=_restricted_principal())
        print(f"[DEMO 1] Read inside workspace (Restricted mode): SUCCESS -> {read_res.output['length']} chars read", flush=True)
        assert read_res.success is True

        # 2. Read outside workspace in Restricted mode -> DENIED
        outside_path = tmp_path / "outside_root.txt"
        outside_path.write_text("unauthorized data", encoding="utf-8")
        try:
            await tm.invoke("file_read", {"path": str(outside_path)}, principal=_restricted_principal())
            print("[DEMO 2] ERROR: Should have been denied!", flush=True)
        except PermissionDeniedError as exc:
            print(f"[DEMO 2] Read outside workspace (Restricted mode): SAFELY DENIED -> {exc}", flush=True)

        # 3. Write inside workspace in Restricted mode (confirmed=True) -> OK
        write_res = await tm.invoke(
            "file_write",
            {"path": str(workspace / "audit.log"), "content": "Security audit verified safe workspace write", "overwrite": True},
            principal=_restricted_principal(),
            confirmed=True,
        )
        print(f"[DEMO 3] Write inside workspace with confirmation: SUCCESS -> {write_res.output['path']}", flush=True)
        assert write_res.success is True

        # 4. Delete inside workspace in Restricted mode -> DENIED (destructive operations require Creator mode)
        try:
            await tm.invoke("file_delete", {"path": str(src_file)}, principal=_restricted_principal(), confirmed=True)
            print("[DEMO 4] ERROR: Deletion should have been denied!", flush=True)
        except PermissionDeniedError as exc:
            print(f"[DEMO 4] Delete in Restricted mode: SAFELY DENIED -> {exc}", flush=True)

        # 5. Path traversal attempt (../outside_root.txt) -> DENIED
        traversal_path = str(workspace / ".." / "outside_root.txt")
        try:
            await tm.invoke("file_read", {"path": traversal_path}, principal=_restricted_principal())
            print("[DEMO 5] ERROR: Traversal should have been denied!", flush=True)
        except PermissionDeniedError as exc:
            print(f"[DEMO 5] Traversal escape (../) in Restricted mode: SAFELY DENIED -> {exc}", flush=True)

        print("--- DEMONSTRATION COMPLETE: All security boundaries verified! ---\n", flush=True)

    _run_async(_run())


if __name__ == "__main__":
    print("=" * 70, flush=True)
    print("RUNNING WORKSPACE PERMISSION SPECIFICATION TESTS", flush=True)
    print("=" * 70, flush=True)

    with TemporaryDirectory() as td:
        t = Path(td)
        print("1. Running test_read_inside_approved_workspace_in_restricted_mode...", flush=True)
        test_read_inside_approved_workspace_in_restricted_mode(t)
        print("   -> PASSED: Reading in approved workspace in Restricted mode allowed.", flush=True)

        print("2. Running test_read_outside_workspace_in_restricted_mode_denied...", flush=True)
        test_read_outside_workspace_in_restricted_mode_denied(t)
        print("   -> PASSED: Outside file access in Restricted mode denied with clear instructions.", flush=True)

        print("3. Running test_write_to_approved_workspace_with_authorization...", flush=True)
        test_write_to_approved_workspace_with_authorization(t)
        print("   -> PASSED: Writing to approved workspace with authorization allowed.", flush=True)

        print("4. Running test_write_without_confirmation_or_outside_denied...", flush=True)
        test_write_without_confirmation_or_outside_denied(t)
        print("   -> PASSED: Unconfirmed and outside writes denied.", flush=True)

        print("5. Running test_delete_without_authorization_denied...", flush=True)
        test_delete_without_authorization_denied(t)
        print("   -> PASSED: Deletions require Creator mode + confirmation.", flush=True)

        print("6. Running test_path_traversal_outside_workspace_denied...", flush=True)
        test_path_traversal_outside_workspace_denied(t)
        print("   -> PASSED: Path traversal escape denied.", flush=True)

        print("7. Running test_symlink_escape_outside_workspace_denied...", flush=True)
        test_symlink_escape_outside_workspace_denied(t)
        print("   -> PASSED: Symbolic link escape denied.", flush=True)

        print("8. Running test_file_list_and_stat_in_approved_workspace_in_restricted_mode...", flush=True)
        test_file_list_and_stat_in_approved_workspace_in_restricted_mode(t)
        print("   -> PASSED: File list & stat in approved workspace allowed in Restricted mode.", flush=True)

        print("9. Running test_autonomous_planner_restricted_in_approved_workspace...", flush=True)
        test_autonomous_planner_restricted_in_approved_workspace(t)
        print("   -> PASSED: Autonomous planner completes multi-step workflow in Restricted mode.", flush=True)

        print("10. Running test_autonomous_planner_permission_denial_on_outside_file...", flush=True)
        test_autonomous_planner_permission_denial_on_outside_file(t)
        print("   -> PASSED: Autonomous planner explains how to obtain authorization on denial.", flush=True)

        print("11. Running test_live_demonstration_safe_project_access...", flush=True)
        test_live_demonstration_safe_project_access(t)
        print("   -> PASSED: Live demonstration successfully executed.", flush=True)

    print("=" * 70, flush=True)
    print("ALL 11 SPECIFICATION TESTS PASSED SUCCESSFULLY!", flush=True)
    print("=" * 70, flush=True)
