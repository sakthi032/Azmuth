from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from app.config.settings import Settings, load_settings
from app.core.errors import LLMError, ProviderNotFoundError, ProviderUnavailableError
from app.core.registry import Registry
from app.llm import (
    ChatRequest,
    LLMProvider,
    LLMManager,
    Message,
    Role,
    build_llm_registry,
)
from app.llm.messages import ChatResponse
from app.llm.providers import OpenAICompatibleProvider, StubLLMProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _request(text: str, *, system: str | None = None) -> ChatRequest:
    messages = []
    if system:
        messages.append(Message(Role.SYSTEM, system))
    messages.append(Message(Role.USER, text))
    return ChatRequest(messages=tuple(messages))


def _run(coro):  # noqa: ANN001 - test helper
    return asyncio.run(coro)


def _settings(tmp_path: Path, **env: str) -> Settings:
    base = {
        "AZMUTH_HOME": str(tmp_path),
        "AZMUTH_LOG_LEVEL": "WARNING",
        "AZMUTH_LOG_TO_CONSOLE": "false",
        "AZMUTH_LOG_TO_FILE": "false",
    }
    base.update(env)
    return load_settings(
        env_file=tmp_path / "missing.env", environ=base, create_directories=False
    )


class _FakeHTTPResponse:
    """Minimal stand-in for the object ``urllib.request.urlopen`` returns."""

    def __init__(self, body: bytes = b"", lines: list[bytes] | None = None) -> None:
        self._body = body
        self._lines = lines or []

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body

    def __iter__(self):  # noqa: ANN204 - matches urllib
        return iter(self._lines)


# ---------------------------------------------------------------------------
# Message / Role
# ---------------------------------------------------------------------------
def test_role_is_string_enum() -> None:
    assert Role.USER == "user"
    assert Role("system") is Role.SYSTEM


def test_message_coerces_string_role() -> None:
    msg = Message(role="user", content="hi")  # type: ignore[arg-type]
    assert msg.role is Role.USER


# ---------------------------------------------------------------------------
# StubLLMProvider
# ---------------------------------------------------------------------------
def test_stub_provider_is_available() -> None:
    provider = StubLLMProvider()
    assert provider.is_available() is True
    assert provider.name == "stub"


def test_stub_provider_defaults_model_when_unset() -> None:
    assert StubLLMProvider().model == "stub-echo-v1"
    assert StubLLMProvider(model="custom").model == "custom"


def test_stub_provider_echoes_last_user_message() -> None:
    provider = StubLLMProvider(model="m1")
    response = _run(provider.chat(_request("hello world", system="be nice")))

    assert isinstance(response, ChatResponse)
    assert response.content == "[stub:m1] hello world"
    assert response.provider == "stub"
    assert response.model == "m1"
    assert response.finish_reason == "stop"
    assert response.usage is not None and response.usage.total_tokens > 0


def test_stub_provider_stream_concatenates_to_chat_output() -> None:
    provider = StubLLMProvider(model="m1")

    async def collect() -> str:
        parts: list[str] = []
        async for chunk in provider.stream(_request("streaming works fine")):
            parts.append(chunk.delta)
        return "".join(parts)

    streamed = _run(collect())
    chatted = _run(provider.chat(_request("streaming works fine"))).content
    assert streamed == chatted


def test_stub_provider_stream_marks_finish() -> None:
    provider = StubLLMProvider()

    async def last_chunk():
        chunk = None
        async for chunk in provider.stream(_request("hi")):
            pass
        return chunk

    final = _run(last_chunk())
    assert final is not None
    assert final.finish_reason == "stop"


def test_stub_provider_model_info() -> None:
    info = StubLLMProvider(model="m").get_model_info()
    assert info.provider == "stub"
    assert info.model == "m"
    assert info.supports_streaming is True


def test_generate_convenience_wraps_prompt_in_request() -> None:
    provider = StubLLMProvider(model="m")
    response = _run(provider.generate("ping", system="sys"))
    assert response.content.endswith("ping")


# ---------------------------------------------------------------------------
# OpenAICompatibleProvider — configuration
# ---------------------------------------------------------------------------
def test_openai_compatible_unavailable_without_base_url() -> None:
    provider = OpenAICompatibleProvider(model="gpt-x", base_url=None)
    assert provider.is_available() is False


def test_openai_compatible_unavailable_without_model() -> None:
    provider = OpenAICompatibleProvider(model=None, base_url="http://x/v1")
    assert provider.is_available() is False


def test_openai_compatible_chat_raises_when_unavailable() -> None:
    provider = OpenAICompatibleProvider(model=None, base_url=None)
    with pytest.raises(ProviderUnavailableError):
        _run(provider.chat(_request("hi")))


def test_openai_compatible_builds_chat_url_without_double_slash() -> None:
    provider = OpenAICompatibleProvider(
        model="m", base_url="https://api.example.com/v1/"
    )
    assert provider._chat_url() == "https://api.example.com/v1/chat/completions"


