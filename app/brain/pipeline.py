
"""The turn orchestrator.

The pipeline is deliberately explicit about *what* it does and *when*,
but the decisions themselves are made by the LLM wherever possible:

* Tool selection - native function calling (the model returns
  ``tool_calls`` in the response). A text parser remains as a fallback
  for providers that do not implement the ``tools`` parameter.
* Search - the model calls ``search_web`` on its own judgment. There is
  no keyword heuristic.
* Response language - observed from the text the model actually produced,
  not predicted from the input.
* Misspellings - handled by the model, not by preprocessing.

The runtime still enforces the security boundaries (permission, sandbox,
confirmation). Those are *rules*, not decisions.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
import inspect
import re
from app.brain.prompts import build_system_prompt
from app.brain.context import RuntimeContext
from app.brain.purpose import detect_purpose, is_vision_query
from app.brain.tool_calls import ToolCall, parse_tool_calls
from app.config.logging_setup import get_logger
from app.core.errors import ProviderUnavailableError
from app.core.events import Event, EventBus, EventName
from app.llm.base import LLMProvider
from app.llm.messages import (
    ChatRequest,
    Message,
    Role,
    ToolDefinition,
)
from app.llm.registry import ModelPurpose
from app.llm.router import LLMRouter
from app.memory.conversation.context import ContextBuilder
from app.memory.conversation.manager import SessionManager
from app.memory.manager import MemoryManager
from app.search.manager import SearchManager
from app.search.models import SearchQuery
from app.security.modes import Principal
from app.tools.manager import ToolManager
from app.voice.input_manager import UserInput
from app.brain.planner import AutonomousPlanner, PlanStatus, StepStatus, is_planning_goal
from app.brain.loop_guard import LoopGuard, LoopDetectedError
from app.llm.messages import ChatRequest, ChatResponse, ModelInfo
from app.tools.base import Tool, ToolMetadata, ToolResult

logger = get_logger("brain.pipeline")

@dataclass(frozen=True, slots=True)
class ConfirmationRequest:
    """A pending confirmation the pipeline is asking the user about."""

    tool_name: str
    arguments: dict[str, Any]
    capability: str
    risk_level: str
    description: str

from collections.abc import Awaitable, Callable

Confirmer = Callable[[ConfirmationRequest], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class ToolExecutionRecord:
    name: str
    arguments: dict[str, Any]
    success: bool
    tool_call_id: str = ""
    output: Any = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "arguments": dict(self.arguments),
            "success": self.success,
            "output": self.output if self.success else None,
            "error": self.error,
        }




@dataclass(frozen=True, slots=True)
class BrainResponse:
    """The outcome of one turn."""

    text: str
    session_id: str
    language: str
    purpose: str = "chat"
    model: str = ""
    provider: str = ""
    tool_calls: tuple[ToolExecutionRecord, ...] = ()
    used_search: bool = False
    used_memory: bool = False
    iterations: int = 0
    stopped_reason: str = "final"
    spoken_text: str = "" 

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "session_id": self.session_id,
            "language": self.language,
            "purpose": self.purpose,
            "tool_calls": [r.as_dict() for r in self.tool_calls],
            "used_search": self.used_search,
            "used_memory": self.used_memory,
            "iterations": self.iterations,
            "stopped_reason": self.stopped_reason,
        }


class Brain:
    def __init__(
        self,
        *,
        llm: LLMProvider,
        memory: MemoryManager,
        sessions: SessionManager,
        language,
        tools: ToolManager,
        search: SearchManager,
        event_bus: EventBus | None = None,
        llm_router: LLMRouter | None = None,
        max_tool_iterations: int = 12,          # LLM/tool loop cap (was 5 — raised to 12)
        planner_round_budget: int = 10,         # Planner tool calls per round
        planner_total_budget: int = 50,         # Planner total tool call ceiling
        temperature: float = 0.7,
        memory_top_k: int = 3,
        planner: AutonomousPlanner | None = None,
    ) -> None:
        self._llm = llm
        self._llm_router = llm_router
        self._memory = memory
        self._sessions = sessions
        self._language = language
        self._tools = tools
        self._search = search
        self._event_bus = event_bus
        self._max_tool_iterations = max(1, max_tool_iterations)
        self._temperature = temperature
        self._memory_top_k = memory_top_k
        self._context_builder = ContextBuilder()
        self._identity_block: str = ""
        self._location_service = None
        self._prefer_local: bool = False
        ws_root = None
        if self._tools is not None:
            try:
                approved = self._tools.permissions.workspace_policy.approved_roots
                if approved:
                    ws_root = approved[0]
            except Exception:
                pass
        self._planner = planner or AutonomousPlanner(
            llm=self._llm,
            tools=self._tools,
            event_bus=self._event_bus,
            workspace_root=ws_root or Path.cwd(),
            round_tool_budget=planner_round_budget,
            total_tool_budget=planner_total_budget,
        )

    @property
    def tools(self) -> ToolManager:
        """Expose the tool manager for narration and diagnostics."""
        return self._tools

    def set_prefer_local(self, value: bool) -> None:
        """Route every subsequent turn to the local backend.

        The router's ``by_purpose(..., local_only=True)`` returns only
        entries marked ``is_local``. Setting this flag bypasses cloud
        tiers entirely — useful when the user wants to conserve cloud
        quota or run fully offline.
        """
        self._prefer_local = value

    def _handle_local_toggle(self, enabled: bool) -> None:
        """Toggle local-only LLM routing for this Brain instance."""
        self.set_prefer_local(enabled)
        
        
    def attach_identity(self, *, identity_block: str, location_service) -> None:
        self._identity_block = identity_block
        self._location_service = location_service
        
        
    def get_tools_for_llm(
        self, principal: Principal | None = None
    ) -> tuple[ToolMetadata, ...]:
        """Return the tools exposed to the LLM for the given principal.

        Presents ONE reminder-creation tool (schedule_reminder), not both
        schedule_task and schedule_reminder.
        """
        tools = list(self._tools.list_metadata(principal=principal))
        if any(m.name == "schedule_reminder" for m in tools):
            tools = [m for m in tools if m.name != "schedule_task"]
        return tuple(tools)

    async def respond(
        self,
        user_input: UserInput,
        *,
        conversation_id: str | None = None,
        principal: Principal | None = None,
        confirmer: "Confirmer | None" = None,
        on_delta: Callable[[str], Awaitable[None] | None] | None = None,
    ) -> BrainResponse:
        principal = principal or Principal.anonymous()

        session = self._sessions.get_or_create(
            conversation_id, language=user_input.language
        )
        session.add_user_message(user_input.text)

        await self._emit(
            EventName.USER_INPUT,
            text_length=len(user_input.text),
            source=user_input.source.value,
            language=user_input.language,
        )

        
        effective_user_id = principal.user_id or "anonymous"
        early = await self._handle_memory_command(
            user_input.text,
            user_id=effective_user_id,
            session=session,
        )
        if early is not None:
            return early

        # ---- retrieval ---------------------------------------------------
        # Only *unconditional* retrieval remains here. Search is now a tool
        # the model calls on its own; memory recall is the one thing we do
        # proactively because the model cannot query it on demand.
        retrieval_parts: list[str] = []
        used_memory = False

        effective_user_id = principal.user_id or "anonymous"
        memory_block = self._recall_memory(
            user_input.text, user_id=effective_user_id
        )
        if memory_block:
            retrieval_parts.append(memory_block)
            used_memory = True

        retrieval_context = (
            "\n\n".join(retrieval_parts) if retrieval_parts else None
        )

        # ---- build prompt ------------------------------------------------
        tools_for_llm = self.get_tools_for_llm(principal=principal)
        tool_definitions = tuple(
            ToolDefinition(
                name=m.name,
                description=m.description,
                parameters=m.parameters,
            )
            for m in tools_for_llm
        )

        location = None
        if self._location_service is not None:
            location = self._location_service.location
        runtime_context = RuntimeContext(location=location)

        # The session's current language is a hint. The model is told to
        # follow the user's actual language, so the hint is soft.
        # Detect the session language from the *input*. The model decides
        # what to actually produce — but the session hint must reflect what
        # the user sent, or a single Latin-script reply would flip Tamil
        # back to English permanently.
        resolved_language = self._language.resolve_response_language(
            user_input.text, session_language=session.language
        )
        session.language = resolved_language
        language_hint = resolved_language
        system_prompt = build_system_prompt(
            language=language_hint,
            tools=tools_for_llm,
            identity_block=self._identity_block,
            context_block=runtime_context.as_prompt_block(),
        )

        messages: list[Message] = list(
            self._context_builder.build(
                session,
                system_prompt=system_prompt,
                retrieval_context=retrieval_context,
            )
        )

        # ---- Autonomous Agent Planning for multi-step compound goals -----
        if is_planning_goal(user_input.text):
            try:
                logger.info("detected multi-step autonomous goal; routing to AutonomousPlanner")
                if self._llm_router is not None:
                    try:
                        self._planner.set_llm(self._llm_router.for_purpose(ModelPurpose.REASONING))
                    except Exception:
                        pass

                plan = await self._planner.create_plan(user_input.text, context=retrieval_context or "")

                # ---- Multi-round execution loop (Requirement 3 & 8) ----
                # Execute up to max_tool_iterations rounds; each round can spend
                # plan.round_tool_budget tool calls before suspending.  We loop
                # until the plan is complete/failed or we exhaust the round limit.
                max_planner_rounds = self._max_tool_iterations
                for _round in range(max_planner_rounds):
                    plan = await self._planner.execute_plan(
                        plan, principal=principal, confirmer=confirmer
                    )
                    if plan.is_complete:
                        break
                    # Plan still RUNNING with pending steps — another round needed
                    logger.info(
                        "Planner round %d/%d complete; plan still has pending steps (plan_id=%s)",
                        _round + 1, max_planner_rounds, plan.id,
                    )
                else:
                    # Exhausted all rounds without finishing — fail the plan (Requirement 9)
                    if not plan.is_complete:
                        plan.status = PlanStatus.FAILED
                        from app.brain.planner import _now as _planner_now
                        plan.completed_at = _planner_now()
                        pending_titles = [s.title for s in plan.steps if s.status == StepStatus.PENDING]
                        plan.summary = (
                            f"Plan could not complete within {max_planner_rounds} execution rounds. "
                            f"Remaining unfinished steps: {', '.join(pending_titles) or 'none'}. "
                            f"Tool budget used: {plan.tools_used}/{plan.total_tool_budget}."
                        )
                        logger.warning(
                            "Planner exceeded %d rounds without completing plan %s",
                            max_planner_rounds, plan.id,
                        )

                plan_tool_records: list[ToolExecutionRecord] = []
                used_search = False
                for step in plan.steps:
                    if step.tool_name:
                        plan_tool_records.append(
                            ToolExecutionRecord(
                                name=step.tool_name,
                                arguments=step.arguments,
                                success=(step.status.value == "completed"),
                                output=step.result,
                                error=step.error,
                            )
                        )
                        if step.tool_name == "search_web":
                            used_search = True

                plan_blocks = [
                    plan.render_markdown(),
                    "",
                    "---",
                    "",
                ]
                if plan.status == PlanStatus.COMPLETED:
                    synthesized_results = [s.result for s in plan.steps if s.action_type == "synthesize" and s.result]
                    if synthesized_results:
                        plan_blocks.extend(synthesized_results)
                    else:
                        plan_blocks.append(plan.summary)

                    spoken_summary = (
                        f"I have successfully executed and verified the autonomous plan for your request. "
                        f"{len(plan.steps)} steps executed and verified."
                    )
                    if plan.deliverables:
                        spoken_summary += f" Deliverables: {', '.join(plan.deliverables)}."
                else:
                    plan_blocks.append(f"**Execution Notice:** {plan.summary}")
                    failed_details = [f"- Step {s.id} ({s.title}): {s.error}" for s in plan.steps if s.status == StepStatus.FAILED]
                    if failed_details:
                        plan_blocks.append("\n**Failure Breakdown:**\n" + "\n".join(failed_details))
                    spoken_summary = f"Plan execution encountered an error and could not be verified: {plan.summary}"

                final_text = "\n\n".join(plan_blocks) + f"\n\n<spoken>\n{spoken_summary}\n</spoken>"
                display_text, spoken_text = _split_spoken_block(final_text)
                if not spoken_text:
                    spoken_text = spoken_summary
                final_text = display_text
                session.add_assistant_message(final_text)

                # Auto-extract and store personal facts from user message
                try:
                    from app.memory.extractor import extract_facts
                    extract_facts(user_input.text, user_id=effective_user_id, memory=self._memory)
                except Exception:
                    logger.exception("fact extraction failed", extra={"event": "MEMORY_EXTRACT_FAILED"})

                return BrainResponse(
                    text=final_text,
                    session_id=session.id,
                    language=resolved_language,
                    purpose="planning",
                    model=getattr(self._llm, "model", "planner"),
                    provider=getattr(self._llm, "provider", "autonomous_planner"),
                    tool_calls=tuple(plan_tool_records),
                    used_search=used_search,
                    used_memory=used_memory,
                    iterations=plan.rounds_executed,
                    stopped_reason="plan_completed" if plan.status == PlanStatus.COMPLETED else "plan_failed",
                    spoken_text=spoken_text,
                )
            except Exception as exc:
                logger.warning(
                    "Autonomous planner encountered an issue, falling back to conversational loop: %s",
                    exc,
                )

        # ---- LLM / tool loop ---------------------------------------------
        final_text = ""
        tool_records: list[ToolExecutionRecord] = []
        stopped_reason = "final"
        iterations = 0
        used_search = False

        vision_turn = is_vision_query(user_input.text)
        purpose = detect_purpose(user_input.text, language=language_hint)
        if vision_turn and purpose is ModelPurpose.FAST:
            purpose = ModelPurpose.CHAT
        routing_reason = (
            "vision" if vision_turn
            else ("has_tools" if tool_definitions else "default")
        )
        last_model = ""
        last_provider = ""
        loop_guard = LoopGuard()
        loop_aborted = False
        for iteration in range(1, self._max_tool_iterations + 1):
            iterations = iteration
            if on_delta is not None and not tool_definitions:
                response = await self._chat_stream_with_fallback(
                    purpose, tuple(messages),
                    tools=tool_definitions, on_delta=on_delta,
                    routing_reason=routing_reason,
                )
            else:
                response = await self._chat_with_fallback(
                    purpose, tuple(messages), tools=tool_definitions,
                    routing_reason=routing_reason,
                )
            last_model = response.model or last_model
            last_provider = response.provider or last_provider

            # Prefer native tool calls. Fall back to text parsing only
            # when the provider returned none and the content looks like
            # it might contain a fenced tool block.
            native_calls = response.tool_calls
            if native_calls:
                calls: list[ToolCall] = [
                    ToolCall(name=tc.name, arguments=tc.arguments)
                    for tc in native_calls
                ]
                cleaned = (response.content or "").strip()
                call_ids: list[str] = [tc.id for tc in native_calls]
            else:
                raw_text = response.content or ""
                parsed, cleaned = parse_tool_calls(raw_text)
                calls = list(parsed)
                call_ids = ["" for _ in calls]

            if not calls:
                final_text = cleaned or (response.content or "").strip()
                stopped_reason = "final"
                break

            # Preserve the assistant's tool-calling turn so the model sees
            # what it said when the result comes back.
            assistant_content = response.content or ""
            messages.append(Message(Role.ASSISTANT, assistant_content))

            for call, call_id in zip(calls, call_ids):
                try:
                    loop_guard.check(call.name, call.arguments)
                except LoopDetectedError as exc:
                    logger.warning("LoopGuard detected repetitive tool call: %s", exc)
                    stopped_reason = "loop_detected"
                    final_text = f"Tool execution interrupted by LoopGuard: {exc.reason}"
                    loop_aborted = True
                    break

                record = await self._execute_tool(
                    call,
                    principal=principal,
                    tool_call_id=call_id,
                    confirmer=confirmer,
                )
                tool_records.append(record)
                if call.name == "search_web":
                    used_search = True

                # Native path: reply with Role.TOOL and the tool_call_id.
                # Text path: reply with a SYSTEM message carrying the same
                # [TOOL_RESULT ...] framing the parser expects.
                if call_id:
                    messages.append(
                        Message(
                            Role.TOOL,
                            _tool_result_text(record),
                            tool_call_id=call_id,
                        )
                    )
                else:
                    messages.append(
                        Message(Role.SYSTEM, _format_tool_result(record))
                    )

            if loop_aborted:
                break
        else:
            stopped_reason = "max_iterations"
            tool_names = ", ".join(r.name for r in tool_records) or "none"
            final_text = (
                f"I reached the maximum tool-call rounds for this turn "
                f"({self._max_tool_iterations} rounds). "
                f"Tools I used: {tool_names}. "
                f"The request may need a different approach — "
                f"could you describe what you want more specifically?"
            )
            logger.warning(
                "tool loop hit max iterations",
                extra={"event": "BRAIN_MAX_ITERATIONS", "session_id": session.id},
            )
        # Fabricated-script guard. The model sometimes invents
        # pseudo-Tamil by mixing scripts; that is worse than plain
        # English. When detected, replace with a clear apology.
        from app.brain.language_guard import fallback_message, is_suspect_response
        if is_suspect_response(final_text, expected_language=resolved_language):
            logger.warning(
                "detected fabricated script in response",
                extra={
                    "event": "BRAIN_FABRICATED_SCRIPT",
                    "session_id": session.id,
                    "language": resolved_language,
                },
            )
            final_text = fallback_message(expected_language=resolved_language)
            
        display_text, spoken_text = _split_spoken_block(final_text)
        if not spoken_text:
            spoken_text = _fallback_spoken(display_text)
        final_text = display_text
        if not final_text.strip():
            raise ProviderUnavailableError(
                "every LLM provider returned an empty response for this "
                "turn. Check the LLM provider log lines (BRAIN_PROVIDER_FALLBACK, "
                "BRAIN_EMPTY_RESPONSE) for the specific cause."
            )
        session.add_assistant_message(final_text)

        # Auto-extract and store personal facts from user message
        try:
            from app.memory.extractor import extract_facts
            extract_facts(user_input.text, user_id=effective_user_id, memory=self._memory)
        except Exception:
            logger.exception("fact extraction failed", extra={"event": "MEMORY_EXTRACT_FAILED"})

        # Observe the language the model actually produced. This replaces
        # the input-side heuristic: whatever the model emitted becomes the
        # session's language for next turn's hint.

        return BrainResponse(
            text=final_text,
            session_id=session.id,
            language=resolved_language,
            purpose=purpose.value,
            model=last_model,
            provider=last_provider,
            tool_calls=tuple(tool_records),
            used_search=used_search,
            used_memory=used_memory,
            iterations=iterations,
            stopped_reason=stopped_reason,
            spoken_text=spoken_text, 
        )

    # -- provider dispatch -------------------------------------------------
    def _resolve_purpose_and_reason(
        self,
        purpose: ModelPurpose,
        tools: tuple[ToolDefinition, ...],
        routing_reason: str,
    ) -> tuple[ModelPurpose, str]:
        if tools and purpose is ModelPurpose.FAST:
            reason = routing_reason if routing_reason == "vision" else "has_tools"
            logger.info(
                "upgraded purpose FAST→CHAT for tool-using turn",
                extra={
                    "event": "ROUTER_PURPOSE_UPGRADED",
                    "original": "fast",
                    "effective": "chat",
                    "tool_count": len(tools),
                    "reason": reason,
                },
            )
            return ModelPurpose.CHAT, reason
        return purpose, routing_reason

    async def _chat_with_fallback(
        self,
        purpose: ModelPurpose,
        messages: tuple[Message, ...],
        tools: tuple[ToolDefinition, ...] = (),
        routing_reason: str = "default",
    ):
        # Tool-using turns must go to a capable model, not a fast/lightweight one.
        # FAST → CHAT when tools are present; never send tool calls to FAST tier.
        effective_purpose, reason = self._resolve_purpose_and_reason(
            purpose, tools, routing_reason
        )

        request = ChatRequest(
            messages=messages,
            temperature=self._temperature,
            tools=tools,
        )

        if self._llm_router is None:
            logger.info(
                "routing turn",
                extra={
                    "event": "TURN_ROUTED",
                    "purpose": effective_purpose.value,
                    "model": getattr(self._llm, "model", "") or "direct",
                    "reason": reason,
                    "native_tools": bool(tools),
                },
            )
            return await self._llm.chat(request)

        last_exception: Exception | None = None
        candidates = self._llm_router.registry.by_purpose(
            effective_purpose, local_only=self._prefer_local
        )
        for spec in candidates:
            try:
                provider = self._llm_router.for_model(spec.name)
            except Exception as exc:
                last_exception = exc
                continue
            try:
                logger.info(
                    "routing turn",
                    extra={
                        "event": "TURN_ROUTED",
                        "purpose": effective_purpose.value,
                        "model": spec.name,
                        "reason": reason,
                        "native_tools": bool(tools),
                    },
                )
                response = await provider.chat(request)
                if not (response.content or "").strip() and not response.tool_calls:
                    # An HTTP-200 response with no content and no tool calls
                    # is not a usable turn. Treat as provider failure so the
                    # next candidate is tried.
                    logger.warning(
                        "provider returned empty response",
                        extra={
                            "event": "BRAIN_EMPTY_RESPONSE",
                            "model": spec.name,
                        },
                    )
                    self._llm_router.record_failure(spec)
                    last_exception = RuntimeError(
                        f"{spec.name} returned empty content"
                    )
                    continue
                self._llm_router.record_success(spec)
                return response
            except Exception as exc:
                logger.warning(
                    "provider failed, trying next",
                    extra={
                        "event": "BRAIN_PROVIDER_FALLBACK",
                        "failed_model": spec.name,
                        "reason": str(exc),
                    },
                )
                # Also print the exception in the message so it appears in
                # the default log line, not only in the JSON context.
                logger.warning(
                    "provider %s failed: %s", spec.name, exc,
                )

                # Mark it unhealthy so the next turn within the cooldown
                # window skips straight to a working provider.
                self._llm_router.record_failure(spec)
                last_exception = exc
                continue

        try:
            return await self._llm.chat(request)
        except Exception as exc:
            if last_exception is not None:
                raise ProviderUnavailableError(
                    f"all providers failed for purpose {purpose.value!r}"
                ) from last_exception
            raise ProviderUnavailableError(
                f"all providers failed for purpose {purpose.value!r}"
            ) from exc

    async def _chat_stream_with_fallback(
        self,
        purpose: ModelPurpose,
        messages: tuple[Message, ...],
        tools: tuple[ToolDefinition, ...] = (),
        on_delta: Callable[[str], Awaitable[None] | None] | None = None,
        routing_reason: str = "default",
    ):
        """Stream a round. Falls back to non-streaming on any failure."""
        effective_purpose, reason = self._resolve_purpose_and_reason(
            purpose, tools, routing_reason
        )

        request = ChatRequest(
            messages=messages,
            temperature=self._temperature,
            tools=tools,
        )

        if self._llm_router is None:
            logger.info(
                "routing turn",
                extra={
                    "event": "TURN_ROUTED",
                    "purpose": effective_purpose.value,
                    "model": getattr(self._llm, "model", "") or "direct",
                    "reason": reason,
                    "native_tools": bool(tools),
                },
            )
            try:
                return await self._stream_one(self._llm, request, on_delta)
            except Exception:
                logger.warning(
                    "streaming failed; falling back to chat",
                    extra={"event": "BRAIN_STREAM_FALLBACK"},
                )
                return await self._llm.chat(request)

        last_exception: Exception | None = None
        candidates = self._llm_router.registry.by_purpose(
            effective_purpose, local_only=self._prefer_local
        )
        for spec in candidates:
            try:
                provider = self._llm_router.for_model(spec.name)
            except Exception as exc:
                last_exception = exc
                continue
            try:
                logger.info(
                    "routing turn",
                    extra={
                        "event": "TURN_ROUTED",
                        "purpose": effective_purpose.value,
                        "model": spec.name,
                        "reason": reason,
                        "native_tools": bool(tools),
                    },
                )
                response = await self._stream_one(provider, request, on_delta)
                self._llm_router.record_success(spec)
                return response
            except Exception as exc:
                logger.warning(
                    "streaming failed for model; falling back",
                    extra={
                        "event": "BRAIN_STREAM_PROVIDER_FALLBACK",
                        "failed_model": spec.name,
                        "reason": str(exc),
                    },
                )
                try:
                    response = await provider.chat(request)
                    self._llm_router.record_success(spec)
                    return response
                except Exception as exc2:
                    self._llm_router.record_failure(spec)
                    last_exception = exc2
                    continue

        if last_exception is not None:
            raise ProviderUnavailableError(
                f"all providers failed for purpose {purpose.value!r}"
            ) from last_exception
        raise ProviderUnavailableError(
            f"all providers failed for purpose {purpose.value!r}"
        )

    async def _stream_one(
        self,
        provider: LLMProvider,
        request: ChatRequest,
        on_delta: Callable[[str], Awaitable[None] | None] | None,
    ):
        """Stream one provider's chunks, forwarding text deltas live."""
        buffer: list[str] = []
        tool_calls: list[Any] = []
        model_name = getattr(provider, "model", "") or ""
        provider_name = getattr(provider, "name", "") or ""

        async for chunk in provider.stream(request):
            tc = getattr(chunk, "tool_calls", None)
            if tc:
                tool_calls.extend(tc)
            text = (
                getattr(chunk, "text", None)
                or getattr(chunk, "delta", None)
                or ""
            )
            if text and not tool_calls:
                buffer.append(text)
                if on_delta is not None:
                    try:
                        result = on_delta(text)
                        if inspect.isawaitable(result):
                            await result
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception(
                            "on_delta callback raised",
                            extra={"event": "BRAIN_ON_DELTA_FAILED"},
                        )

        if tool_calls:
            try:
                return ChatResponse(
                    content="".join(buffer),
                    tool_calls=tuple(tool_calls),
                    model=model_name,
                    provider=provider_name,
                )
            except TypeError:
                # ChatResponse constructor shape differs; redo non-streaming
                return await provider.chat(request)

        try:
            return ChatResponse(
                content="".join(buffer),
                tool_calls=(),
                model=model_name,
                provider=provider_name,
            )
        except TypeError:
            return await provider.chat(request)

    # -- internals ---------------------------------------------------------
    def _recall_memory(self, text: str, *, user_id: str) -> str | None:
        try:
            # Use hybrid recall (BM25 + vector RRF) when setting is on
            # and the memory manager supports it. Falls back to plain
            # vector recall otherwise (Phase 1.4).
            mem_settings = getattr(self, "_settings", None) or getattr(self._memory, "_settings", None)
            use_hybrid = getattr(mem_settings, "memory_hybrid_recall", True)
            if use_hybrid and hasattr(self._memory, "recall_hybrid"):
                records = self._memory.recall_hybrid(
                    text, user_id=user_id, top_k=self._memory_top_k
                )
            else:
                records = self._memory.recall(
                    text, user_id=user_id, top_k=self._memory_top_k
                )
        except Exception:
            logger.exception(
                "memory recall failed",
                extra={"event": "BRAIN_MEMORY_RECALL_FAILED"},
            )
            return None
        
        logger.info(
            "memory recall",
            extra={
                "event": "MEMORY_RECALL",
                "user_id": user_id,
                "query": text[:80],
                "count": len(records or []),
            },
        )

        if not records:
            return None
        lines = ["[MEMORY]"]
        for record in records:
            lines.append(f"- ({record.kind.value}) {record.content}")
        return "\n".join(lines)


    async def _handle_memory_command(
        self,
        text: str,
        *,
        user_id: str,
        session,
    ) -> BrainResponse | None:
        """If ``text`` is a memory-list/forget command, answer deterministically.

        Returns None if the input is not a memory command — caller falls
        through to the normal LLM loop.
        """
        action, payload = _detect_memory_command(text)
        if not action:
            return None

        if action == "list":
            try:
                records = self._memory.list_for_user(user_id, limit=50)
            except Exception:
                logger.exception(
                    "memory list failed",
                    extra={"event": "MEMORY_LIST_FAILED", "user_id": user_id},
                )
                records = []
            logger.info(
                "memory list command",
                extra={
                    "event": "MEMORY_LIST_COMMAND",
                    "user_id": user_id,
                    "count": len(records),
                },
            )
            reply = _format_memory_list(records)
            session.add_assistant_message(reply)
            return BrainResponse(
                text=reply,
                session_id=session.id,
                language=session.language or "en",
                purpose="memory",
                model="",
                provider="memory",
                used_memory=True,
                stopped_reason="memory_list",
                spoken_text=reply,
            )

        # action == "forget"
        try:
            matches = self._memory.search(
                payload or "", user_id=user_id, limit=20
            )
        except Exception:
            logger.exception(
                "memory search for forget failed",
                extra={"event": "MEMORY_FORGET_SEARCH_FAILED"},
            )
            matches = []

        if not matches:
            reply = f"I couldn't find a stored memory matching '{payload}'."
        else:
            deleted = 0
            for r in matches:
                try:
                    if self._memory.delete(r.id):
                        deleted += 1
                except Exception:
                    logger.exception(
                        "memory delete failed",
                        extra={"event": "MEMORY_DELETE_FAILED",
                               "memory_id": r.id},
                    )
            reply = (
                f"Forgotten: {deleted} memory item"
                f"{'s' if deleted != 1 else ''} matching '{payload}'."
            )
        logger.info(
            "memory forget command",
            extra={
                "event": "MEMORY_FORGET_COMMAND",
                "user_id": user_id,
                "payload": payload,
                "deleted": deleted if matches else 0,
            },
        )
        session.add_assistant_message(reply)
        return BrainResponse(
            text=reply,
            session_id=session.id,
            language=session.language or "en",
            purpose="memory",
            model="",
            provider="memory",
            used_memory=True,
            stopped_reason="memory_forget",
            spoken_text=reply,
        )
        
        
    async def _execute_tool(
        self,
        call: ToolCall,
        *,
        principal: Principal,
        tool_call_id: str = "",
        confirmer: "Confirmer | None" = None,
    ) -> ToolExecutionRecord:
        await self._emit(
            EventName.TOOL_STARTED,
            tool=call.name,
            capability="(pipeline)",
            mode=principal.mode.value,
        )

        # Ask for confirmation if this tool requires it and we have a
        # confirmer. The confirmer is what the UI wires up as a dialog.
        confirmed = False
        try:
            metadata = self._tools.metadata(call.name)
        except Exception:
            metadata = None

        if metadata is not None and confirmer is not None:
            requires = (
                metadata.requires_confirmation
                or self._tools.requires_confirmation(call.name)
            )
            if requires:
                request = ConfirmationRequest(
                    tool_name=call.name,
                    arguments=dict(call.arguments),
                    capability=metadata.capability.value,
                    risk_level=metadata.risk_level.value,
                    description=_confirmation_description(
                        call.name, call.arguments, metadata,
                    ),
                 )
                
                try:
                    confirmed = await confirmer(request)
                except Exception:
                    logger.exception(
                        "confirmer raised; treating as refusal",
                        extra={"event": "BRAIN_CONFIRMER_FAILED"},
                    )
                    confirmed = False

        try:
            result = await self._tools.invoke(
                call.name,
                call.arguments,
                principal=principal,
                confirmed=confirmed,
            )
        except Exception as exc:
            logger.info(
                "tool call refused or failed",
                extra={
                    "event": "BRAIN_TOOL_FAILED",
                    "tool": call.name,
                    "reason": str(exc),
                },
            )
            return ToolExecutionRecord(
                name=call.name,
                arguments=dict(call.arguments),
                success=False,
                tool_call_id=tool_call_id,
                error=str(exc),
            )
        return ToolExecutionRecord(
            name=call.name,
            arguments=dict(call.arguments),
            success=result.success,
            tool_call_id=tool_call_id,
            output=result.output,
            error=result.error,
        )

    async def _emit(self, name: EventName, **payload: Any) -> None:
        if self._event_bus is None:
            return
        try:
            await self._event_bus.publish(
                Event(name=name, source="brain", payload=payload)
            )
        except Exception:
            logger.exception(
                "failed to emit brain event",
                extra={"event": name.value},
            )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _tool_result_text(record: ToolExecutionRecord) -> str:
    """Plain-text body for a Role.TOOL message (native path)."""
    if record.success:
        output = record.output
        if output is None:
            return "(no output)"
        if isinstance(output, (dict, list)):
            return json.dumps(output, ensure_ascii=False, default=str)
        return str(output)
    return record.error or "unknown error"

