from __future__ import annotations

import asyncio

import pytest

from app.config.settings import Settings, load_settings
from app.core.errors import ProviderNotFoundError
from app.core.registry import Registry
from app.voice import (
    AudioFrame,
    NullTextToSpeechProvider,
    SpeechOutcome,
    SynthesisResult,
    TTSManager,
    TextToSpeechProvider,
    VoiceGender,
    VoiceProfile,
    build_tts_manager,
    build_tts_registry,
)


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
        },
        create_directories=False,
    )


def _audio() -> AudioFrame:
    return AudioFrame(data=b"\x00" * 3_200, sample_rate=16_000)


class FakeTTS(TextToSpeechProvider):
    """A provider that plays back the strings it was given."""

    name = "fake"

    def __init__(
        self,
        *,
        available: bool = True,
        voices: tuple[VoiceProfile, ...] = (),
        languages: tuple[str, ...] = ("en", "ta"),
        speak_result: bool = True,
        raise_on_speak: bool = False,
        raise_on_synthesize: bool = False,
    ) -> None:
        self._available = available
        self._voices = voices
        self._languages = languages
        self._speak_result = speak_result
        self._raise_on_speak = raise_on_speak
        self._raise_on_synthesize = raise_on_synthesize
        self.spoken: list[tuple[str, str | None, str | None]] = []
        self.synthesized: list[tuple[str, str | None, str | None]] = []

    def is_available(self) -> bool:
        return self._available

    async def synthesize(
        self,
        text: str,
        *,
        language: str | None = None,
        voice_id: str | None = None,
    ) -> SynthesisResult:
        self.synthesized.append((text, language, voice_id))
        if self._raise_on_synthesize:
            raise RuntimeError("synthesize exploded")
        return SynthesisResult(
            text=text,
            audio=_audio(),
            provider=self.name,
            language=language,
            voice_id=voice_id,
            elapsed_ms=1.0,
        )

    async def speak(
        self,
        text: str,
        *,
        language: str | None = None,
        voice_id: str | None = None,
    ) -> bool:
        self.spoken.append((text, language, voice_id))
        if self._raise_on_speak:
            raise RuntimeError("speak exploded")
        return self._speak_result

    def list_voices(self, language: str | None = None) -> tuple[VoiceProfile, ...]:
        if language is None:
            return self._voices
        return tuple(v for v in self._voices if v.language == language)

    def supports_language(self, language: str) -> bool:
        return language in self._languages


def _manager(provider: TextToSpeechProvider, *, name: str = "fake") -> TTSManager:
    registry: Registry[TextToSpeechProvider] = Registry("TTS")
    registry.register(name, lambda: provider)
    return TTSManager(name, registry)


# ---------------------------------------------------------------------------
# VoiceProfile
# ---------------------------------------------------------------------------
def test_voice_profile_as_dict_is_json_ready() -> None:
    import json
    profile = VoiceProfile(
        id="azmuth-custom",
        name="Azmuth",
        language="en",
        gender=VoiceGender.NEUTRAL,
        is_custom=True,
    )
    payload = json.loads(json.dumps(profile.as_dict()))
    assert payload["id"] == "azmuth-custom"
    assert payload["is_custom"] is True
    assert payload["gender"] == "neutral"


# ---------------------------------------------------------------------------
# NullTextToSpeechProvider
# ---------------------------------------------------------------------------
def test_null_provider_is_unavailable() -> None:
    provider = NullTextToSpeechProvider()
    assert provider.is_available() is False
    assert provider.name == "null"


def test_null_provider_synthesize_returns_error() -> None:
    provider = NullTextToSpeechProvider()
    result = asyncio.run(provider.synthesize("hi"))
    assert result.succeeded is False
    assert result.error is not None
    assert result.text == "hi"  # text preserved even in failure


def test_null_provider_speak_returns_false() -> None:
    provider = NullTextToSpeechProvider()
    assert asyncio.run(provider.speak("hi")) is False


def test_null_provider_lists_no_voices_and_supports_no_language() -> None:
    provider = NullTextToSpeechProvider()
    assert provider.list_voices() == ()
    assert provider.supports_language("en") is False
    assert provider.supports_language("ta") is False


# ---------------------------------------------------------------------------
# TTSManager — resolution
# ---------------------------------------------------------------------------
def test_manager_resolves_null_by_default(settings: Settings) -> None:
    manager = build_tts_manager(settings)
    assert manager.provider_name == "null"
    assert manager.is_available() is False


