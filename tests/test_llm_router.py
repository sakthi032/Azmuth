"""Tests for the multi-model router."""

from __future__ import annotations

import pytest

from app.config.settings import Settings, load_settings
from app.core.errors import ProviderNotFoundError, ProviderUnavailableError
from app.llm.base import LLMProvider
from app.llm.messages import (
    ChatRequest, ChatResponse, ModelInfo, StreamChunk, Message, Role,
)
from app.llm.registry import (
    ModelPurpose,
    ModelRegistry,
    ModelSpec,
    build_default_registry,
)
from app.llm.router import LLMRouter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def settings(tmp_path) -> Settings:
    return load_settings(
        env_file=tmp_path / "missing.env",
        environ={
            "AZMUTH_HOME": str(tmp_path),
            "AZMUTH_LOG_LEVEL": "WARNING",
            "AZMUTH_LOG_TO_CONSOLE": "false",
            "AZMUTH_LOG_TO_FILE": "false",
            "OLLAMA_BASE_URL": "http://localhost:11434/v1",
            "NVIDIA_API_KEY": "nvapi-test-key",
        },
        create_directories=False,
    )


class FakeProvider(LLMProvider):
    """A provider that is available or not, at the caller's discretion."""

    def __init__(self, *, available: bool = True, **kwargs) -> None:  # noqa: ANN003
        super().__init__(**kwargs)
        self._available = available

    def is_available(self) -> bool:
        return self._available

    async def chat(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            content=f"reply from {self._model}",
            model=self._model,
            provider="fake",
        )

    async def stream(self, request):  # noqa: ANN001
        yield StreamChunk(delta="x", finish_reason="stop")

    def get_model_info(self) -> ModelInfo:
        return ModelInfo(provider="fake", model=self._model)


# ---------------------------------------------------------------------------
# ModelRegistry
# ---------------------------------------------------------------------------
def test_registry_register_and_query() -> None:
    r = ModelRegistry()
    r.register(ModelSpec(name="a", provider="x", purposes=frozenset({ModelPurpose.CHAT})))
    r.register(ModelSpec(name="b", provider="y", purposes=frozenset({ModelPurpose.CODING})))
    assert len(r) == 2
    assert [m.name for m in r.by_purpose(ModelPurpose.CHAT)] == ["a"]
    assert [m.name for m in r.by_purpose(ModelPurpose.CODING)] == ["b"]
    assert r.by_name("a") is not None
    assert r.by_name("z") is None
    assert set(r.providers()) == {"x", "y"}


def test_registry_orders_by_tier_then_local() -> None:
    r = ModelRegistry()
    r.register(ModelSpec(name="remote", provider="x", purposes=frozenset({ModelPurpose.CHAT}),
                         is_local=False, tier=1))
    r.register(ModelSpec(name="local", provider="x", purposes=frozenset({ModelPurpose.CHAT}),
                         is_local=True, tier=1))
    r.register(ModelSpec(name="tier2", provider="x", purposes=frozenset({ModelPurpose.CHAT}),
                         is_local=True, tier=2))
    ordered = [m.name for m in r.by_purpose(ModelPurpose.CHAT)]
    assert ordered == ["local", "remote", "tier2"]


def test_registry_local_only_filter() -> None:
    r = ModelRegistry()
    r.register(ModelSpec(name="local", provider="x",
                         purposes=frozenset({ModelPurpose.CHAT}), is_local=True))
    r.register(ModelSpec(name="remote", provider="y",
                         purposes=frozenset({ModelPurpose.CHAT}), is_local=False))
    local = r.by_purpose(ModelPurpose.CHAT, local_only=True)
    assert [m.name for m in local] == ["local"]


