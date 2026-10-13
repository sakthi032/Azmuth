
"""Print the exact messages passed to the model across a tool-call turn.

Run:  py -m scripts.debug_search_messages
"""

from __future__ import annotations

import asyncio

from app.brain import build_azmuth
from app.config.settings import load_settings
from app.search.models import SearchResult
from app.voice import InputManager


async def main() -> None:
    from app.llm.base import LLMProvider
    from app.llm.messages import ChatRequest, ChatResponse, ModelInfo
    from collections.abc import AsyncIterator
    from app.llm.messages import StreamChunk

    class Scripted(LLMProvider):
        name = "scripted"
        def __init__(self) -> None:
            super().__init__(model="scripted")
            self.calls: list[ChatRequest] = []
        def is_available(self) -> bool:
            return True
        async def chat(self, request: ChatRequest) -> ChatResponse:
            self.calls.append(request)
            if len(self.calls) == 1:
                return ChatResponse(
                    content=(
                        '```tool\n'
                        '{"name": "search_web", "arguments": {"query": "weather"}}\n'
                        '```'
                    ),
                    model="scripted", provider="scripted",
                )
            return ChatResponse(
                content="It will be sunny.",
                model="scripted", provider="scripted",
            )
        async def stream(self, request):  # noqa: ANN001
            yield StreamChunk(delta="", finish_reason="stop")
        def get_model_info(self) -> ModelInfo:
            return ModelInfo(provider="scripted", model="scripted")

    from app.core.registry import Registry
    from app.search.base import SearchProvider
    from app.search.manager import SearchManager

    class FixedSearch(SearchProvider):
        name = "fixed"
        def is_available(self) -> bool: return True
        async def search(self, query):  # noqa: ANN001
            return [SearchResult(
                title="Weather today",
                url="https://example.com",
                snippet="snippet body",
                source="test",
            )]

    reg: Registry[SearchProvider] = Registry("search")
    reg.register("fixed", FixedSearch)
    search = SearchManager("fixed", reg)

    llm = Scripted()
    azmuth = build_azmuth(
        load_settings(),
        llm_provider=llm,
        search=search,
    )
    inputs = InputManager(azmuth.language)

    await azmuth.respond(inputs.from_text("What's the weather today?"))

    print(f"LLM called {len(llm.calls)} time(s)")
    for i, call in enumerate(llm.calls):
        print()
        print(f"=== Call {i} — {len(call.messages)} message(s) ===")
        for m in call.messages:
            preview = m.content[:120].replace(chr(10), " ")
            print(f"  [{m.role.value:9}] {preview}")
        has_lang = any("## Language" in m.content for m in call.messages)
        print(f"  --> contains '## Language': {has_lang}")

    azmuth.memory.close()


if __name__ == "__main__":
    asyncio.run(main())

# py -m scripts.debug_search_messages