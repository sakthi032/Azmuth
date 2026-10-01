"""Shared pytest fixtures.

Two kinds of fixture live here:

* **Plain fixtures** — a ready object for tests that just want the
  default (`settings`, `event_bus`, `container`, `principal_*`).
* **Factory fixtures** — callables that build a configured object for
  tests that need one variant. Named ``make_*``. Prefer these when a
  test needs one thing different from the default; they make the test
  one line shorter and communicate "what is unusual" at a glance.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from app.config.settings import Settings, load_settings
from app.core.container import Container
from app.core.events import EventBus
from app.core.registry import Registry
from app.llm.base import LLMProvider
from app.llm.messages import (
    ChatRequest,
    ChatResponse,
    ModelInfo,
    StreamChunk,
)
from app.search.base import SearchProvider
from app.search.models import SearchQuery, SearchResult
from app.security.modes import AssuranceLevel, PermissionMode, Principal


# ---------------------------------------------------------------------------
# Paths / settings
# ---------------------------------------------------------------------------
@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    """A Settings object rooted at a throwaway directory."""
    return load_settings(
        env_file=tmp_path / "missing.env",
        environ={
            "AZMUTH_HOME": str(tmp_path),
            "AZMUTH_LOG_LEVEL": "WARNING",
            "AZMUTH_LOG_TO_CONSOLE": "false",
            "AZMUTH_LOG_TO_FILE": "false",
        },
        create_directories=True,
    )


@pytest.fixture()
def make_settings(tmp_path: Path) -> Callable[..., Settings]:
    """Factory: build a Settings with overrides.

    Usage::

        s = make_settings(LLM_PROVIDER="stub", SESSION_MAX_TURNS="5")
    """

    def _make(**overrides: str) -> Settings:
        env = {
            "AZMUTH_HOME": str(tmp_path),
            "AZMUTH_LOG_LEVEL": "WARNING",
            "AZMUTH_LOG_TO_CONSOLE": "false",
            "AZMUTH_LOG_TO_FILE": "false",
        }
        env.update(overrides)
        return load_settings(
            env_file=tmp_path / "missing.env",
            environ=env,
            create_directories=True,
        )

    return _make


# ---------------------------------------------------------------------------
# Core primitives
# ---------------------------------------------------------------------------
@pytest.fixture()
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture()
def container() -> Container:
    return Container()


@pytest.fixture()
def registry() -> Registry[object]:
    return Registry("test")


# ---------------------------------------------------------------------------
# Principals
# ---------------------------------------------------------------------------
@pytest.fixture()
def principal_restricted() -> Principal:
    return Principal.anonymous()


@pytest.fixture()
def principal_creator() -> Principal:
    return Principal(
        user_id="creator",
        mode=PermissionMode.CREATOR,
        assurance=AssuranceLevel.HIGH,
        methods=("pin",),
    )


# ---------------------------------------------------------------------------
# Fakes — LLM
# ---------------------------------------------------------------------------
class ScriptedLLM(LLMProvider):
    """An LLM that returns a fixed list of responses in order."""

    name = "scripted"

    def __init__(self, responses: list[str] | str) -> None:
        super().__init__(model="scripted")
        self._script = [responses] if isinstance(responses, str) else list(responses)
        self.calls: list[ChatRequest] = []

    def is_available(self) -> bool:
        return True

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        text = self._script.pop(0) if self._script else ""
        return ChatResponse(
            content=text, model="scripted", provider="scripted",
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamChunk]:
        response = await self.chat(request)
        yield StreamChunk(delta=response.content, finish_reason="stop")

    def get_model_info(self) -> ModelInfo:
        return ModelInfo(provider="scripted", model="scripted")


@pytest.fixture()
def make_llm() -> Callable[..., ScriptedLLM]:
    """Factory: build a ScriptedLLM with the given responses."""

    def _make(responses: list[str] | str = "ok", **_: Any) -> ScriptedLLM:
        return ScriptedLLM(responses)

    return _make


# ---------------------------------------------------------------------------
# Fakes — search
# ---------------------------------------------------------------------------
class FixedSearch(SearchProvider):
    """A search provider that returns a fixed list of results."""

    name = "fixed"

    def __init__(self, results: list[SearchResult] | None = None) -> None:
        self._results = list(results or [])
        self.queries: list[SearchQuery] = []

    def is_available(self) -> bool:
        return True

    async def search(self, query: SearchQuery) -> list[SearchResult]:
        self.queries.append(query)
        return list(self._results)


@pytest.fixture()
def make_search() -> Callable[..., FixedSearch]:
    """Factory: build a FixedSearch provider and its manager."""

    def _make(results: list[SearchResult] | None = None) -> FixedSearch:
        return FixedSearch(results)

    return _make


# ---------------------------------------------------------------------------
# Fully wired Azmuth
# ---------------------------------------------------------------------------
@pytest.fixture()
def make_azmuth(settings: Settings):
    """Factory: build a fully wired Azmuth object with any subsystem faked.

    Usage::

        azmuth = make_azmuth(responses="hello")
        azmuth = make_azmuth(responses=["a", "b"], search_results=[...])

    Every keyword except ``responses`` and ``search_results`` is passed
    straight to :func:`build_azmuth`, so any subsystem can be overridden
    by name.
    """
    from app.brain import build_azmuth
    from app.core.registry import Registry
    from app.search.manager import SearchManager

    def _make(
        responses: list[str] | str = "ok",
        search_results: list[SearchResult] | None = None,
        **overrides: Any,
    ):
        llm = ScriptedLLM(responses)
        search_provider = FixedSearch(search_results)
        search_registry: Registry[SearchProvider] = Registry("search")
        search_registry.register("fixed", lambda: search_provider)
        search_manager = SearchManager("fixed", search_registry)

        azmuth = build_azmuth(
            settings,
            llm_provider=llm,
            search=search_manager,
            **overrides,
        )
        # Expose the fakes for tests that want to inspect them.
        azmuth._test_llm = llm              # type: ignore[attr-defined]
        azmuth._test_search = search_provider  # type: ignore[attr-defined]
        return azmuth

    return _make