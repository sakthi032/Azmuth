"""Autonomous Agent Planner for Azmuth.

Enables Azmuth to handle complex, multi-step goals autonomously:
1. Understand the high-level goal and context.
2. Break it down into an ordered execution plan with dependencies.
3. Select appropriate tools for each task.
4. Execute steps sequentially with context passing.
5. Recover from failures with self-healing retries.
6. Verify every result (tool success flag, content checks, file existence where applicable).
7. Deliver a comprehensive final report and spoken summary.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from app.config.logging_setup import get_logger
from app.core.errors import ConfirmationRequiredError, PermissionDeniedError
from app.core.events import Event, EventBus, EventName
from app.llm.base import LLMProvider
from app.llm.messages import ChatRequest, Message, Role
from app.security.modes import Principal
from app.tools.manager import ToolManager

logger = get_logger("brain.planner")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PlanStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRIED = "retried"
    SKIPPED = "skipped"


@dataclass(slots=True)
class PlanStep:
    """A single discrete task within an execution plan."""

    id: int
    title: str
    description: str
    action_type: str = "tool"  # "tool", "synthesize", "verify"
    tool_name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    dependencies: list[int] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    result: Any = None
    error: str | None = None
    verification: str = ""
    verified: bool = False
    retries: int = 0
    duration_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "action_type": self.action_type,
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
            "dependencies": list(self.dependencies),
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "verification": self.verification,
            "verified": self.verified,
            "retries": self.retries,
            "duration_ms": round(self.duration_ms, 2),
        }


@dataclass(slots=True)
class ExecutionPlan:
    """A complete multi-step autonomous plan.

    Budget tracking fields
    ----------------------
    round_tool_budget : max tool calls allowed per execution round (default 10).
    total_tool_budget : hard ceiling across ALL rounds combined (default 50).
    tools_used        : running count of tool calls consumed so far.
    rounds_executed   : number of execute_plan() rounds that have been run.
    """

    id: str
    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    status: PlanStatus = PlanStatus.PENDING
    created_at: datetime = field(default_factory=_now)
    completed_at: datetime | None = None
    summary: str = ""
    deliverables: list[str] = field(default_factory=list)
    final_report: str = ""
    # --- Budget tracking (Requirement 7 & 8) ---
    round_tool_budget: int = 10
    total_tool_budget: int = 50
    tools_used: int = 0
    rounds_executed: int = 0

    @property
    def is_complete(self) -> bool:
        return self.status in (PlanStatus.COMPLETED, PlanStatus.FAILED, PlanStatus.CANCELLED)

    @property
    def budget_exhausted(self) -> bool:
        """True when the total tool-call ceiling has been reached."""
        return self.tools_used >= self.total_tool_budget

    @property
    def has_pending_steps(self) -> bool:
        """True when at least one step is still waiting to execute."""
        return any(s.status == StepStatus.PENDING for s in self.steps)

    @property
    def progress(self) -> float:
        if not self.steps:
            return 0.0
        completed = sum(1 for s in self.steps if s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED))
        return completed / len(self.steps)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "status": self.status.value,
            "progress": round(self.progress, 2),
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "steps": [s.as_dict() for s in self.steps],
            "summary": self.summary,
            "deliverables": list(self.deliverables),
            "tools_used": self.tools_used,
            "rounds_executed": self.rounds_executed,
            "round_tool_budget": self.round_tool_budget,
            "total_tool_budget": self.total_tool_budget,
        }

    def render_markdown(self) -> str:
        """Render a readable markdown summary of the plan and its execution."""
        budget_pct = int(100 * self.tools_used / max(self.total_tool_budget, 1))
        lines = [
            f"### Execution Plan: {self.goal}",
            f"**Status:** `{self.status.value.upper()}` | **Progress:** {int(self.progress * 100)}% | "
            f"**Tool budget:** {self.tools_used}/{self.total_tool_budget} ({budget_pct}%)",
            "",
            "| Step | Task | Tool / Action | Status | Verified? |",
            "| :---: | :--- | :--- | :---: | :---: |",
        ]
        for s in self.steps:
            status_icon = {
                StepStatus.COMPLETED: "✅ Completed",
                StepStatus.RUNNING: "⏳ Running",
                StepStatus.FAILED: "❌ Failed",
                StepStatus.PENDING: "⏸ Pending",
                StepStatus.SKIPPED: "⏭ Skipped",
                StepStatus.RETRIED: "🔄 Retried",
            }.get(s.status, s.status.value)
            verif_icon = "✔️ Yes" if s.verified else ("❌ No" if s.error else "—")
            action_desc = f"`{s.tool_name}`" if s.tool_name else f"`{s.action_type}`"
            lines.append(f"| {s.id} | {s.title} | {action_desc} | {status_icon} | {verif_icon} |")

        if self.deliverables:
            lines.extend(["", "#### Deliverables:"])
            for d in self.deliverables:
                lines.append(f"- {d}")

        return "\n".join(lines)


# --------------------------------------------------------------------------
# Plan Detector — recognizes compound, multi-step requests
# --------------------------------------------------------------------------
_PLAN_INDICATORS: tuple[str, ...] = (
    "research", "compare", "report", "save it", "save in",
    "workflow", "step by step", "multi-step", "pipeline",
    "investigate and", "analyze and", "analyse and",
    "first .* then", "after that", "and then", "and save",
)

_PLAN_VERB_PATTERNS = [
    re.compile(r"\b(research|investigate|find|search)\b.*\b(compare|analyze|evaluate)\b", re.IGNORECASE),
    re.compile(r"\b(compare|analyze)\b.*\b(report|summary|document)\b", re.IGNORECASE),
    re.compile(r"\b(report|write|draft)\b.*\b(save|export|store)\b", re.IGNORECASE),
    re.compile(r"\b(inspect|read)\b.*\b(update|fix|modify)\b", re.IGNORECASE),
]


_SINGLE_STEP_CODE_RE = re.compile(
    r"^\s*(?:please\s+)?(?:plot|chart|graph|draw|calculate|compute|convert|run\s+python|execute\s+python)\b",
    re.IGNORECASE,
)


def is_planning_goal(text: str) -> bool:
    """Return True if the text represents a compound multi-step goal."""
    if not text or len(text.strip()) < 25:
        return False
    lowered = text.lower()

    # Check regex combination patterns (e.g. "research ... and compare ... and save")
    for pat in _PLAN_VERB_PATTERNS:
        if pat.search(lowered):
            return True

    # Single-step code/plot/calculate/convert requests should call run_python
    # directly rather than spinning up the multi-step autonomous planner.
    if _SINGLE_STEP_CODE_RE.search(lowered):
        return False

    # Count matching indicators (avoid double-counting "save it" + "and save")
    hits = 0
    matched_save = False
    for ind in _PLAN_INDICATORS:
        if re.search(r"\b" + ind + r"\b", lowered):
            if ind in ("save it", "save in", "and save"):
                if matched_save:
                    continue
                matched_save = True
            hits += 1
    return hits >= 2


def _tool_fingerprint(tool_name: str, arguments: dict[str, Any]) -> tuple[str, str]:
    """Create a de-duplication key for a tool call.

    Uses the tool name plus the most identifying argument (path for file ops,
    query for search) so that identical calls on the same file/query are caught
    without being overly strict about every argument field.
    """
    key_arg = (
        arguments.get("path")
        or arguments.get("query")
        or arguments.get("source")
        or json.dumps(arguments, sort_keys=True, default=str)
    )
    return (tool_name, str(key_arg))


# --------------------------------------------------------------------------
# Autonomous Planner Engine
# --------------------------------------------------------------------------
class AutonomousPlanner:
    """Autonomous goal decomposition, multi-round execution, failure recovery, and verification.

    Tool budget model
    -----------------
    * round_tool_budget  : max tool (non-synthesize) steps per execute_plan() round.
    * total_tool_budget  : hard ceiling across ALL rounds combined.
    
    When ``round_tool_budget`` is reached the plan is suspended (status stays RUNNING)
    so the caller can resume by calling ``execute_plan()`` again on the same plan object.
    When ``total_tool_budget`` is exhausted the plan is failed with a budget-exceeded error.
    This prevents infinite loops while letting complex plans continue across multiple rounds.
    """

    def __init__(
        self,
        *,
        llm: LLMProvider,
        tools: ToolManager,
        event_bus: EventBus | None = None,
        workspace_root: Path | None = None,
        round_tool_budget: int = 10,
        total_tool_budget: int = 50,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._event_bus = event_bus
        self._workspace_root = workspace_root or Path.cwd()
        self._default_round_budget = max(1, round_tool_budget)
        self._default_total_budget = max(1, total_tool_budget)

    def set_llm(self, llm: LLMProvider) -> None:
        self._llm = llm

    # -- 1. Plan Generation -------------------------------------------------
    async def create_plan(self, goal: str, context: str = "") -> ExecutionPlan:
        """Decompose a high-level goal into an ordered ExecutionPlan."""
        plan_id = uuid.uuid4().hex[:8]
        available_tools = [m.name for m in self._tools.list_metadata()]

        prompt = f"""You are the Autonomous Planning Engine for Azmuth AI Assistant.
