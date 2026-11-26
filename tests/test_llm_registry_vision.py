"""Tests for model registration, vision candidate chain, and deduplication."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch
import httpx
import pytest

from app.automation.vision import describe_image_detailed
from app.config.settings import load_settings
from app.llm.registry import (
    ModelPurpose,
    ModelRegistry,
    ModelSpec,
    build_default_registry,
)
from app.llm.router import LLMRouter


def test_no_duplicate_models_in_default_registry() -> None:
    """Every (provider, name) pair must appear at most once in registry.all()."""
    registry = build_default_registry()
    seen = set()
    for m in registry.all():
        pair = (m.provider, m.name)
        assert pair not in seen, f"Duplicate model registration: {pair}"
        seen.add(pair)

    # Specific check for models that must be present (update when registry changes)
    nemotron = [m for m in registry.all() if m.name == "nvidia/nemotron-3.5-lightning-30b-a3b"]
    assert len(nemotron) == 1, f"Expected 1 nemotron-3.5-lightning, found {len(nemotron)}"

    kimi = [m for m in registry.all() if m.name == "moonshotai/kimi-k3.1-pro"]
    assert len(kimi) == 1, f"Expected 1 kimi-k3.1-pro, found {len(kimi)}"


def test_registry_register_merges_purposes_and_tiers() -> None:
    """Registering the same (provider, name) merges purposes and tracks per-purpose tiers."""
    r = ModelRegistry()
    r.register(ModelSpec(
        name="test-model",
        provider="test-prov",
        purposes=frozenset({ModelPurpose.CHAT}),
        tier=1,
    ))
    r.register(ModelSpec(
        name="test-model",
        provider="test-prov",
        purposes=frozenset({ModelPurpose.CODING}),
        tier=3,
    ))
    assert len(r.all()) == 1
    m = r.all()[0]
    assert m.supports(ModelPurpose.CHAT)
    assert m.supports(ModelPurpose.CODING)

    chat_candidates = r.by_purpose(ModelPurpose.CHAT)
    assert len(chat_candidates) == 1
    assert chat_candidates[0].tier == 1

    coding_candidates = r.by_purpose(ModelPurpose.CODING)
    assert len(coding_candidates) == 1
    assert coding_candidates[0].tier == 3


def test_gemini_registered_when_key_present(monkeypatch) -> None:
    """When GEMINI_API_KEY is present, Gemini models are registered."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    registry = build_default_registry()
    gemini_models = [m for m in registry.all() if m.provider == "gemini"]
    assert len(gemini_models) >= 1
    names = {m.name for m in gemini_models}
    assert "gemini-flash-lite-latest" in names


def test_gemini_registered_with_google_api_key(monkeypatch) -> None:
    """When GOOGLE_API_KEY is present instead of GEMINI_API_KEY, Gemini models are registered."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
    registry = build_default_registry()
    gemini_models = [m for m in registry.all() if m.provider == "gemini"]
    assert len(gemini_models) >= 1
    names = {m.name for m in gemini_models}
    assert "gemini-flash-lite-latest" in names


def test_gemini_not_registered_when_key_absent(monkeypatch, tmp_path) -> None:
    """When no Gemini key is present, Gemini models are omitted from registry."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    # Provide a dummy settings object with no key
    dummy_settings = load_settings(
        env_file=tmp_path / "empty.env",
        environ={"AZMUTH_HOME": str(tmp_path)},
        create_directories=False,
    )
    registry = build_default_registry(dummy_settings)
    gemini_models = [m for m in registry.all() if m.provider == "gemini"]
    assert len(gemini_models) == 0


def test_vision_candidate_chain_order(monkeypatch) -> None:
    """Vision chain order must be: Gemini -> NVIDIA NIM vision -> Ollama vision."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    registry = build_default_registry()
    vision_models = registry.by_purpose(ModelPurpose.VISION)
    assert len(vision_models) >= 3

    providers = [m.provider for m in vision_models]
    # First provider should be gemini
    assert providers[0] == "gemini"
    # Second cloud provider is nvidia_nim
    assert "nvidia_nim" in providers
    assert providers.index("gemini") < providers.index("nvidia_nim")
    # Last fallback is ollama
    assert "ollama" in providers
    assert providers.index("nvidia_nim") < providers.index("ollama")


@pytest.mark.asyncio
async def test_describe_image_detailed_missing_key(monkeypatch) -> None:
    """When key is missing, returns exact 'GEMINI_API_KEY is not set' message."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    with patch("app.config.settings.load_settings") as mock_settings:
        mock_s = mock_settings.return_value
        mock_s.gemini_api_key = None
        ans, err = await describe_image_detailed(b"fake_png", "what is this")
        assert ans == ""
        assert "GEMINI_API_KEY is not set" in err


@pytest.mark.asyncio
async def test_describe_image_detailed_http_error(monkeypatch) -> None:
    """When HTTP error occurs, returns real HTTP status and reason, never leaking key."""
    test_key = "secret-super-key-12345"
    monkeypatch.setenv("GEMINI_API_KEY", test_key)

    mock_resp = httpx.Response(
        status_code=503,
        json={"error": {"code": 503, "message": f"High demand on {test_key}"}},
        request=httpx.Request("POST", "https://example.com"),
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        ans, err = await describe_image_detailed(b"fake_png", "what is this")
        assert ans == ""
        assert "503" in err
        # Assert the secret key was sanitized and never leaked
        assert test_key not in err
        assert "***" in err