# ---------------------------------------------------------------------------
# OpenAICompatibleProvider — request/response
# ---------------------------------------------------------------------------
def test_openai_compatible_parses_response(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(req, timeout=None):  # noqa: ANN001
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeHTTPResponse(
            body=json.dumps(
                {
                    "model": "gpt-x",
                    "choices": [
                        {"message": {"content": "hello back"}, "finish_reason": "stop"}
                    ],
                    "usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 4,
                        "total_tokens": 7,
                    },
                }
            ).encode("utf-8")
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    provider = OpenAICompatibleProvider(
        model="gpt-x",
        base_url="https://api.example.com/v1",
        api_key="secret",
        timeout=5.0,
    )
    response = _run(provider.chat(_request("hi")))

    assert response.content == "hello back"
    assert response.model == "gpt-x"
    assert response.provider == "openai_compatible"
    assert response.finish_reason == "stop"
    assert response.usage is not None and response.usage.total_tokens == 7

    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert captured["body"]["model"] == "gpt-x"
    assert captured["body"]["stream"] is False
    assert captured["body"]["messages"] == [{"role": "user", "content": "hi"}]
    assert captured["headers"].get("Authorization") == "Bearer secret"


def test_openai_compatible_forwards_optional_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(req, timeout=None):  # noqa: ANN001
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeHTTPResponse(
            body=json.dumps(
                {
                    "model": "m",
                    "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                }
            ).encode("utf-8")
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    provider = OpenAICompatibleProvider(model="m", base_url="http://x/v1")
    request = ChatRequest(
        messages=(Message(Role.USER, "hi"),),
        temperature=0.2,
        max_tokens=32,
        stop=("END",),
    )
    _run(provider.chat(request))

    assert captured["body"]["temperature"] == 0.2
    assert captured["body"]["max_tokens"] == 32
    assert captured["body"]["stop"] == ["END"]


def test_openai_compatible_rejects_empty_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _FakeHTTPResponse(
            body=json.dumps({"choices": []}).encode("utf-8")
        ),
    )

    provider = OpenAICompatibleProvider(model="m", base_url="http://x/v1")
    with pytest.raises(Exception, match="no choices"):
        _run(provider.chat(_request("hi")))


# ---------------------------------------------------------------------------
# OpenAICompatibleProvider — streaming
# ---------------------------------------------------------------------------
def test_openai_compatible_streams_sse_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def sse(payload: dict[str, Any]) -> bytes:
        return b"data: " + json.dumps(payload).encode("utf-8") + b"\n"

    lines = [
        sse({"choices": [{"delta": {"content": "Hel"}}]}),
        sse({"choices": [{"delta": {"content": "lo"}}]}),
        sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        b"data: [DONE]\n",
    ]

    captured: dict[str, Any] = {}

    def fake_urlopen(req, timeout=None):  # noqa: ANN001
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeHTTPResponse(lines=lines)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    provider = OpenAICompatibleProvider(model="m", base_url="http://x/v1")

    async def collect() -> list[str]:
        return [chunk.delta async for chunk in provider.stream(_request("hi"))]

    deltas = _run(collect())

    assert "".join(deltas) == "Hello"
    assert captured["body"]["stream"] is True


# ---------------------------------------------------------------------------
# LLMManager
# ---------------------------------------------------------------------------
def test_manager_builds_stub_by_default(tmp_path: Path) -> None:
    settings = _settings(tmp_path, LLM_PROVIDER="stub")
    manager = LLMManager(settings, build_llm_registry())

    assert manager.provider_name == "stub"
    assert manager.is_available() is True
    assert manager.provider.name == "stub"


def test_manager_caches_provider_instance(tmp_path: Path) -> None:
    settings = _settings(tmp_path, LLM_PROVIDER="stub")
    manager = LLMManager(settings, build_llm_registry())

    first = manager.provider
    second = manager.provider
    assert first is second

    manager.reset()
    assert manager.provider is not first


def test_manager_unknown_provider_raises(tmp_path: Path) -> None:
    settings = _settings(tmp_path, LLM_PROVIDER="does-not-exist")
    manager = LLMManager(settings, build_llm_registry())

    with pytest.raises(ProviderNotFoundError):
        _ = manager.provider

    # is_available() must swallow the error.
    assert manager.is_available() is False


def test_manager_marks_unavailable_provider(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path, LLM_PROVIDER="openai_compatible", LLM_MODEL="m"
    )  # no base_url → not available
    manager = LLMManager(settings, build_llm_registry())

    assert manager.is_available() is False
    with pytest.raises(ProviderUnavailableError):
        _run(manager.chat(_request("hi")))


def test_manager_generate_forwards_to_provider(tmp_path: Path) -> None:
    settings = _settings(tmp_path, LLM_PROVIDER="stub")
    manager = LLMManager(settings, build_llm_registry())

    response = _run(manager.generate("ping"))
    assert response.content.endswith("ping")


def test_manager_get_model_info(tmp_path: Path) -> None:
    settings = _settings(tmp_path, LLM_PROVIDER="stub", LLM_MODEL="custom")
    manager = LLMManager(settings, build_llm_registry())

    info = manager.get_model_info()
    assert info.provider == "stub"
    assert info.model == "custom"


def test_manager_uses_registry_it_is_given(tmp_path: Path) -> None:
    class MyProvider(StubLLMProvider):
        name = "custom-stub"

    registry: Registry[LLMProvider] = Registry("LLM")
    registry.register("custom-stub", MyProvider)

    settings = _settings(tmp_path, LLM_PROVIDER="custom-stub")
    manager = LLMManager(settings, registry)
    assert manager.provider.name == "custom-stub"


def test_manager_wraps_provider_failure_as_llm_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, LLM_PROVIDER="stub")
    manager = LLMManager(settings, build_llm_registry())

    async def boom(_request: ChatRequest) -> ChatResponse:
        raise RuntimeError("network down")

    monkeypatch.setattr(manager.provider, "chat", boom)

    with pytest.raises(LLMError, match="network down"):
        _run(manager.chat(_request("hi")))