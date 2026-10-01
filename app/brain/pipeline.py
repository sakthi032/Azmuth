
"""The turn orchestrator.

Takes one :class:`UserInput`, drives the full pipeline, and returns a
:class:`BrainResponse`. The orchestration is deliberately linear and
explicit - every step is a call to one subsystem, and the pipeline holds
no subsystem state of its own. Everything is injected via ``__init__``,
which is what makes the whole thing testable with fakes.

Security invariants enforced here:

* Retrieval context (memory, search) always enters the LLM prompt as a
  *separate* system message - never merged with the app's system prompt.
* Memory recall is scoped to the authenticated principal.
* Tool invocation goes through :class:`~app.tools.manager.ToolManager`.
* Tool results are treated as untrusted and marked with a fixed block.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.brain.prompts import build_system_prompt
from app.brain.purpose import detect_purpose
from app.brain.tool_calls import ToolCall, parse_tool_calls
from app.config.logging_setup import get_logger
from app.core.errors import ProviderUnavailableError
from app.core.events import Event, EventBus, EventName
from app.llm.base import LLMProvider
from app.llm.messages import ChatRequest, Message, Role
from app.llm.registry import ModelPurpose
from app.llm.router import LLMRouter
from app.memory.conversation.context import ContextBuilder
from app.memory.conversation.manager import SessionManager
from app.memory.manager import MemoryManager
from app.search.manager import SearchManager
from app.search.models import SearchQuery
from app.search.needs import needs_search
from app.security.modes import Principal
from app.tools.manager import ToolManager
from app.voice.input_manager import UserInput
from app.brain.context import LocationService, RuntimeContext
logger = get_logger("brain.pipeline")


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ToolExecutionRecord:
    name: str
    arguments: dict[str, Any]
    success: bool
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
    text: str
    session_id: str
    language: str
    purpose: str = "chat"
    tool_calls: tuple[ToolExecutionRecord, ...] = ()
    used_search: bool = False
    used_memory: bool = False
    iterations: int = 0
    stopped_reason: str = "final"

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


# --------------------------------------------------------------------------
# The brain
# --------------------------------------------------------------------------
class Brain:
    """Turn a :class:`UserInput` into a :class:`BrainResponse`."""

    def __init__(
        self,
        *,
        llm: LLMProvider,
        memory: MemoryManager,
        sessions: SessionManager,
        language,  # LanguageManager - not typed to avoid a cycle
        tools: ToolManager,
        search: SearchManager,
        event_bus: EventBus | None = None,
        llm_router: LLMRouter | None = None,
        max_tool_iterations: int = 5,
        temperature: float = 0.7,
        memory_top_k: int = 3,
    ) -> None:
        self._llm = llm
        self._llm_router = llm_router
        self._location_service: LocationService | None = None
        self._identity_block: str = ""
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

    # -- public API --------------------------------------------------------
    async def respond(
        self,
        user_input: UserInput,
        *,
        conversation_id: str | None = None,
        principal: Principal | None = None,
    ) -> BrainResponse:
        principal = principal or Principal.anonymous()

        # ---- session -----------------------------------------------------
        session = self._sessions.get_or_create(
            conversation_id, language=user_input.language
        )
        resolved_language = self._language.resolve_response_language(
            user_input.text, session_language=session.language
        )
        session.language = resolved_language
        session.add_user_message(user_input.text)

        await self._emit(
            EventName.USER_INPUT,
            text_length=len(user_input.text),
            source=user_input.source.value,
            language=resolved_language,
        )

        # ---- retrieval ---------------------------------------------------
        retrieval_parts: list[str] = []
        used_memory = False
        used_search = False

        if principal.user_id:
            memory_block = self._recall_memory(
                user_input.text, user_id=principal.user_id
            )
            if memory_block:
                retrieval_parts.append(memory_block)
                used_memory = True

        if needs_search(user_input.text, session_language=resolved_language):
            search_block = await self._run_search(
                user_input.text, language=resolved_language
            )
            if search_block:
                retrieval_parts.append(search_block)
                used_search = True

        retrieval_context = (
            "\n\n".join(retrieval_parts) if retrieval_parts else None
        )

        # ---- initial messages --------------------------------------------
        # ---- initial messages --------------------------------------------
        tools_for_llm = self._tools.list_metadata(principal=principal)

        # Build the runtime context fresh per turn so the date/time are
        # always current, and include location if we have it.
        location = None
        if self._location_service is not None:
            location = self._location_service.location
        runtime_context = RuntimeContext(location=location)

        system_prompt = build_system_prompt(
            language=resolved_language,
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

        # ---- LLM / tool loop ---------------------------------------------
        final_text = ""
        tool_records: list[ToolExecutionRecord] = []
        stopped_reason = "final"
        iterations = 0

        # The purpose is detected from the user's original message and does
        # not change between tool iterations - that would let a tool result
        # re-route the turn.
        purpose = detect_purpose(user_input.text, language=resolved_language)

        for iteration in range(1, self._max_tool_iterations + 1):
            iterations = iteration
            response = await self._chat_with_fallback(
                purpose, tuple(messages)
            )
            raw_text = response.content or ""
            calls, cleaned = parse_tool_calls(raw_text)

            if not calls:
                final_text = cleaned or raw_text.strip()
                stopped_reason = "final"
                break

            # Preserve the assistant's tool-calling output so the model
            # sees what it said when we hand the results back.
            messages.append(Message(Role.ASSISTANT, raw_text))

            for call in calls:
                record = await self._execute_tool(call, principal=principal)
                tool_records.append(record)
                messages.append(
                    Message(Role.SYSTEM, _format_tool_result(record))
                )
        else:
            stopped_reason = "max_iterations"
            final_text = (
                "I reached the tool-call limit for this turn. "
                "Let me know what you'd like me to try next."
            )
            logger.warning(
                "tool loop hit max iterations",
                extra={
                    "event": "BRAIN_MAX_ITERATIONS",
                    "session_id": session.id,
                },
            )

        session.add_assistant_message(final_text)

        return BrainResponse(
            text=final_text,
            session_id=session.id,
            language=resolved_language,
            purpose=purpose.value,
            tool_calls=tuple(tool_records),
            used_search=used_search,
            used_memory=used_memory,
            iterations=iterations,
            stopped_reason=stopped_reason,
        )

    # -- provider selection & fallback -------------------------------------
    async def _chat_with_fallback(
        self, purpose: ModelPurpose, messages: tuple[Message, ...]
    ):
        """Try each provider registered for ``purpose``, in tier order.

        Falls back to the single injected provider (Phase 1 behaviour)
        when no router is configured. Raises the last error if every
        candidate fails.
        """
        request = ChatRequest(
            messages=messages, temperature=self._temperature
        )

        if self._llm_router is None:
            return await self._llm.chat(request)

        last_exception: Exception | None = None
        for spec in self._llm_router.registry.by_purpose(purpose):
            try:
                provider = self._llm_router.for_model(spec.name)
            except Exception as exc:  # noqa: BLE001 - provider build failed
                logger.warning(
                    "provider build failed, trying next",
                    extra={
                        "event": "BRAIN_PROVIDER_BUILD_FAILED",
                        "model": spec.name,
                        "reason": str(exc),
                    },
                )
                last_exception = exc
                continue

            try:
                logger.info(
                    "routing turn",
                    extra={
                        "event": "BRAIN_ROUTED",
                        "purpose": purpose.value,
                        "model": spec.name,
                        "provider": spec.provider,
                    },
                )
                return await provider.chat(request)
            except Exception as exc:  # noqa: BLE001 - provider failed
                logger.warning(
                    "provider failed, trying next",
                    extra={
                        "event": "BRAIN_PROVIDER_FALLBACK",
                        "failed_model": spec.name,
                        "reason": str(exc),
                    },
                )
                last_exception = exc
                continue

        # Nothing in the routing table succeeded. Try the default provider
        # one last time - it may be the stub, or a Phase 1 single provider
        # that still works.
        try:
            logger.warning(
                "all routed providers failed; falling back to default",
                extra={
                    "event": "BRAIN_DEFAULT_FALLBACK",
                    "purpose": purpose.value,
                },
            )
            return await self._llm.chat(request)
        except Exception as exc:  # noqa: BLE001
            if last_exception is not None:
                raise ProviderUnavailableError(
                    f"all providers failed for purpose {purpose.value!r}; "
                    f"last routed error: {last_exception}"
                ) from last_exception
            raise ProviderUnavailableError(
                f"all providers failed for purpose {purpose.value!r}"
            ) from exc

    # -- internals ---------------------------------------------------------
    def _recall_memory(self, text: str, *, user_id: str) -> str | None:
        try:
            records = self._memory.recall(
                text, user_id=user_id, top_k=self._memory_top_k
            )
        except Exception:  # noqa: BLE001 - recall must never break a turn
            logger.exception(
                "memory recall failed",
                extra={"event": "BRAIN_MEMORY_RECALL_FAILED"},
            )
            return None
        if not records:
            return None
        lines = ["[MEMORY]"]
        for record in records:
            lines.append(f"- ({record.kind.value}) {record.content}")
        return "\n".join(lines)

    async def _run_search(self, text: str, *, language: str) -> str | None:
        try:
            response = await self._search.search(
                SearchQuery(text=text, language=language)
            )
        except Exception:  # noqa: BLE001 - search failure is not fatal
            logger.exception(
                "search failed",
                extra={"event": "BRAIN_SEARCH_FAILED"},
            )
            return None
        if not response.has_reference:
            return None
        return response.reference_block

    async def _execute_tool(
        self, call: ToolCall, *, principal: Principal
    ) -> ToolExecutionRecord:
        await self._emit(
            EventName.TOOL_STARTED,
            tool=call.name,
            capability="(pipeline)",
            mode=principal.mode.value,
        )
        try:
            result = await self._tools.invoke(
                call.name, call.arguments, principal=principal
            )
        except Exception as exc:  # noqa: BLE001 - tool failure is contained
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
                error=str(exc),
            )
        return ToolExecutionRecord(
            name=call.name,
            arguments=dict(call.arguments),
            success=result.success,
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
        except Exception:  # noqa: BLE001
            logger.exception(
                "failed to emit brain event",
                extra={"event": name.value},
            )
    def attach_identity(self, *, identity_block: str, location_service: LocationService) -> None:
        """Provide the identity block and location service.

        Called by the Azmuth factory after construction so tests can
        build a Brain without identity files.
        """
        self._identity_block = identity_block
        self._location_service = location_service

# --------------------------------------------------------------------------
# Tool-result formatting
# --------------------------------------------------------------------------
def _format_tool_result(record: ToolExecutionRecord) -> str:
    status = "success" if record.success else "failure"
    lines = [f"[TOOL_RESULT name={record.name} status={status}]"]
    if record.success:
        output = record.output
        if output is None:
            lines.append("(no output)")
        elif isinstance(output, (dict, list)):
            lines.append(json.dumps(output, ensure_ascii=False, default=str))
        else:
            text = str(output)
            lines.append(text if text else "(empty)")
    else:
        lines.append(record.error or "unknown error")
    lines.append("[/TOOL_RESULT]")
    return "\n".join(lines)


__all__ = ["Brain", "BrainResponse", "ToolExecutionRecord"]
