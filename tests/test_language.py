from __future__ import annotations

import pytest

from app.config.settings import Settings, load_settings
from app.core.errors import LanguageError
from app.language import (
    ENGLISH_PROFILE,
    TAMIL_PROFILE,
    DetectionResult,
    HeuristicLanguageDetector,
    LanguageCode,
    LanguageManager,
    LanguageProfile,
    build_language_manager,
    build_default_profiles,
)
from app.language.base import LanguageDetector


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def settings(tmp_path):
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


@pytest.fixture()
def manager(settings: Settings) -> LanguageManager:
    return build_language_manager(settings)


# ---------------------------------------------------------------------------
# Profile data
# ---------------------------------------------------------------------------
def test_default_profiles_contain_tamil_and_english() -> None:
    profiles = build_default_profiles()
    assert set(profiles) == {"ta", "en"}
    assert profiles["ta"] is TAMIL_PROFILE
    assert profiles["en"] is ENGLISH_PROFILE


def test_profile_rejects_empty_code() -> None:
    with pytest.raises(ValueError, match="invalid language code"):
        LanguageProfile(code="", display_name="x", native_name="x")


def test_profile_normalises_code_case() -> None:
    p = LanguageProfile(code="TA", display_name="Tamil", native_name="தமிழ்")
    assert p.code == "ta"


def test_profile_matches_codepoint() -> None:
    assert TAMIL_PROFILE.matches_codepoint(ord("அ")) is True
    assert TAMIL_PROFILE.matches_codepoint(ord("A")) is False
    assert ENGLISH_PROFILE.matches_codepoint(ord("A")) is True
    assert ENGLISH_PROFILE.matches_codepoint(ord("அ")) is False


# ---------------------------------------------------------------------------
# Detector — script signals
# ---------------------------------------------------------------------------
def test_detect_pure_tamil(manager: LanguageManager) -> None:
    result = manager.detect("இன்று வானிலை என்ன?")
    assert result.primary == LanguageCode.TAMIL.value
    assert result.detected == ("ta",)
    assert result.is_mixed is False
    assert result.confidence > 0.9


def test_detect_pure_english(manager: LanguageManager) -> None:
    result = manager.detect("What is the weather today?")
    assert result.primary == LanguageCode.ENGLISH.value
    assert result.detected == ("en",)
    assert result.is_mixed is False


def test_detect_mixed_tamil_script_and_english(manager: LanguageManager) -> None:
    result = manager.detect("Azmuth, இன்று weather என்ன?")
    assert result.is_mixed is True
    assert set(result.detected) == {"ta", "en"}
    assert result.primary in {"ta", "en"}


# ---------------------------------------------------------------------------
# Detector — Romanized / Tanglish signals
# ---------------------------------------------------------------------------
def test_detect_tanglish_marks_mixed(manager: LanguageManager) -> None:
    result = manager.detect("Chrome open pannu")
    assert result.is_mixed is True
    assert "ta" in result.detected
    assert "en" in result.detected
    assert result.romanized_hits["ta"] >= 1


def test_detect_pure_tanglish_short(manager: LanguageManager) -> None:
    # Entirely Romanized Tamil → Tamil wins because the Romanized weight
    # outweighs the mere presence of Latin letters.
    result = manager.detect("romba nalla irukku")
    assert "ta" in result.detected


def test_detect_english_with_borrowed_tamil_word(manager: LanguageManager) -> None:
    result = manager.detect("Please open Chrome and search YouTube")
    assert result.primary == LanguageCode.ENGLISH.value
    assert result.is_mixed is False


def test_detect_does_not_use_suffix_false_positives(manager: LanguageManager) -> None:
    # "da", "di", "la" alone must not register as Tamil.
    result = manager.detect("la di da")
    assert result.primary in {LanguageCode.ENGLISH.value, LanguageCode.UNKNOWN.value}


# ---------------------------------------------------------------------------
# Detector — degenerate input
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["", "   ", "!!!", "🙂🙂", "1234 5678"])
def test_detect_unknown_for_non_language_input(
    manager: LanguageManager, text: str
) -> None:
    result = manager.detect(text)
    assert result.is_unknown is True
    assert result.detected == ()
    assert result.confidence == 0.0


