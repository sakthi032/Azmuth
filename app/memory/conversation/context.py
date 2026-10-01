"""Build bounded LLM context from a session.

This is the piece that enforces *"do not continuously send the entire lifetime
conversation to the LLM"*. It:

* prepends the app's system prompt (if any),
* prepends retrieved memory / search context as a *second*, clearly labelled
  system message — never merged with the app's own system prompt, so
  untrusted external content cannot pose as instructions (see STEP 10 and
  STEP 23),
* slices the session to the last ``N`` user turns,
* returns an immutable tuple ready to drop into a :class:`ChatRequest`.

The builder is stateless. Everything it needs is on the session it is given.
"""

from __future__ import annotations

from app.llm.messages import Message, Role
from app.memory.conversation.session import Session

#: Delimiter used to wrap untrusted reference material. Kept ASCII and
#: unambiguous so downstream providers can render it deterministically.
_REFERENCE_LABEL = "[reference]"


class ContextBuilder:
    """Turn a session into the message list the LLM should see."""

    def build(
        self,
        session: Session,
        *,
        system_prompt: str | None = None,
        retrieval_context: str | None = None,
        max_turns: int | None = None,
    ) -> tuple[Message, ...]:
        messages: list[Message] = []

        if system_prompt:
            messages.append(Message(Role.SYSTEM, system_prompt))

        if retrieval_context:
            messages.append(
                Message(
                    Role.SYSTEM,
                    f"{_REFERENCE_LABEL}\n{retrieval_context}",
                )
            )

        messages.extend(session.recent_messages(max_turns))
        return tuple(messages)