def test_default_registry_contains_verified_models() -> None:
    """The registry lists only models confirmed working on NVIDIA NIM.

    These are the seven IDs that came back OK from
    scripts/nvidia_availability. If a model is retired on NVIDIA's side,
    re-run that script and update both the registry and this test.
    """
    r = build_default_registry()
    names = {m.name for m in r.all()}
    assert "nvidia/nemotron-3-super-120b-a12b" in names
    assert "nvidia/nemotron-3-ultra-550b-a55b" in names
    assert "nvidia/nemotron-3.5-lightning-30b-a3b" in names
    assert "openai/gpt-oss-20b" in names
    assert "z-ai/glm-5.3" in names
    assert "z-ai/glm-5.3-flash" in names
    assert "deepseek-ai/deepseek-v4.1-flash" in names


def test_default_registry_coding_prefers_deepseek() -> None:
    r = build_default_registry()
    coders = r.by_purpose(ModelPurpose.CODING)
    # DeepSeek V4.1 Flash is tier-1 for coding in the cloud-only setup.
    assert coders[0].name == "deepseek-ai/deepseek-v4.1-flash"
    assert coders[0].is_local is False
    assert coders[0].tier == 1


def test_default_registry_reasoning_prefers_nemotron_ultra() -> None:
    r = build_default_registry()
    reasoners = r.by_purpose(ModelPurpose.REASONING)
    # Nemotron Ultra 550B is tier-1 for reasoning in the cloud-only setup.
    assert reasoners[0].name == "nvidia/nemotron-3-ultra-550b-a55b"
    assert reasoners[0].is_local is False
    assert reasoners[0].tier == 1


# ---------------------------------------------------------------------------
# LLMRouter
# ---------------------------------------------------------------------------
def test_router_selects_provider_for_purpose(settings: Settings) -> None:
    r = ModelRegistry()
    r.register(ModelSpec(name="coder", provider="ollama",
                         purposes=frozenset({ModelPurpose.CODING})))
    r.register(ModelSpec(name="chat", provider="ollama",
                         purposes=frozenset({ModelPurpose.CHAT})))

    router = LLMRouter(
        settings, r,
        provider_classes={"ollama": lambda **kw: FakeProvider(**kw)},
    )

    coding_provider = router.for_purpose(ModelPurpose.CODING)
    chat_provider = router.for_purpose(ModelPurpose.CHAT)
    assert coding_provider.model == "coder"
    assert chat_provider.model == "chat"


def test_router_skips_unavailable_provider(settings: Settings) -> None:
    r = ModelRegistry()
    r.register(ModelSpec(name="down", provider="ollama",
                         purposes=frozenset({ModelPurpose.CHAT}), tier=1))
    r.register(ModelSpec(name="up", provider="ollama",
                         purposes=frozenset({ModelPurpose.CHAT}), tier=2))

    def make(**kw):  # noqa: ANN003
        return FakeProvider(available=(kw["model"] == "up"), **kw)

    router = LLMRouter(settings, r, provider_classes={"ollama": make})
    assert router.for_purpose(ModelPurpose.CHAT).model == "up"


def test_router_raises_when_nothing_available(settings: Settings) -> None:
    r = ModelRegistry()
    r.register(ModelSpec(name="down", provider="ollama",
                         purposes=frozenset({ModelPurpose.CHAT})))
    router = LLMRouter(
        settings, r,
        provider_classes={"ollama": lambda **kw: FakeProvider(available=False, **kw)},
    )
    with pytest.raises(ProviderUnavailableError, match="no available provider"):
        router.for_purpose(ModelPurpose.CHAT)


def test_router_raises_when_no_model_registered(settings: Settings) -> None:
    router = LLMRouter(settings, ModelRegistry())
    with pytest.raises(ProviderUnavailableError, match="no model registered"):
        router.for_purpose(ModelPurpose.CHAT)


def test_router_for_model_by_name(settings: Settings) -> None:
    r = ModelRegistry()
    r.register(ModelSpec(name="exact", provider="ollama",
                         purposes=frozenset({ModelPurpose.CHAT})))
    router = LLMRouter(
        settings, r,
        provider_classes={"ollama": lambda **kw: FakeProvider(**kw)},
    )
    assert router.for_model("exact").model == "exact"


def test_router_for_model_unknown_name(settings: Settings) -> None:
    router = LLMRouter(settings, ModelRegistry())
    with pytest.raises(ProviderNotFoundError):
        router.for_model("nope")