def test_manager_unknown_provider_raises_on_access() -> None:
    registry: Registry[TextToSpeechProvider] = Registry("TTS")
    manager = TTSManager("does-not-exist", registry)
    with pytest.raises(ProviderNotFoundError):
        _ = manager.provider


def test_manager_unknown_provider_is_unavailable(settings: Settings) -> None:
    registry: Registry[TextToSpeechProvider] = Registry("TTS")
    manager = TTSManager("does-not-exist", registry)
    assert manager.is_available() is False


def test_manager_caches_provider() -> None:
    provider = FakeTTS()
    manager = _manager(provider)
    assert manager.provider is provider
    assert manager.provider is provider  # cached
    manager.reset()
    assert manager.provider is provider  # lambda rebuilds from same instance


def test_manager_uses_registry_it_is_given() -> None:
    custom = FakeTTS()
    registry: Registry[TextToSpeechProvider] = Registry("TTS")
    registry.register("custom", lambda: custom)

    manager = TTSManager("custom", registry)
    assert manager.provider is custom


# ---------------------------------------------------------------------------
# TTSManager — speak_response
# ---------------------------------------------------------------------------
def test_speak_response_success() -> None:
    provider = FakeTTS()
    manager = _manager(provider)

    outcome = asyncio.run(
        manager.speak_response("hello world", language="en", voice_id="v1")
    )

    assert isinstance(outcome, SpeechOutcome)
    assert outcome.spoken is True
    assert outcome.text == "hello world"
    assert outcome.error is None
    assert outcome.is_silent is False
    assert provider.spoken == [("hello world", "en", "v1")]


def test_speak_response_returns_false_from_provider_becomes_silent() -> None:
    provider = FakeTTS(speak_result=False)
    manager = _manager(provider)

    outcome = asyncio.run(manager.speak_response("hello"))

    assert outcome.spoken is False
    assert outcome.text == "hello"
    assert outcome.error is not None
    assert outcome.is_silent is True


def test_speak_response_never_raises_when_provider_raises() -> None:
    """The whole point of the invariant."""
    provider = FakeTTS(raise_on_speak=True)
    manager = _manager(provider)

    outcome = asyncio.run(manager.speak_response("must survive"))

    assert outcome.text == "must survive"
    assert outcome.spoken is False
    assert outcome.error is not None and "speak exploded" in outcome.error
    assert outcome.is_silent is True


def test_speak_response_never_raises_when_provider_unavailable() -> None:
    manager = build_tts_manager(load_settings_stub := _settings_stub())
    del load_settings_stub
    outcome = asyncio.run(manager.speak_response("keep me"))
    assert outcome.text == "keep me"
    assert outcome.spoken is False
    assert outcome.error is not None


def test_speak_response_unavailable_provider_is_reported() -> None:
    provider = FakeTTS(available=False)
    manager = _manager(provider)
    outcome = asyncio.run(manager.speak_response("hi"))
    assert outcome.spoken is False
    assert outcome.text == "hi"
    assert outcome.error is not None


def test_speak_response_empty_text_does_not_call_provider() -> None:
    provider = FakeTTS()
    manager = _manager(provider)
    outcome = asyncio.run(manager.speak_response("   "))
    assert outcome.spoken is False
    assert outcome.text == "   "
    assert provider.spoken == []


def test_speak_response_records_elapsed_time() -> None:
    provider = FakeTTS()
    manager = _manager(provider)
    outcome = asyncio.run(manager.speak_response("timed"))
    assert outcome.elapsed_ms >= 0


def test_speak_response_outcome_as_dict_preserves_text() -> None:
    provider = FakeTTS()
    manager = _manager(provider)
    outcome = asyncio.run(manager.speak_response("hello"))
    payload = outcome.as_dict()
    assert payload["text"] == "hello"
    assert payload["spoken"] is True


# ---------------------------------------------------------------------------
# TTSManager — synthesize
# ---------------------------------------------------------------------------
def test_synthesize_success() -> None:
    provider = FakeTTS()
    manager = _manager(provider)

    result = asyncio.run(
        manager.synthesize("hello", language="en", voice_id="v1")
    )

    assert isinstance(result, SynthesisResult)
    assert result.succeeded is True
    assert result.audio is not None
    assert result.text == "hello"
    assert provider.synthesized == [("hello", "en", "v1")]


