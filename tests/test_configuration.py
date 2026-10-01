# tests/test_configuration.py
from __future__ import annotations

from pathlib import Path

import pytest

from app.config.settings import PRIMARY_LANGUAGES, load_settings
from app.core.errors import ConfigurationError


def _load(**env: str):
    return load_settings(environ=env, env_file=Path("does-not-exist.env"), create_directories=False)


def test_defaults_are_safe_and_complete() -> None:
    settings = _load()

    assert settings.env == "development"
    assert settings.debug is True
    assert settings.llm_provider == "stub"
    assert settings.llm_api_key is None
    assert settings.search_api_key is None
    assert settings.memory_backend == "sqlite"
    assert settings.supported_languages == PRIMARY_LANGUAGES
    assert "ta" in settings.supported_languages
    assert "en" in settings.supported_languages


def test_environment_overrides_defaults() -> None:
    settings = _load(
        AZMUTH_ENV="production",
        LLM_PROVIDER="openai_compatible",
        LLM_MODEL="some-model",
        LLM_API_KEY="secret-key",
        SESSION_MAX_TURNS="5",
    )

    assert settings.env == "production"
    assert settings.is_development is False
    assert settings.llm_provider == "openai_compatible"
    assert settings.llm_model == "some-model"
    assert settings.llm_api_key == "secret-key"
    assert settings.session_max_turns == 5
    # Production defaults to quiet console logging.
    assert settings.log_to_console is False


def test_env_file_is_loaded_and_environment_wins(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "LLM_PROVIDER=from_file\nLLM_MODEL=file-model\nAZMUTH_DEFAULT_LANGUAGE=ta\n",
        encoding="utf-8",
    )

    settings = load_settings(
        env_file=env_file, environ={"LLM_PROVIDER": "from_env"}, create_directories=False
    )

    assert settings.llm_provider == "from_env"  # environment beats .env
    assert settings.llm_model == "file-model"  # .env beats defaults
    assert settings.default_language == "ta"


def test_extra_languages_extend_primary_set() -> None:
    settings = _load(AZMUTH_EXTRA_LANGUAGES="hi, te ,,")
    assert settings.supported_languages == ("ta", "en", "hi", "te")


def test_default_language_must_be_supported() -> None:
    with pytest.raises(ConfigurationError, match="not in the supported language set"):
        _load(AZMUTH_DEFAULT_LANGUAGE="fr")


@pytest.mark.parametrize("value", ["maybe", "2", ""])
def test_invalid_boolean_is_rejected(value: str) -> None:
    # An empty string is only valid because it is treated as "unset"; a
    # non-empty invalid token must raise.
    if value == "":
        assert _load(AZMUTH_DEBUG=value).debug is True
        return
    with pytest.raises(ConfigurationError, match="must be a boolean"):
        _load(AZMUTH_DEBUG=value)


def test_invalid_integer_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="must be an integer"):
        _load(SESSION_MAX_TURNS="many")


def test_integer_bounds_are_enforced() -> None:
    with pytest.raises(ConfigurationError, match="must be >= 1"):
        _load(TASK_MAX_CONCURRENT="0")


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="AZMUTH_LOG_LEVEL"):
        _load(AZMUTH_LOG_LEVEL="CHATTY")


def test_describe_redacts_secrets() -> None:
    settings = _load(LLM_API_KEY="super-secret", SEARCH_API_KEY="another-secret")
    described = settings.describe()

    assert described["llm_api_key"] == "***redacted***"
    assert described["search_api_key"] == "***redacted***"
    assert "super-secret" not in str(described)


def test_ensure_creates_data_tree(tmp_path: Path) -> None:
    settings = load_settings(
        environ={"AZMUTH_HOME": str(tmp_path)},
        env_file=tmp_path / "missing.env",
        create_directories=True,
    )

    for directory in (
        settings.paths.data,
        settings.paths.memory,
        settings.paths.conversations,
        settings.paths.users,
        settings.paths.logs,
        settings.paths.cache,
    ):
        assert directory.is_dir()