The user has requested the following goal:
"{goal}"

Available tools in the system: {available_tools}
Context:
{context}

Decompose this goal into 3 to 6 logical, sequential execution steps.
For each step, specify:
- id: integer (1, 2, 3...)
- title: concise title
- description: what this step achieves
- action_type: "tool" (if using an available tool) or "synthesize" (if using LLM reasoning to draft content/report)
- tool_name: tool name from the available tools list (e.g., "search_web", "file_read", "file_list", "file_write", "file_stat") or null if synthesize
- arguments: dict of exact tool arguments. Use placeholders like "{{step_1_result}}" or "{{report_content}}" if an argument depends on a previous step.
- dependencies: list of step ids that must finish before this step
- verification: how to verify this step succeeded (e.g., "file_exists", "has_results", "content_not_empty")

IMPORTANT – run_python tool:
- run_python executes code in a SANDBOX (temp directory). Files saved inside the sandbox (e.g. chart.png) are captured and returned as base64 in the tool result.
- Do NOT add a file_stat step to verify files produced by run_python – they are not on the workspace disk.
- Verify a run_python step by checking success=true and stdout/files in the tool result, NOT by calling file_stat afterward.

Output ONLY valid JSON in this exact structure:
{{
  "goal": "{goal}",
  "steps": [
    {{
      "id": 1,
      "title": "...",
      "description": "...",
      "action_type": "tool",
      "tool_name": "...",
      "arguments": {{}},
      "dependencies": [],
      "verification": "..."
    }}
  ]
}}
"""
        steps: list[PlanStep] = []
        try:
            req = ChatRequest(
                messages=(
                    Message(Role.SYSTEM, "You are a software architect and autonomous execution planner. Respond ONLY with valid JSON."),
                    Message(Role.USER, prompt),
                ),
                temperature=0.2,
                max_tokens=1500,
            )
            resp = await self._llm.chat(req)
            content = resp.content or ""
            # Strip markdown code fences if model enclosed in ```json
            cleaned = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.MULTILINE)
            cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)

            parsed = json.loads(cleaned)
            for s in parsed.get("steps", []):
                steps.append(
                    PlanStep(
                        id=int(s.get("id", len(steps) + 1)),
                        title=str(s.get("title", f"Step {len(steps) + 1}")),
                        description=str(s.get("description", "")),
                        action_type=str(s.get("action_type", "tool")),
                        tool_name=s.get("tool_name"),
                        arguments=dict(s.get("arguments", {})),
                        dependencies=[int(d) for d in s.get("dependencies", [])],
                        verification=str(s.get("verification", "check_success")),
                    )
                )
        except Exception as exc:
            logger.warning("LLM plan decomposition failed, using heuristic plan: %s", exc)
            steps = self._build_heuristic_plan(goal)

        if not steps:
            steps = self._build_heuristic_plan(goal)

        plan = ExecutionPlan(
            id=plan_id,
            goal=goal,
            steps=steps,
            status=PlanStatus.PENDING,
            round_tool_budget=self._default_round_budget,
            total_tool_budget=self._default_total_budget,
        )

        await self._emit(EventName.PLAN_CREATED, plan_id=plan.id, steps_count=len(plan.steps), goal=goal)
        return plan

    # -- 2. Plan Execution & Multi-Round Self-Healing -----------------------
    async def execute_plan(
        self,
        plan: ExecutionPlan,
        *,
        principal: Principal,
        confirmer: Any | None = None,
    ) -> ExecutionPlan:
        """Execute pending steps in the plan with per-round and total tool budgets.

        This method is designed to be called multiple times on the SAME plan object.
        On each call it resumes from the first PENDING step, spending at most
        ``plan.round_tool_budget`` tool calls before suspending and returning.
        When all steps are COMPLETED the plan status is set to COMPLETED.
        When the total budget is exhausted it is set to FAILED.

        Resumption contract
        -------------------
        * Only PENDING steps are executed; COMPLETED/FAILED/SKIPPED steps are skipped.
        * step_outputs is rebuilt from the results of already-completed steps so that
          template variables like ``{{report_content}}`` resolve correctly even after
          the plan is resumed from a suspension point.
        * No step is duplicated or restarted unless it was explicitly marked FAILED and
          retried via the self-healing mechanism.
        """
        if plan.is_complete:
            # Already done — nothing to do, return as-is
            return plan

        plan.status = PlanStatus.RUNNING
        plan.rounds_executed += 1

        # --- Rebuild execution state from already-completed steps (Requirement 4 & 5) ---
        step_outputs: dict[int, Any] = {
            s.id: s.result
            for s in plan.steps
            if s.status == StepStatus.COMPLETED and s.result is not None
        }
        synthesized_data: dict[str, Any] = {}
        for s in plan.steps:
            if s.action_type == "synthesize" and s.status == StepStatus.COMPLETED and s.result:
                synthesized_data["report_content"] = s.result  # last wins; ordering is plan order

        # --- Track seen (tool_name, path/query) pairs to avoid duplicates (Requirement 6) ---
        seen_tool_calls: set[tuple[str, str]] = set()
        for s in plan.steps:
            if s.status == StepStatus.COMPLETED and s.tool_name:
                key = _tool_fingerprint(s.tool_name, s.arguments)
                seen_tool_calls.add(key)

        round_tools_used = 0  # tool calls consumed this round (non-synthesize steps)
        step_outputs: dict[int, Any] = {
            s.id: s.result
            for s in plan.steps
            if s.status == StepStatus.COMPLETED and s.result is not None
        }
        synthesized_data: dict[str, Any] = {}
        for s in plan.steps:
            if s.action_type == "synthesize" and s.status == StepStatus.COMPLETED and s.result:
                synthesized_data["report_content"] = s.result  # last wins; ordering is plan order

        # --- Track seen (tool_name, path/query) pairs to avoid duplicates (Requirement 6) ---
        seen_tool_calls: set[tuple[str, str]] = set()
        for s in plan.steps:
            if s.status == StepStatus.COMPLETED and s.tool_name:
                key = _tool_fingerprint(s.tool_name, s.arguments)
                seen_tool_calls.add(key)

        round_tools_used = 0  # tool calls consumed this round (non-synthesize steps)

        for step in plan.steps:
            # --- Skip already-finished steps (Requirement 4 & 5) ---
            if step.status in (StepStatus.COMPLETED, StepStatus.SKIPPED):
                continue
            if step.status == StepStatus.FAILED:
                # A previously permanently-failed step stops the plan
                break

            # --- Check round budget BEFORE starting a tool step (Requirement 7) ---
            is_tool_step = step.action_type == "tool" and step.tool_name
            if is_tool_step:
                if round_tools_used >= plan.round_tool_budget:
                    # Suspend: round budget exhausted; plan is not failed, just paused
                    logger.info(
                        "Round tool budget (%d) reached after %d steps — plan suspended for next round "
                        "(plan_id=%s, total_used=%d/%d)",
                        plan.round_tool_budget, round_tools_used,
                        plan.id, plan.tools_used, plan.total_tool_budget,
                    )
                    # Keep status RUNNING so the caller can resume
                    plan.summary = self._generate_plan_summary(plan)
                    return plan

                if plan.budget_exhausted:
                    # Total budget hit — fail the plan (Requirement 8)
                    err = (
                        f"Total tool budget exhausted ({plan.tools_used}/{plan.total_tool_budget} calls). "
                        f"Stopping to prevent infinite execution."
                    )
                    step.status = StepStatus.FAILED
                    step.error = err
                    plan.status = PlanStatus.FAILED
                    plan.completed_at = _now()
                    plan.summary = self._generate_plan_summary(plan)
                    await self._emit(EventName.PLAN_STEP_FAILED, plan_id=plan.id, step_id=step.id, error=err)
                    logger.error("Plan %s budget exhausted: %s", plan.id, err)
                    return plan

            # --- Duplicate-call guard (Requirement 6) ---
            if is_tool_step:
                fingerprint = _tool_fingerprint(step.tool_name, step.arguments)
                if fingerprint in seen_tool_calls:
                    logger.info(
                        "Skipping duplicate tool call: %s %s (plan_id=%s)",
                        step.tool_name, fingerprint, plan.id,
                    )
                    # Carry forward the previously-cached result
                    cached = next(
                        (s.result for s in plan.steps
                         if s.status == StepStatus.COMPLETED and s.tool_name == step.tool_name
                         and _tool_fingerprint(s.tool_name, s.arguments) == fingerprint),
                        None,
                    )
                    step.status = StepStatus.SKIPPED
                    step.result = cached
                    step.verified = True
                    step.verification = "Skipped — identical tool call already completed in this plan"
                    step_outputs[step.id] = cached
                    continue

            step.status = StepStatus.RUNNING
            t_start = time.monotonic()
            await self._emit(EventName.PLAN_STEP_STARTED, plan_id=plan.id, step_id=step.id, title=step.title)

            success = False
            error_msg: str | None = None
            max_retries = 2
            seen_code_hashes: set[str] = set()

            for attempt in range(max_retries + 1):
                try:
                    if step.action_type == "synthesize":
                        # LLM reasoning / report draft step (no tool budget charge)
                        res = await self._synthesize_step(step, plan.goal, step_outputs)
                        if res and len(str(res).strip()) > 50:
                            step.result = res
                            synthesized_data["report_content"] = res
                            success = True
                            step.verified = True
                            step.verification = f"Content synthesized and verified ({len(str(res))} characters)"
                            break
                        else:
                            success = False
                            step.verified = False
                            error_msg = "Synthesis produced insufficient or empty content"

                    elif step.action_type == "tool" and step.tool_name:
                        # Resolve dynamic argument templates
                        resolved_args = self._resolve_arguments(step.arguments, step_outputs, synthesized_data)

                        # Normalize file paths relative to workspace_root if relative
                        if step.tool_name in ("file_write", "file_read", "file_stat", "file_delete"):
                            p_arg = resolved_args.get("path")
                            if p_arg and not Path(p_arg).is_absolute():
                                resolved_args["path"] = str((self._workspace_root / p_arg).resolve())

                        step.arguments = resolved_args

                        # Charge the tool budget on every invocation (including retries)
                        round_tools_used += 1
                        plan.tools_used += 1

                        # Confirmation & code-hash tracking for run_python
                        step_confirmed = True
                        if step.tool_name == "run_python":
                            code_str = str(resolved_args.get("code", ""))
                            seen_code_hashes.add(hashlib.sha256(code_str.encode("utf-8")).hexdigest())

                            from app.security.modes import PermissionMode
                            if principal.mode == PermissionMode.RESTRICTED:
                                step_confirmed = False
                            elif self._tools.requires_confirmation(step.tool_name):
                                if confirmer is None:
                                    step_confirmed = False
                                else:
                                    from app.brain.pipeline import ConfirmationRequest
                                    tool_obj = self._tools.registry.get(step.tool_name)
                                    desc = (
                                        tool_obj.confirmation_description(resolved_args)
                                        if hasattr(tool_obj, "confirmation_description")
                                        else f"Run Python code:\n\n{code_str[:1500]}"
                                    )
                                    meta = self._tools.metadata(step.tool_name)
                                    req = ConfirmationRequest(
                                        tool_name=step.tool_name,
                                        arguments=dict(resolved_args),
                                        capability=meta.capability.value if meta else "run_sandboxed_code",
                                        risk_level=meta.risk_level.value if meta else "high",
                                        description=desc,
                                    )
                                    step_confirmed = bool(await confirmer(req))
                                    if not step_confirmed:
                                        raise ConfirmationRequiredError("User declined run_python execution")

                        # Invoke the tool through ToolManager
                        tool_result = await self._tools.invoke(
                            step.tool_name,
                            resolved_args,
                            principal=principal,
                            confirmed=step_confirmed,
                        )

                        if tool_result.success:
                            step.result = tool_result.output
                            # Emit SANDBOX_RESULT so the UI can render inline images
                            # produced by run_python (charts, PNGs) even when called
                            # through the planner path rather than the pipeline loop.
                            if step.tool_name == "run_python" and isinstance(tool_result.output, dict):
                                _files = tool_result.output.get("files") or []
                                if _files:
                                    await self._emit(EventName.SANDBOX_RESULT, files=_files)
                            # Strict verification check
                            is_verified, v_note = await self._verify_step(step, resolved_args, tool_result.output)
                            step.verified = is_verified
                            step.verification = v_note
                            if is_verified:
                                # Register this call so it won't be duplicated later
                                seen_tool_calls.add(_tool_fingerprint(step.tool_name, step.arguments))
                                success = True
                                break
                            else:
                                success = False
                                error_msg = f"Verification failed: {v_note}"
                        else:
                            step.result = None
                            step.verified = False
                            error_msg = tool_result.error or f"Tool {step.tool_name} returned failure"

                    elif step.action_type == "verify":
                        resolved_args = self._resolve_arguments(step.arguments, step_outputs, synthesized_data)
                        step.arguments = resolved_args
                        is_verified, v_note = await self._verify_step(step, resolved_args, None)
                        step.verified = is_verified
                        step.verification = v_note
                        if is_verified:
                            success = True
                            break
                        else:
                            success = False
                            error_msg = f"Verification failed: {v_note}"

                except PermissionDeniedError as exc:
                    success = False
                    step.verified = False
                    error_msg = str(exc)
                    logger.warning(
                        "Plan step %d (%s) denied by security policy: %s",
                        step.id, step.title, error_msg,
                    )
                    # Security permission denial is final: do not attempt recovery retries
                    break
                except ConfirmationRequiredError as exc:
                    success = False
                    step.verified = False
                    error_msg = f"{exc}. To proceed, explicit user confirmation is required."
                    logger.warning(
                        "Plan step %d (%s) requires user confirmation: %s",
                        step.id, step.title, error_msg,
                    )
                    break
                except Exception as exc:
                    success = False
                    step.verified = False
                    error_msg = f"Exception during execution: {exc}"

                # Failure recovery & Self-healing retry
                if not success and attempt < max_retries:
                    if step.tool_name == "run_python":
                        healed = await self._heal_run_python(step, error_msg or "")
                        new_code = str(step.arguments.get("code", ""))
                        new_hash = hashlib.sha256(new_code.encode("utf-8")).hexdigest()
                        if not healed or new_hash in seen_code_hashes:
                            logger.warning(
                                "Stopping run_python retry for step %d: identical or unchanged code",
                                step.id,
                            )
                            break
                        step.retries += 1
                    else:
                        step.retries += 1
                        logger.warning(
                            "Plan step %d (%s) failed attempt %d: %s. Initiating recovery...",
                            step.id, step.title, attempt + 1, error_msg,
                        )
                        self._heal_step_arguments(step, attempt)

            step.duration_ms = (time.monotonic() - t_start) * 1000.0

            if success:
                step.status = StepStatus.COMPLETED
                step_outputs[step.id] = step.result
                await self._emit(EventName.PLAN_STEP_COMPLETED, plan_id=plan.id, step_id=step.id, verified=step.verified)
            else:
                step.status = StepStatus.FAILED
                step.error = error_msg
                await self._emit(EventName.PLAN_STEP_FAILED, plan_id=plan.id, step_id=step.id, error=error_msg)
                logger.error("Plan step %d permanently failed after %d retries: %s", step.id, step.retries, error_msg)
                # Fail-fast: if a step fails, mark plan FAILED immediately (Requirement 9)
                break

        # Plan completion wrap-up
        all_steps_done = all(
            s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED)
            for s in plan.steps
        )
        has_failure = any(s.status == StepStatus.FAILED for s in plan.steps)
        still_pending = any(s.status == StepStatus.PENDING for s in plan.steps)

        if all_steps_done:
            plan.status = PlanStatus.COMPLETED
            plan.completed_at = _now()
        elif has_failure:
            plan.status = PlanStatus.FAILED
            plan.completed_at = _now()
        else:
            # still_pending — round budget must have suspended; keep RUNNING (Requirement 3)
            pass

        # Identify deliverables (files written, created reports) — ONLY from verified steps!
        deliverables: list[str] = []
        for s in plan.steps:
            if s.tool_name == "file_write" and s.status == StepStatus.COMPLETED and s.verified:
                p = s.arguments.get("path", "file")
                deliverables.append(f"Saved file: `{p}` (Verified on disk)")
            elif s.action_type == "synthesize" and s.status == StepStatus.COMPLETED and s.verified:
                deliverables.append(f"Generated Analysis: {s.title}")
        plan.deliverables = deliverables

        # Final Summary
        plan.summary = self._generate_plan_summary(plan)
        await self._emit(EventName.PLAN_COMPLETED, plan_id=plan.id, status=plan.status.value, deliverables=len(deliverables))
        return plan

    # -- 3. Step Verification -----------------------------------------------
    async def _verify_step(self, step: PlanStep, arguments: dict[str, Any], output: Any) -> tuple[bool, str]:
        """Verify the actual execution outcome of a step with independent checks."""
        # Verification 0: run_python — files live in temp sandbox, not on disk.
        # Verify by ok/success flag and (optionally) captured files/stdout.
        if step.tool_name == "run_python":
            if not isinstance(output, dict):
                return False, "run_python returned unexpected non-dict result"
            ok_flag = output.get("ok") if "ok" in output else output.get("success")
            if not ok_flag:
                if output.get("timed_out", False):
                    return False, "run_python timed out"
                user_msg = output.get("user_message")
                stderr_tail = (output.get("stderr_tail") or output.get("stderr") or "").strip()
                if user_msg and stderr_tail:
                    return False, f"{user_msg}\n{stderr_tail}"
                if user_msg:
                    return False, user_msg
                return False, f"run_python exited non-zero:\n{stderr_tail or '(no stderr)'}"
            files = output.get("files") or []
            stdout = (output.get("stdout") or "").strip()
            detail = f"stdout={len(stdout)} chars"
            if files:
                names = ", ".join(f.get("name", "?") for f in files if isinstance(f, dict))
                detail += f"; files=[{names}]"
            return True, f"Sandbox executed successfully ({detail})"

        # Verification 1: File Write verification (output check, stat check on disk, size > 0, content check)
        if step.tool_name == "file_write":
            # Output from FileWriteTool is FileInfo.as_dict(): {"path": ..., "is_dir": False, "size": ...}
            if not isinstance(output, dict):
                return False, "file_write returned invalid non-dictionary result"

            reported_path = output.get("path") or arguments.get("path")
            if not reported_path:
                return False, "file_write output did not contain a target path"

            try:
                target_path = Path(reported_path)
                if not target_path.is_absolute():
                    target_path = self._workspace_root / reported_path

                # Independent file stat verification
                if not target_path.exists():
                    return False, f"File {target_path} not found on disk after write"
                if not target_path.is_file():
                    return False, f"Target {target_path} is not a regular file"

                file_size = target_path.stat().st_size
                if file_size <= 0:
                    return False, f"File created but empty (0 bytes)"

                # Independent content verification
                expected_content = arguments.get("content", "")
                if expected_content and expected_content != "(No content generated)":
                    actual_content = target_path.read_text(encoding="utf-8")
                    if len(actual_content) == 0:
                        return False, "File content verification failed: file is empty"
                    # Check that content matches or contains expected non-empty prefix
                    if len(expected_content) > 100:
                        sample = expected_content[:80]
                        if sample not in actual_content:
                            return False, "File content verification failed: written content does not match expected output"

                return True, f"Verified on disk: {target_path.name} ({file_size} bytes)"
            except Exception as e:
                return False, f"File verification check failed: {e}"

        # Verification 2: File Stat verification
        if step.tool_name == "file_stat":
            if not isinstance(output, dict):
                return False, "file_stat returned invalid non-dictionary result"
            exists = output.get("exists", False)
            size = output.get("size_bytes", 0)
            kind = output.get("kind", "missing")
            stat_path = output.get("path", arguments.get("path", "unknown"))

            if not exists or kind == "missing":
                return False, f"File stat check confirmed target does NOT exist on disk: {stat_path}"
            if size <= 0:
                return False, f"File stat check reported file exists but has 0 bytes: {stat_path}"
            return True, f"Verified: Target exists on disk ({kind}, {size} bytes)"

        # Verification 3: Web Search verification
        if step.tool_name == "search_web":
            if isinstance(output, dict):
                results = output.get("results") or []
                ref_block = output.get("reference_block") or ""
                if results or (ref_block and len(ref_block) > 40):
                    return True, f"Verified: Retrieved {len(results)} search results"
                return False, "Search returned 0 results and empty reference block"
            return True, "Search completed"

        # Verification 4: File Read / List verification
        if step.tool_name in ("file_read", "file_list"):
            if output is None:
                return False, "No data returned from file operation"
            if isinstance(output, dict):
                if "content" in output and output["content"] is None:
                    return False, "File read returned null content"
            return True, "Verified: Content inspected successfully"

        # Verification 5: Generic or unknown tools
        if output is not None:
            if isinstance(output, dict) and output.get("error"):
                return False, f"Tool reported error in result: {output['error']}"
            return True, "Operation completed without errors"
        return False, "Tool produced no output"

    # -- 4. Self-Healing & Parameter Adaptation -----------------------------
    async def _heal_run_python(self, step: PlanStep, error_msg: str) -> bool:
        """Ask the LLM to fix failing run_python code using the real stderr."""
        if self._llm is None:
            return False
        current_code = str(step.arguments.get("code", ""))
        prompt = (
            "The following Python code failed in the Azmuth sandbox.\n\n"
            f"Current code:\n```python\n{current_code}\n```\n\n"
            f"Error output:\n{error_msg[-2000:]}\n\n"
            'Return ONLY valid JSON with the fixed code: {"code": "..."}'
        )
        req = ChatRequest(
            messages=(
                Message(Role.SYSTEM, "You fix Python scripts. Return ONLY JSON {\"code\": \"...\"}."),
                Message(Role.USER, prompt),
            ),
            temperature=0.1,
            max_tokens=1500,
        )
        try:
            resp = await self._llm.chat(req)
            raw = (resp.content or "").strip()
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if not m:
                return False
            data = json.loads(m.group(0))
            new_code = data.get("code")
            if isinstance(new_code, str) and new_code.strip():
                step.arguments["code"] = new_code
                return True
        except Exception as exc:
            logger.debug("_heal_run_python failed: %s", exc)
        return False

    def _heal_step_arguments(self, step: PlanStep, attempt: int) -> None:
        """Adjust tool arguments to recover from known failure modes."""
        if step.tool_name == "search_web":
            q = step.arguments.get("query", "")
            # Simplify query on retry (remove exact year numbers or over-specific tokens)
            if "updates" in q:
                step.arguments["query"] = re.sub(r"\b(202[4-9]|latest|updates)\b", "", q).strip() + " release notes changelog"
            else:
                step.arguments["query"] = q.split(" and ")[0].strip()

        elif step.tool_name == "file_write":
            # If path failed, try keeping just the clean basename without illegal chars
            raw_path = step.arguments.get("path", "")
            base = Path(raw_path).name
            clean_base = re.sub(r'[<>:"/\\|?*]', '_', base).strip() or "output.md"
            step.arguments["path"] = str((self._workspace_root / clean_base).resolve())

        elif step.tool_name == "file_read":
            # Fallback file targets if specific file is missing
            path = step.arguments.get("path", "")
            if "requirements" in path.lower():
                step.arguments["path"] = "requirements.txt"

    # -- 5. Synthesize Step (LLM Synthesis & Reasoning) ---------------------
    async def _synthesize_step(self, step: PlanStep, goal: str, previous_outputs: dict[int, Any]) -> str:
        """Generate analysis or report content using the outputs of previous steps."""
        context_parts = []
        for sid, out in previous_outputs.items():
            if isinstance(out, dict):
                text = out.get("reference_block") or out.get("content") or json.dumps(out, default=str)
            else:
                text = str(out)
            context_parts.append(f"--- Output of Step {sid} ---\n{text[:2500]}")

        combined_context = "\n\n".join(context_parts)

        prompt = f"""Goal: {goal}