def test_synthesize_provider_raises_returns_empty_result() -> None:
    provider = FakeTTS(raise_on_synthesize=True)
    manager = _manager(provider)

    result = asyncio.run(manager.synthesize("keep the text"))

    assert result.succeeded is False
    assert result.text == "keep the text"  # invariant
    assert result.error is not None and "synthesize exploded" in result.error


def test_synthesize_unavailable_provider_returns_empty_result() -> None:
    provider = FakeTTS(available=False)
    manager = _manager(provider)
    result = asyncio.run(manager.synthesize("hi"))
    assert result.succeeded is False
    assert result.text == "hi"


def test_synthesize_empty_text_does_not_call_provider() -> None:
    provider = FakeTTS()
    manager = _manager(provider)
    result = asyncio.run(manager.synthesize(""))
    assert result.succeeded is False
    assert provider.synthesized == []


def test_synthesize_overrides_provider_text_if_mismatched() -> None:
    """A buggy provider cannot change the invariant that result.text == input."""

    class BadTTS(FakeTTS):
        async def synthesize(self, text, *, language=None, voice_id=None):  # noqa: ANN001
            return SynthesisResult(
                text="WRONG TEXT",
                audio=_audio(),
                provider=self.name,
                language=language,
                voice_id=voice_id,
            )

    manager = _manager(BadTTS())
    result = asyncio.run(manager.synthesize("the real text"))
    assert result.text == "the real text"


# ---------------------------------------------------------------------------
# TTSManager — voice discovery
# ---------------------------------------------------------------------------
def test_list_voices_returns_provider_voices() -> None:
    voices = (
        VoiceProfile(id="v1", name="Alpha", language="en"),
        VoiceProfile(id="v2", name="Beta", language="ta"),
    )
    provider = FakeTTS(voices=voices)
    manager = _manager(provider)

    assert manager.list_voices() == voices
    assert manager.list_voices("ta") == (voices[1],)


def test_list_voices_never_raises() -> None:
    class ExplodingTTS(FakeTTS):
        def list_voices(self, language: str | None = None):
            raise RuntimeError("boom")

    manager = _manager(ExplodingTTS())
    assert manager.list_voices() == ()


def test_supports_language_never_raises() -> None:
    class ExplodingTTS(FakeTTS):
        def supports_language(self, language: str) -> bool:
            raise RuntimeError("boom")

    manager = _manager(ExplodingTTS())
    assert manager.supports_language("en") is False


def test_supports_language_delegates() -> None:
    manager = _manager(FakeTTS(languages=("ta", "en")))
    assert manager.supports_language("en") is True
    assert manager.supports_language("fr") is False


# ---------------------------------------------------------------------------
# Custom Azmuth voice is a first-class concept
# ---------------------------------------------------------------------------
def test_custom_azmuth_voice_is_expressible_as_a_profile() -> None:
    custom = VoiceProfile(
        id="azmuth",
        name="Azmuth",
        language="en",
        gender=VoiceGender.NEUTRAL,
        sample_rate=22_050,
        is_custom=True,
        extra={"author": "creator"},
    )
    provider = FakeTTS(voices=(custom,))
    manager = _manager(provider)

    voices = manager.list_voices()
    assert len(voices) == 1
    assert voices[0].is_custom is True
    assert voices[0].id == "azmuth"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def test_default_registry_contains_only_null() -> None:
    registry = build_tts_registry()
    assert registry.names() == ("null",)


def test_registry_can_be_extended_at_runtime() -> None:
    registry = build_tts_registry()
    registry.register("custom", FakeTTS)
    assert "custom" in registry.names()

    manager = TTSManager("custom", registry)
    assert isinstance(manager.provider, FakeTTS)


# ---------------------------------------------------------------------------
# Helper used by an earlier test
# ---------------------------------------------------------------------------
def _settings_stub() -> Settings:
    from pathlib import Path
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    return load_settings(
        env_file=tmp / "missing.env",
        environ={
            "AZMUTH_HOME": str(tmp),
            "AZMUTH_LOG_LEVEL": "WARNING",
            "AZMUTH_LOG_TO_CONSOLE": "false",
            "AZMUTH_LOG_TO_FILE": "false",
            "TTS_PROVIDER": "null",
        },
        create_directories=False,
    )