def test_detection_result_as_dict_is_json_friendly(manager: LanguageManager) -> None:
    payload = manager.detect("hello").as_dict()
    assert payload["primary"] == "en"
    assert payload["detected"] == ["en"]
    assert isinstance(payload["confidence"], float)


# ---------------------------------------------------------------------------
# Manager — profile registry
# ---------------------------------------------------------------------------
def test_manager_reports_supported_languages(manager: LanguageManager) -> None:
    assert manager.supported_languages() == ("en", "ta")
    assert manager.default_language == "en"


def test_manager_normalises_language_codes(manager: LanguageManager) -> None:
    assert manager.is_supported("EN-US") is True
    assert manager.is_supported("ta-in") is True
    assert manager.is_supported("fr") is False
    assert manager.get_profile("EN") is ENGLISH_PROFILE
    assert manager.get_profile("unknown-code") is None


def test_profile_for_raises_on_unknown_code(manager: LanguageManager) -> None:
    with pytest.raises(LanguageError, match="no profile registered"):
        manager.profile_for("fr")


def test_registering_custom_profile_makes_it_available(
    manager: LanguageManager,
) -> None:
    hindi = LanguageProfile(
        code="hi",
        display_name="Hindi",
        native_name="हिन्दी",
        script_ranges=((0x0900, 0x097F),),
    )
    manager.register_profile(hindi)

    assert manager.is_supported("hi")
    # Devanagari "नमस्ते" should now detect as Hindi.
    result = manager.detect("नमस्ते")
    assert result.primary == "hi"


def test_manager_rejects_default_language_without_profile(tmp_path) -> None:
    settings = load_settings(
        env_file=tmp_path / "missing.env",
        environ={
            "AZMUTH_HOME": str(tmp_path),
            "AZMUTH_LOG_TO_CONSOLE": "false",
            "AZMUTH_LOG_TO_FILE": "false",
            "AZMUTH_DEFAULT_LANGUAGE": "en",  # valid, but:
        },
        create_directories=False,
    )
    # Build a manager with an empty profile set — must refuse.
    with pytest.raises(LanguageError, match="default language"):
        LanguageManager(settings, profiles=[])


# ---------------------------------------------------------------------------
# Manager — response-language policy
# ---------------------------------------------------------------------------
def test_response_language_matches_detected(manager: LanguageManager) -> None:
    assert manager.resolve_response_language("What is the weather?") == "en"
    assert manager.resolve_response_language("வானிலை என்ன?") == "ta"


def test_response_language_unknown_falls_back_to_default(
    manager: LanguageManager,
) -> None:
    assert manager.resolve_response_language("") == "en"


def test_response_language_unknown_uses_session_when_available(
    manager: LanguageManager,
) -> None:
    assert manager.resolve_response_language("", session_language="ta") == "ta"


def test_response_language_preserves_session_on_mixed(
    manager: LanguageManager,
) -> None:
    # Tanglish detected as {en, ta}; established session is Tamil → answer in Tamil.
    assert (
        manager.resolve_response_language("Chrome open pannu", session_language="ta")
        == "ta"
    )


def test_response_language_ignores_session_not_in_detected_set(
    manager: LanguageManager,
) -> None:
    # Pure English text, session says Tamil → still answer in English.
    assert (
        manager.resolve_response_language("Open Chrome", session_language="ta")
        == "en"
    )


def test_response_language_ignores_unsupported_session(
    manager: LanguageManager,
) -> None:
    assert (
        manager.resolve_response_language("", session_language="fr") == "en"
    )


# ---------------------------------------------------------------------------
# Detector interface
# ---------------------------------------------------------------------------
def test_heuristic_detector_satisfies_interface() -> None:
    assert isinstance(HeuristicLanguageDetector(), LanguageDetector)
    assert HeuristicLanguageDetector().is_available() is True


def test_manager_accepts_custom_detector(settings: Settings) -> None:
    class AlwaysTamil(LanguageDetector):
        name = "always-ta"

        def is_available(self) -> bool:
            return True

        def detect(self, text, profiles) -> DetectionResult:  # noqa: ANN001
            return DetectionResult(
                primary="ta", detected=("ta",), is_mixed=False, confidence=1.0
            )

    manager = LanguageManager(settings, detector=AlwaysTamil())
    assert manager.detect("anything").primary == "ta"
    assert manager.resolve_response_language("literally anything") == "ta"