def _confirmation_description(
    tool_name: str,
    arguments: dict[str, Any],
    metadata: Any,
) -> str:
    if tool_name == "run_python":
        code = str(arguments.get("code", ""))
        preview = code if len(code) <= 1500 else code[:1500] + "\n... (truncated)"
        return f"Azmuth wants to run this Python code:\n\n{preview}"
    return (
        f"Azmuth wants to run '{tool_name}' "
        f"({metadata.capability.value}, {metadata.risk_level.value} risk)"
    )


_SPOKEN_RE = re.compile(
    r"<spoken>\s*(.*?)\s*</spoken>", re.DOTALL | re.IGNORECASE
)


def _split_spoken_block(text: str) -> tuple[str, str]:
    """Return (display_text, spoken_text).

    If the model followed the <spoken> tag convention, extract the
    spoken version and strip the block from the display text. If the
    tag is missing, spoken_text is '' and the caller falls back.
    """
    if not text:
        return text, ""
    match = _SPOKEN_RE.search(text)
    if not match:
        return text, ""
    spoken = match.group(1).strip()
    display = (text[: match.start()] + text[match.end():]).strip()
    return display, spoken


_MEM_LIST_PATTERNS = [
    re.compile(
        r"\bwhat (?:do you|have you) (?:remember|know|learned|saved)\b"
        r".*\babout (?:me|myself)\b", re.IGNORECASE,
    ),
    re.compile(r"\bwhat have i (?:told|shared with) you\b", re.IGNORECASE),
    re.compile(r"\b(?:show|list|tell me)\s+(?:my|all|everything you)\s+"
               r"(?:memories|memory|facts)\b", re.IGNORECASE),
    re.compile(r"\bwhat (?:are|do)\s+(?:my|your)\s+memories\b",
               re.IGNORECASE),
]

