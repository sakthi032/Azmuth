"""Offline-first eval runner.

Uses a scripted LLM stub and a recording ToolManager, so the pipeline's
routing, permission checks, loop guard, retry cap, and injection defense
all run without touching real providers or the OS.

Live mode (--live) is implemented in round 2. Round 1 raises a clear
error when --live is requested.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from app.config.logging_setup import get_logger
from app.evals.judges import judge
from app.evals.schema import (
    Case, CaseResult, Check, JudgeResult, RunResult, StubResponse,
)
from app.llm.messages import ChatRequest, ChatResponse, Message, ToolCallRequest

logger = get_logger("evals.runner")

CASES_DIR = Path(__file__).parent / "cases"


# ---------------------------------------------------------------------------
# Scripted LLM
# ---------------------------------------------------------------------------
class ScriptedLLM:
    """LLMProvider stub that returns predefined responses in order."""

    name = "scripted"
    model = "scripted"

    def __init__(self, responses: list[StubResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[ChatRequest] = []

    def is_available(self) -> bool:
        return True

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        if not self._responses:
            return ChatResponse(
                content="(scripted: no more responses)",
                tool_calls=(), model="scripted", provider="scripted",
            )
        stub = self._responses.pop(0)
        calls = tuple(
            ToolCallRequest(
                id=f"call_{i}",
                name=tc["name"],
                arguments=tc.get("arguments", {}),
            )
            for i, tc in enumerate(stub.tool_calls)
        )
        return ChatResponse(
            content=stub.content,
            tool_calls=calls,
            model="scripted",
            provider="scripted",
        )

    async def stream(self, request: ChatRequest):
        # Streaming not used by eval cases
        raise NotImplementedError

    def get_model_info(self):
        from app.llm.messages import ModelInfo
        return ModelInfo(
            provider="scripted", model="scripted",
            context_window=None, supports_streaming=False,
        )


# ---------------------------------------------------------------------------
# Recording tool manager
# ---------------------------------------------------------------------------
class RecordingToolManager:
    """Wraps a real ToolManager, enforcing the real permission +
    confirmation gate, but stubbing the tool body.

    Two lists:
    * ``calls``         — every attempt the pipeline made (raw)
    * ``permitted_calls`` — attempts that passed the permission and
      confirmation checks, i.e. actually reached the tool body

    Eval assertions use ``permitted_calls``. A restricted principal
    calling ``run_python`` shows up in ``calls`` but not
    ``permitted_calls`` — correct semantics for "was the tool
    executed?".

    Per-tool stub overrides come from case setup:
    * ``setup.tool_outputs: {name: {…}}`` — success with this output
    * ``setup.tool_errors: {name: "msg"}`` — failure with this error
    """

    def __init__(self, real) -> None:
        self._real = real
        self.calls: list[tuple[str, dict[str, Any], bool]] = []
        self.permitted_calls: list[str] = []
        self.tool_outputs: dict[str, dict] = {}
        self.tool_errors: dict[str, str] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def configure(
        self,
        *,
        tool_outputs: dict | None = None,
        tool_errors: dict | None = None,
    ) -> None:
        if tool_outputs:
            self.tool_outputs.update(tool_outputs)
        if tool_errors:
            self.tool_errors.update(tool_errors)

    async def invoke(self, name, arguments, *, principal, confirmed=False):
        from app.tools.base import ToolResult

        self.calls.append((name, dict(arguments), confirmed))

        # 1. Tool must be registered so we can read its capability
        try:
            tool = self._real.registry.get(name)
        except Exception as exc:
            return ToolResult(
                success=False, tool=name, output=None,
                error=f"tool {name!r} not registered: {exc}",
                duration_ms=0.0,
            )

        # 2. Real permission check
        try:
            self._real._permissions.require(
                principal, tool.capability,
                path=arguments.get("path"),
            )
        except Exception as exc:
            return ToolResult(
                success=False, tool=name, output=None,
                error=str(exc), duration_ms=0.0,
            )

        # 3. Real confirmation check
        try:
            self._real._permissions.assert_confirmation(
                principal, tool.capability,
                confirmed=confirmed,
                required=tool.requires_confirmation,
            )
        except Exception as exc:
            return ToolResult(
                success=False, tool=name, output=None,
                error=str(exc), duration_ms=0.0,
            )

        # 4. Permission + confirmation passed — record and return stub
        self.permitted_calls.append(name)

        if name in self.tool_errors:
            return ToolResult(
                success=False, tool=name, output=None,
                error=self.tool_errors[name], duration_ms=0.0,
            )
        return ToolResult(
            success=True, tool=name,
            output=self.tool_outputs.get(
                name, {"stub": True, "injection_risk": False},
            ),
            error=None, duration_ms=0.0,
        )

# ---------------------------------------------------------------------------
# Case loading
# ---------------------------------------------------------------------------
def _parse_case(raw: dict[str, Any], category: str) -> Case:
    stub_responses = []
    for r in raw.get("stub_responses") or []:
        stub_responses.append(StubResponse(
            content=r.get("content", ""),
            tool_calls=list(r.get("tool_calls") or []),
        ))
    checks = [
        Check(type=c["type"], params={k: v for k, v in c.items() if k != "type"})
        for c in (raw.get("checks") or [])
    ]
    return Case(
        id=raw["id"],
        category=raw.get("category", category),
        input=raw["input"],
        mode=raw.get("mode", "creator"),
        stub_responses=stub_responses,
        expected_tools=list(raw.get("expected_tools") or []),
        forbidden_tools=list(raw.get("forbidden_tools") or []),
        checks=checks,
        setup=dict(raw.get("setup") or {}),
        notes=raw.get("notes", ""),
    )


def load_cases(category: str | None = None) -> list[Case]:
    cases: list[Case] = []
    for path in sorted(CASES_DIR.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        cat = path.stem
        if category and cat != category:
            continue
        try:
            raw_list = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        except yaml.YAMLError as exc:
            logger.exception("yaml parse failed", extra={"path": str(path)})
            continue
        for raw in raw_list:
            try:
                cases.append(_parse_case(raw, cat))
            except Exception:
                logger.exception(
                    "case parse failed",
                    extra={"path": str(path), "case_id": raw.get("id")},
                )
    return cases


# ---------------------------------------------------------------------------
# Case execution
# ---------------------------------------------------------------------------
async def run_case(case: Case) -> CaseResult:
    """Run one case against a fresh Brain with stubbed deps."""
    started = time.monotonic()
    result = CaseResult(
        case=case, passed=False, duration_ms=0.0,
    )

    try:
        # Late imports — Brain setup imports a lot
        from app.brain.pipeline import Brain
        from app.memory.conversation.manager import SessionManager
        from app.memory.manager import MemoryManager
        from app.security.modes import (
            AssuranceLevel, PermissionMode, Principal,
        )
        from app.security.permissions import PermissionManager
        from app.security.workspace import ProjectWorkspacePolicy
        from app.tools.manager import ToolManager
        from app.voice.input_manager import InputSource, UserInput
        from datetime import datetime, timezone

        # Mode
        if case.mode == "creator":
            principal = Principal(
                user_id="creator",
                mode=PermissionMode.CREATOR,
                assurance=AssuranceLevel.HIGH,
                methods=("pin",),
                authenticated_at=datetime.now(timezone.utc),
            )
        else:
            principal = Principal(
                user_id=None,
                mode=PermissionMode.RESTRICTED,
                assurance=AssuranceLevel.LOW,
                methods=(),
                authenticated_at=datetime.now(timezone.utc),
            )

        # Stubbed LLM
        llm = ScriptedLLM(case.stub_responses)

        # Real ToolManager (with real permissions), real metadata,
        # stub invoke so no tools actually run.
        perms = PermissionManager(
            workspace_policy=ProjectWorkspacePolicy.from_paths([])
        )
        base_tm = ToolManager(perms, event_bus=None)
        # Register lightweight metadata-only stubs so list_metadata
        # works. The eval doesn't need the real tool bodies.
        _register_eval_stub_tools(base_tm)
        recording_tm = RecordingToolManager(base_tm)
        recording_tm.configure(
            tool_outputs=case.setup.get("tool_outputs"),
            tool_errors=case.setup.get("tool_errors"),
        )

        # Memory: in-memory backend avoids touching the real DB
        memory = _build_in_memory_memory()
        _seed_memories(memory, case.setup)

        sessions = SessionManager(_build_minimal_settings())
        language = _build_minimal_language()

        # The eval pipeline exercises routing, tool selection, permission
        # gates and confirmation. It does not call SearchManager directly
        # (search is exposed to the model as the `search_web` tool), so
        # `search=None` is safe here. Brain constructs its own
        # AutonomousPlanner when `planner=None`; we replace its
        # create_plan/execute_plan methods with a no-op so no case can
        # trigger a multi-round plan that would hit real tools.
        brain = Brain(
            llm=llm,
            memory=memory,
            sessions=sessions,
            language=language,
            tools=recording_tm,
            search=None,
            event_bus=None,
            llm_router=None,
            planner=None,
        )
        brain._planner.create_plan = _async_noop_plan
        brain._planner.execute_plan = _async_noop_plan

        user_input = UserInput(
            text=case.input,
            language="en",
            source=InputSource.TEXT,
        )

        # Per-case confirmer. Cases can opt in to auto-approve all
        # confirmation prompts (useful for verifying tool selection of
        # confirmation-required tools such as lock_screen). Default
        # remains deny, which is the correct setting for security cases
        # that verify the pipeline blocks unconfirmed actions.
        confirmer = None
        if case.setup.get("auto_confirm"):
            async def _auto_confirm(_req):
                return True
            confirmer = _auto_confirm

        response = await brain.respond(
            user_input, principal=principal, confirmer=confirmer,
        )

        result.response_text = response.text or ""
        result.model = response.model or ""
        result.provider = response.provider or ""
        result.actual_tools = [name for name, _, _ in recording_tm.calls]
        result.actual_tools = list(recording_tm.permitted_calls)
        # Run checks
        for check in case.checks:
            result.judge_results.append(judge(check, case, result))

        # Also enforce expected / forbidden tools declared at the case
        # level. These are redundant with explicit checks but catch
        # forgetting to declare them.
        if case.expected_tools:
            for name in case.expected_tools:
                result.judge_results.append(
                    judge(Check(type="tool_called", params={"tool": name}),
                          case, result)
                )
        for name in case.forbidden_tools:
            result.judge_results.append(
                judge(Check(type="tool_not_called", params={"tool": name}),
                      case, result)
            )

        result.passed = all(j.passed for j in result.judge_results)

    except Exception as exc:
        logger.exception(
            "eval case failed",
            extra={"case_id": case.id, "category": case.category},
        )
        result.error = str(exc)
        result.passed = False

    result.duration_ms = round((time.monotonic() - started) * 1000, 1)
    return result


async def _async_noop_plan(*args, **kwargs):
    from app.brain.planner import PlanStatus
    class _Plan:
        id = "noop"
        status = PlanStatus.FAILED
        summary = "eval: planner not exercised"
        steps = []
        is_complete = True
        deliverables = []
        rounds_executed = 0
        tools_used = 0
        total_tool_budget = 0
        def render_markdown(self): return ""
    return _Plan()


def _build_minimal_settings():
    """Minimal Settings-like object for SessionManager."""
    from app.config.settings import load_settings
    return load_settings(environ={}, create_directories=False)


def _build_minimal_language():
    class _Lang:
        def resolve_response_language(self, text, session_language=None):
            return "en"
    return _Lang()


def _build_in_memory_memory():
    from app.memory.manager import MemoryManager
    from app.config.settings import load_settings
    from app.memory.backends.sqlite_backend import SqliteMemoryBackend
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="azmuth_eval_mem_"))
    backend = SqliteMemoryBackend(tmp / "mem.db")
    settings = load_settings(environ={}, create_directories=False)
    return MemoryManager(settings, backend)


def _seed_memories(memory, setup: dict) -> None:
    from app.memory.models import MemoryKind
    for m in setup.get("memories") or []:
        try:
            memory.store(
                m["content"],
                user_id=m.get("user_id", "creator"),
                kind=MemoryKind(m.get("kind", "note")),
                importance=float(m.get("importance", 0.5)),
            )
        except Exception:
            pass


def _register_eval_stub_tools(tm) -> None:
    """Register lightweight metadata stubs for every real tool.

    Each stub declares the same capability/risk as the real tool so
    permission checks behave identically, but the body never runs.
    """
    from app.tools.base import RiskLevel, Tool

    class _Stub(Tool):
        name = ""
        description = "(eval stub)"
        capability = None
        risk_level = RiskLevel.LOW
        requires_confirmation = False
        can_run_async = True
        parameters = {"type": "object", "properties": {},
                      "additionalProperties": False}
        async def execute(self, **kwargs):
            return {"stub": True}

    from app.security.capabilities import Capability

    stubs = [
        # name, capability
        ("echo", Capability.CHAT),
        ("get_time", Capability.USE_BASIC_TOOLS),
        ("get_weather", Capability.VIEW_SYSTEM_STATUS),
        ("get_gold_rate", Capability.VIEW_SYSTEM_STATUS),
        ("search_web", Capability.WEB_SEARCH),
        ("schedule_reminder", Capability.VIEW_SYSTEM_STATUS),
        ("read_document", Capability.READ_PERSONAL_FILE),
        ("ask_document", Capability.READ_PERSONAL_FILE),
        ("file_read", Capability.READ_PERSONAL_FILE),
        ("file_list", Capability.LIST_FILES),
        ("file_write", Capability.WRITE_FILE),
        ("file_delete", Capability.DELETE_FILE),
        ("describe_screen", Capability.VIEW_SYSTEM_STATUS),
        ("run_python", Capability.RUN_SANDBOXED_CODE),
        ("set_volume", Capability.CONTROL_SYSTEM_STATE),
        ("mute", Capability.CONTROL_SYSTEM_STATE),
        ("lock_screen", Capability.CONTROL_POWER),
        ("shutdown_timer", Capability.CONTROL_POWER),
        ("chrome_list_tabs", Capability.USE_BASIC_TOOLS),
        ("chrome_focus_tab", Capability.USE_BASIC_TOOLS),
        ("chrome_close_tab", Capability.USE_BASIC_TOOLS),
        ("chrome_open_url", Capability.USE_BASIC_TOOLS),
        ("chrome_eval_js", Capability.USE_BASIC_TOOLS),
        ("chrome_ext_list_tabs", Capability.USE_BASIC_TOOLS),
        ("chrome_ext_eval_js", Capability.USE_BASIC_TOOLS),
        ("add_note", Capability.USE_BASIC_TOOLS),
        ("add_todo", Capability.USE_BASIC_TOOLS),
        ("cancel_scheduled_task", Capability.USE_BASIC_TOOLS),
        ("list_scheduled_tasks", Capability.USE_BASIC_TOOLS),
        ("schedule_task", Capability.USE_BASIC_TOOLS),
        ("open_application", Capability.OPEN_APPLICATION),
        ("type_into_app", Capability.ADVANCED_AUTOMATION),
        ("open_url", Capability.OPEN_APPLICATION),
    ]
    confirm_required = {"type_into_app", "lock_screen", "shutdown_timer", "run_python"}
    for name, cap in stubs:
        cls = type(f"Stub_{name}", (_Stub,), {
            "name": name,
            "capability": cap,
            "requires_confirmation": name in confirm_required,
        })
        tm.register(cls())


def _suppress_console_logging() -> None:
    """Route app logs to a file during evals so the console stays clean."""
    import logging
    from app.evals.report import RESULTS_DIR

    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            h.setLevel(logging.CRITICAL)
    for logger_name in (
        "azmuth",
        "azmuth.tasks.persistent_scheduler",
        "azmuth.memory.retrieval",
        "azmuth.security.permissions",
        "azmuth.brain.pipeline",
    ):
        lg = logging.getLogger(logger_name)
        for h in lg.handlers:
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                h.setLevel(logging.CRITICAL)

    if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(RESULTS_DIR / "eval_app.log", encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
        root.addHandler(fh)
        root.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Category / full run
# ---------------------------------------------------------------------------
async def run_category(category: str) -> RunResult:
    _suppress_console_logging()
    cases = load_cases(category=category)
    result = RunResult(
        started_at=datetime.now(timezone.utc).isoformat(),
        category=category,
    )
    t0 = time.monotonic()
    for case in cases:
        result.cases.append(await run_case(case))
    result.finished_at = datetime.now(timezone.utc).isoformat()
    result.total_duration_ms = round((time.monotonic() - t0) * 1000, 1)
    return result


async def run_all(fast: bool = False) -> RunResult:
    _suppress_console_logging()
    cases = load_cases()
    if fast:
        from collections import defaultdict
        by_cat: dict[str, list[Case]] = defaultdict(list)
        for c in cases:
            by_cat[c.category].append(c)
        fast_cases: list[Case] = []
        for cat in sorted(by_cat):
            fast_cases.extend(by_cat[cat][:2])
        cases = fast_cases
    result = RunResult(
        started_at=datetime.now(timezone.utc).isoformat(),
        category="all",
    )
    t0 = time.monotonic()
    for case in cases:
        result.cases.append(await run_case(case))
    result.finished_at = datetime.now(timezone.utc).isoformat()
    result.total_duration_ms = round((time.monotonic() - t0) * 1000, 1)
    return result


__all__ = [
    "ScriptedLLM", "RecordingToolManager",
    "load_cases", "run_case", "run_category", "run_all",
]