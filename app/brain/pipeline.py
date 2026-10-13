
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

import json
from dataclasses import dataclass
from typing import Any

from app.brain.prompts import build_system_prompt
from app.brain.context import RuntimeContext
from app.brain.purpose import detect_purpose
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
        max_tool_iterations: int = 12,
        temperature: float = 0.7,
        memory_top_k: int = 3,
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
        # self._prefer_local: bool = True

    def set_prefer_local(self, value: bool) -> None:
        """Route every subsequent turn to the local backend.

        The router's ``by_purpose(..., local_only=True)`` returns only
        entries marked ``is_local``. Setting this flag bypasses cloud
        tiers entirely — useful when the user wants to conserve cloud
        quota or run fully offline.
        """
        self._prefer_local = value

    def _handle_local_toggle(self, enabled: bool) -> None:
        if self._brain is None:
            return
        self._brain.set_prefer_local(enabled)
        
        
    def attach_identity(self, *, identity_block: str, location_service) -> None:
        self._identity_block = identity_block
        self._location_service = location_service
        
        
    async def respond(
        self,
        user_input: UserInput,
        *,
        conversation_id: str | None = None,
        principal: Principal | None = None,
        confirmer: "Confirmer | None" = None,
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

        # ---- retrieval ---------------------------------------------------
        # Only *unconditional* retrieval remains here. Search is now a tool
        # the model calls on its own; memory recall is the one thing we do
        # proactively because the model cannot query it on demand.
        retrieval_parts: list[str] = []
        used_memory = False

        if principal.user_id:
            memory_block = self._recall_memory(
                user_input.text, user_id=principal.user_id
            )
            if memory_block:
                retrieval_parts.append(memory_block)
                used_memory = True

        retrieval_context = (
            "\n\n".join(retrieval_parts) if retrieval_parts else None
        )

        # ---- build prompt ------------------------------------------------
        tools_for_llm = self._tools.list_metadata(principal=principal)
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

        # ---- LLM / tool loop ---------------------------------------------
        final_text = ""
        tool_records: list[ToolExecutionRecord] = []
        stopped_reason = "final"
        iterations = 0
        used_search = False

        purpose = detect_purpose(user_input.text, language=language_hint)
        last_model = ""
        last_provider = ""
        for iteration in range(1, self._max_tool_iterations + 1):
            iterations = iteration
            response = await self._chat_with_fallback(
                purpose, tuple(messages), tools=tool_definitions
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
        else:
            stopped_reason = "max_iterations"
            tool_names = ", ".join(r.name for r in tool_records) or "none"
            final_text = (
                f"I reached the tool-call limit for this turn "
                f"({self._max_tool_iterations} rounds). "
                f"Tools I tried: {tool_names}. "
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
        session.add_assistant_message(final_text)

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
        )

    # -- provider dispatch -------------------------------------------------
    async def _chat_with_fallback(
        self,
        purpose: ModelPurpose,
        messages: tuple[Message, ...],
        tools: tuple[ToolDefinition, ...] = (),
    ):
        request = ChatRequest(
            messages=messages,
            temperature=self._temperature,
            tools=tools,
        )

        if self._llm_router is None:
            return await self._llm.chat(request)

        last_exception: Exception | None = None
        candidates = self._llm_router.registry.by_purpose(
            purpose, local_only=self._prefer_local
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
                        "event": "BRAIN_ROUTED",
                        "purpose": purpose.value,
                        "model": spec.name,
                        "native_tools": bool(tools),
                    },
                )
                response = await provider.chat(request)
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

    # -- internals ---------------------------------------------------------
    def _recall_memory(self, text: str, *, user_id: str) -> str | None:
        try:
            records = self._memory.recall(
                text, user_id=user_id, top_k=self._memory_top_k
            )
        except Exception:
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
                    description=(
                        f"Azmuth wants to run '{call.name}' "
                        f"({metadata.capability.value}, {metadata.risk_level.value} risk)"
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