Current Task: {step.title}
Task Details: {step.description}

Data gathered from previous steps:
{combined_context}

Please generate the comprehensive analysis or report requested. 
Be thorough, structured, and practical. Use Markdown with headings, bullet points, comparisons, and actionable recommendations.
"""
        req = ChatRequest(
            messages=(
                Message(Role.SYSTEM, "You are a senior software architect creating an executive technical report."),
                Message(Role.USER, prompt),
            ),
            temperature=0.3,
            max_tokens=2500,
        )
        resp = await self._llm.chat(req)
        return (resp.content or "").strip()

    # -- 6. Heuristic Fallback Plan Builder ---------------------------------
    def _build_heuristic_plan(self, goal: str) -> list[PlanStep]:
        """Deterministic fallback plan generator for common compound requests."""
        lowered = goal.lower()
        steps = []

        if "django" in lowered or "project" in lowered or "framework" in lowered:
            steps.append(
                PlanStep(
                    id=1,
                    title="Inspect Project Dependencies",
                    description="Read project requirements and configuration to inspect current packages and versions",
                    action_type="tool",
                    tool_name="file_read",
                    arguments={"path": "requirements.txt"},
                    dependencies=[],
                    verification="check_success",
                )
            )
            steps.append(
                PlanStep(
                    id=2,
                    title="Research Latest Framework Updates",
                    description="Search the web for the latest Django REST Framework releases, features, and breaking changes",
                    action_type="tool",
                    tool_name="search_web",
                    arguments={"query": "Django REST Framework latest release notes new features updates"},
                    dependencies=[],
                    verification="has_results",
                )
            )
            steps.append(
                PlanStep(
                    id=3,
                    title="Synthesize Comparative Technical Report",
                    description="Compare the latest DRF updates with project dependencies and write a structured evaluation report",
                    action_type="synthesize",
                    tool_name=None,
                    arguments={},
                    dependencies=[1, 2],
                    verification="content_not_empty",
                )
            )
            steps.append(
                PlanStep(
                    id=4,
                    title="Save Report to Project Directory",
                    description="Write the generated comparative report to a markdown file in the project folder",
                    action_type="tool",
                    tool_name="file_write",
                    arguments={
                        "path": "DRF_UPDATE_REPORT.md",
                        "content": "{{report_content}}",
                        "overwrite": True,
                    },
                    dependencies=[3],
                    verification="file_exists",
                )
            )
            steps.append(
                PlanStep(
                    id=5,
                    title="Verify Report File on Disk",
                    description="Verify the saved report exists on disk, has non-zero size, and is accessible",
                    action_type="tool",
                    tool_name="file_stat",
                    arguments={"path": "DRF_UPDATE_REPORT.md"},
                    dependencies=[4],
                    verification="file_exists",
                )
            )
        else:
            # Generic 3-step autonomous workflow
            steps.append(
                PlanStep(
                    id=1,
                    title="Information Gathering & Search",
                    description=f"Gather required context and information for: {goal}",
                    action_type="tool",
                    tool_name="search_web",
                    arguments={"query": goal[:100]},
                    dependencies=[],
                    verification="has_results",
                )
            )
            steps.append(
                PlanStep(
                    id=2,
                    title="Synthesize Deliverables",
                    description="Synthesize findings into a comprehensive structured response",
                    action_type="synthesize",
                    tool_name=None,
                    arguments={},
                    dependencies=[1],
                    verification="content_not_empty",
                )
            )
            steps.append(
                PlanStep(
                    id=3,
                    title="Save Output Artifact",
                    description="Save the outcome as a project artifact file",
                    action_type="tool",
                    tool_name="file_write",
                    arguments={
                        "path": "AZMUTH_TASK_REPORT.md",
                        "content": "{{report_content}}",
                        "overwrite": True,
                    },
                    dependencies=[2],
                    verification="file_exists",
                )
            )
        return steps

    # -- 7. Helpers ---------------------------------------------------------
    def _resolve_arguments(
        self,
        arguments: dict[str, Any],
        step_outputs: dict[int, Any],
        synthesized_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Replace template variables like {{report_content}} or {{step_1_result}}."""
        resolved = dict(arguments)
        for k, v in resolved.items():
            if isinstance(v, str):
                if v == "{{report_content}}":
                    resolved[k] = synthesized_data.get("report_content", "(No content generated)")
                elif v.startswith("{{step_") and v.endswith("_result}}"):
                    try:
                        step_num = int(v.replace("{{step_", "").replace("_result}}", ""))
                        resolved[k] = str(step_outputs.get(step_num, ""))
                    except ValueError:
                        pass
        return resolved

    def _generate_plan_summary(self, plan: ExecutionPlan) -> str:
        succeeded = sum(1 for s in plan.steps if s.status == StepStatus.COMPLETED)
        skipped = sum(1 for s in plan.steps if s.status == StepStatus.SKIPPED)
        total = len(plan.steps)
        verified_count = sum(1 for s in plan.steps if s.verified)
        pending = sum(1 for s in plan.steps if s.status == StepStatus.PENDING)

        if plan.status == PlanStatus.COMPLETED:
            return (
                f"Autonomous Plan Execution Completed Successfully. "
                f"Finished {succeeded}/{total} steps ({skipped} skipped as duplicates), "
                f"{verified_count} steps verified. "
                f"Deliverables created and verified on disk: {len(plan.deliverables)}. "
                f"Tool budget used: {plan.tools_used}/{plan.total_tool_budget}."
            )
        elif plan.status == PlanStatus.RUNNING and pending > 0:
            return (
                f"Plan suspended after round {plan.rounds_executed} — round tool budget reached. "
                f"Completed {succeeded}/{total} steps so far, {pending} pending. "
                f"Tool budget: {plan.tools_used}/{plan.total_tool_budget}. "
                f"Call execute_plan() again to resume."
            )
        else:
            failed_steps = [f"Step {s.id} ({s.title}): {s.error}" for s in plan.steps if s.status == StepStatus.FAILED]
            failure_detail = "; ".join(failed_steps) if failed_steps else "One or more steps failed"
            return (
                f"Autonomous Plan Execution FAILED. "
                f"Completed {succeeded}/{total} steps before failure. "
                f"Tool budget used: {plan.tools_used}/{plan.total_tool_budget}. "
                f"Error details: {failure_detail}."
            )

    async def _emit(self, event_name: EventName, **payload: Any) -> None:
        if self._event_bus is not None:
            try:
                await self._event_bus.publish(
                    Event(name=event_name, source="planner", payload=payload)
                )
            except Exception:
                pass


__all__ = [
    "AutonomousPlanner",
    "ExecutionPlan",
    "PlanStep",
    "PlanStatus",
    "StepStatus",
    "is_planning_goal",
]