_MEM_FORGET_PATTERN = re.compile(
    r"^\s*(?:please\s+)?"
    r"(?:delete the memory about|delete memory of|"
    r"forget about|forget that|forget)\s+(.+?)[.!?]*\s*$",
    re.IGNORECASE,
)


def _detect_memory_command(text: str) -> tuple[str, str | None]:
    """Return ('list', None) or ('forget', payload) or ('', None)."""
    if not text:
        return "", None
    m = _MEM_FORGET_PATTERN.match(text)
    if m:
        return "forget", m.group(1).strip()
    for pat in _MEM_LIST_PATTERNS:
        if pat.search(text):
            return "list", None
    return "", None


def _format_memory_list(records: list) -> str:
    """Render memory records for a 'what do you remember' reply."""
    if not records:
        return "I don't have any stored memories about you yet."
    lines = [
        f"Here's what I remember about you ({len(records)} item"
        f"{'s' if len(records) != 1 else ''}):"
    ]
    for r in records:
        kind = getattr(r, "kind", None)
        kind_label = (
            f" *{kind.value}*" if hasattr(kind, "value") else ""
        )
        lines.append(f"- {r.content}{kind_label}")
    return "\n".join(lines)


def _fallback_spoken(text: str) -> str:
    """Extract a short spoken version when the model omitted <spoken>.

    Uses the first sentence of the first paragraph. Good enough as a
    safety net — the model usually complies, but this guarantees TTS
    always has something reasonable to speak.
    """
    if not text:
        return ""
    first_para = text.split("\n\n")[0].strip()
    sentences = re.split(r"(?<=[.!?])\s+", first_para)
    if sentences:
        first = sentences[0].strip()
        if len(first) > 240:
            first = first[:237] + "..."
        return first
    return first_para[:240]

def _format_tool_result(record: ToolExecutionRecord) -> str:
    """Marked block for a SYSTEM message (text-parse fallback path)."""
    status = "success" if record.success else "failure"
    lines = [f"[TOOL_RESULT name={record.name} status={status}]"]
    lines.append(_tool_result_text(record))
    lines.append("[/TOOL_RESULT]")
    return "\n".join(lines)


#: Tamil Unicode block. Used to observe the language of the model's reply.
def _language_of(text: str) -> str | None:
    if not text:
        return None
    for ch in text:
        if 0x0B80 <= ord(ch) <= 0x0BFF:
            return "ta"
    for ch in text:
        if "a" <= ch.lower() <= "z":
            return "en"
    return None


__all__ = ["Brain", "BrainResponse", "ConfirmationRequest", "Confirmer", "ToolExecutionRecord"]