def test_router_caches_provider_per_model(settings: Settings) -> None:
    r = ModelRegistry()
    r.register(ModelSpec(name="m", provider="ollama",
                         purposes=frozenset({ModelPurpose.CHAT})))
    calls = {"n": 0}

    def make(**kw):  # noqa: ANN003
        calls["n"] += 1
        return FakeProvider(**kw)

    router = LLMRouter(settings, r, provider_classes={"ollama": make})
    router.for_purpose(ModelPurpose.CHAT)
    router.for_purpose(ModelPurpose.CHAT)
    router.for_model("m")
    assert calls["n"] == 1


def test_router_describe_routing(settings: Settings) -> None:
    r = ModelRegistry()
    r.register(ModelSpec(name="c", provider="ollama",
                         purposes=frozenset({ModelPurpose.CHAT})))
    router = LLMRouter(
        settings, r,
        provider_classes={"ollama": lambda **kw: FakeProvider(**kw)},
    )
    table = router.describe_routing()
    assert any(row["purpose"] == "chat" and row["selected"] == "c" for row in table)
    # A purpose with no model registered reports no selection.
    assert any(row["purpose"] == "coding" and row["selected"] is None for row in table)


def test_router_local_only_mode(settings: Settings) -> None:
    r = ModelRegistry()
    r.register(ModelSpec(name="local", provider="ollama", is_local=True,
                         purposes=frozenset({ModelPurpose.CHAT})))
    r.register(ModelSpec(name="remote", provider="nvidia_nim", is_local=False,
                         purposes=frozenset({ModelPurpose.CHAT})))
    router = LLMRouter(
        settings, r,
        provider_classes={
            "ollama": lambda **kw: FakeProvider(**kw),
            "nvidia_nim": lambda **kw: FakeProvider(**kw),
        },
    )
    assert router.for_purpose(ModelPurpose.CHAT, local_only=True).model == "local"
    # Without local_only, still local because tier order puts local first.
    assert router.for_purpose(ModelPurpose.CHAT).model == "local"


def test_router_builds_ollama_with_settings_base_url(settings: Settings) -> None:
    """The router reads the Ollama base URL from settings, not a constant."""
    r = ModelRegistry()
    r.register(ModelSpec(name="m", provider="ollama",
                         purposes=frozenset({ModelPurpose.CHAT})))

    captured: dict = {}

    def make(**kw):  # noqa: ANN003
        captured.update(kw)
        return FakeProvider(**kw)

    router = LLMRouter(settings, r, provider_classes={"ollama": make})
    router.for_purpose(ModelPurpose.CHAT)
    assert captured["base_url"] == settings.ollama_base_url


def test_router_builds_nvidia_with_api_key_from_settings(settings: Settings) -> None:
    r = ModelRegistry()
    r.register(ModelSpec(name="m", provider="nvidia_nim",
                         purposes=frozenset({ModelPurpose.CHAT})))

    captured: dict = {}

    def make(**kw):  # noqa: ANN003
        captured.update(kw)
        return FakeProvider(**kw)

    router = LLMRouter(settings, r, provider_classes={"nvidia_nim": make})
    router.for_purpose(ModelPurpose.CHAT)
    assert captured["api_key"] == "nvapi-test-key"
    assert captured["base_url"] == settings.nvidia_base_url
    
    
@pytest.mark.parametrize(
    "purpose",
    [
        ModelPurpose.CHAT,
        ModelPurpose.CODING,
        ModelPurpose.REASONING,
        ModelPurpose.FAST,
    ],
)
def test_every_purpose_has_at_least_two_candidates(
    purpose: ModelPurpose,
) -> None:
    """The fallback chain needs somewhere to go.

    With only one candidate, a retired model or a rate limit takes down
    the whole purpose. Two or more candidates means the pipeline can
    always fall through.
    """
    r = build_default_registry()
    candidates = r.by_purpose(purpose)
    assert len(candidates) >= 2, (
        f"purpose {purpose.value!r} has only {len(candidates)} candidate(s): "
        f"{[c.name for c in candidates]}"
    )