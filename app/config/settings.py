
# app/config/settings.py
"""Typed configuration loading for Azmuth.

Precedence (highest wins):

1. the explicit ``environ`` mapping passed to :func:`load_settings`
2. the process environment
3. values read from the ``.env`` file
4. built-in defaults

Secrets are never hard-coded here. They are read from the environment and
are redacted by :meth:`Settings.describe`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Mapping

from dotenv import dotenv_values

from app.core.errors import ConfigurationError

_TRUE: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})
_FALSE: Final[frozenset[str]] = frozenset({"0", "false", "no", "off"})
_VALID_LOG_LEVELS: Final[frozenset[str]] = frozenset(
    {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
)

#: Languages Azmuth treats as first-class.
PRIMARY_LANGUAGES: Final[tuple[str, ...]] = ("ta", "en")

#: Project root, derived from this file's location: app/config/settings.py
_PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_DEFAULT_SEARXNG_URL: Final[str] = "http://localhost:8888"

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Paths:
    """Filesystem layout used by Azmuth."""

    root: Path
    data: Path
    memory: Path
    conversations: Path
    users: Path
    logs: Path
    cache: Path
    plugins: Path

    def ensure(self) -> None:
        for directory in (
            self.data,
            self.memory,
            self.conversations,
            self.users,
            self.logs,
            self.cache,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def as_dict(self) -> dict[str, str]:
        return {
            "root": str(self.root),
            "data": str(self.data),
            "memory": str(self.memory),
            "conversations": str(self.conversations),
            "users": str(self.users),
            "logs": str(self.logs),
            "cache": str(self.cache),
            "plugins": str(self.plugins),
        }


def build_paths(root: Path) -> Paths:
    data = root / "data"
    return Paths(
        root=root,
        data=data,
        memory=data / "memory",
        conversations=data / "conversations",
        users=data / "users",
        logs=data / "logs",
        cache=data / "cache",
        plugins=root / "app" / "plugins",
    )


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable, fully validated Azmuth configuration."""

    env: str
    debug: bool

    log_level: str
    log_json: bool
    log_to_file: bool
    log_to_console: bool

    default_language: str
    supported_languages: tuple[str, ...]

    # ---- LLM -----------------------------------------------------------
    llm_provider: str
    llm_model: str
    llm_base_url: str | None
    llm_api_key: str | None
    llm_timeout_seconds: float
    llm_default_purpose: str
    llm_prefer_local: bool

    # ---- Local (Ollama) -------------------------------------------------
    ollama_base_url: str

    # ---- NVIDIA NIM -----------------------------------------------------
    nvidia_base_url: str
    nvidia_api_key: str | None

    # ---- Groq -----------------------------------------------------------
    groq_base_url: str
    groq_api_key: str | None

    # ---- Google Gemini --------------------------------------------------
    gemini_base_url: str
    gemini_api_key: str | None

    # ---- Search ---------------------------------------------------------
    search_provider: str
    search_api_key: str | None
    # ---- Search: provider order (tiered fallback) -----------------------
    search_provider_order: tuple[str, ...]

    # ---- Search: SearXNG ------------------------------------------------
    searxng_base_url: str
    searxng_timeout_seconds: float
    searxng_language: str
    searxng_api_key: str | None

    # ---- Voice ----------------------------------------------------------
    stt_provider: str
    tts_provider: str
    wake_word_provider: str
    wake_word_phrase: str

    # ---- Memory ---------------------------------------------------------
    memory_backend: str
    memory_database: str
    embedding_provider: str
    postgres_host: str
    postgres_port: int
    postgres_database: str
    postgres_user: str
    postgres_password: str
    postgres_schema: str
    # ---- PostgreSQL (used when memory_backend == "postgres") -----------
    postgres_host: str
    postgres_port: int
    postgres_database: str
    postgres_user: str
    postgres_password: str
    postgres_schema: str

    # ---- Security -------------------------------------------------------
    creator_auth_method: str

    # ---- File sandbox ---------------------------------------------------
    file_sandbox_roots: tuple[str, ...]
    
    # ---- Session / tasks ------------------------------------------------
    session_max_turns: int
    session_ttl_minutes: int
    task_max_concurrent: int

    # ---- Location -------------------------------------------------------
    location_city: str | None
    location_region: str | None
    location_country: str | None
    location_country_code: str | None
    location_timezone: str | None
    location_currency: str | None
    location_units: str
    paths: Paths

    # -- helpers ----------------------------------------------------------
    @property
    def is_development(self) -> bool:
        return self.env.lower() in {"development", "dev", "local"}

    def describe(self) -> dict[str, Any]:
        """Return a log-safe summary. Never exposes secret values."""
        return {
            "env": self.env,
            "debug": self.debug,
            "log_level": self.log_level,
            "default_language": self.default_language,
            "supported_languages": list(self.supported_languages),
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "llm_base_url": self.llm_base_url,
            "llm_api_key": "***redacted***" if self.llm_api_key else None,
            "llm_default_purpose": self.llm_default_purpose,
            "llm_prefer_local": self.llm_prefer_local,
            "ollama_base_url": self.ollama_base_url,
            "nvidia_base_url": self.nvidia_base_url,
            "nvidia_api_key": "***redacted***" if self.nvidia_api_key else None,
            "groq_base_url": self.groq_base_url,
            "groq_api_key": "***redacted***" if self.groq_api_key else None,
            "gemini_base_url": self.gemini_base_url,
            "gemini_api_key": "***redacted***" if self.gemini_api_key else None,
            "search_provider": self.search_provider,
            "search_api_key": "***redacted***" if self.search_api_key else None,
            "stt_provider": self.stt_provider,
            "tts_provider": self.tts_provider,
            "wake_word_provider": self.wake_word_provider,
            "memory_backend": self.memory_backend,
            "embedding_provider": self.embedding_provider,
            "creator_auth_method": self.creator_auth_method,
            "paths": self.paths.as_dict(),
            "postgres_host": self.postgres_host,
            "postgres_port": self.postgres_port,
            "postgres_database": self.postgres_database,
            "postgres_user": self.postgres_user,
            "postgres_password": "***redacted***" if self.postgres_password else None,
            "postgres_schema": self.postgres_schema,
            "file_sandbox_roots": list(self.file_sandbox_roots),
            "search_provider_order": list(self.search_provider_order),
            "searxng_base_url": self.searxng_base_url,
            "searxng_timeout_seconds": self.searxng_timeout_seconds,
            "searxng_language": self.searxng_language,
            "searxng_api_key": "***redacted***" if self.searxng_api_key else None,
        }


# --------------------------------------------------------------------------
# Primitive readers
# --------------------------------------------------------------------------
def _get_str(
    env: Mapping[str, str], key: str, default: str | None = None
) -> str | None:
    raw = env.get(key)
    if raw is None:
        return default
    raw = raw.strip()
    return raw if raw else default


def _get_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    raise ConfigurationError(
        f"{key} must be a boolean (one of {sorted(_TRUE | _FALSE)}), got {raw!r}"
    )

def _get_int(
    env: Mapping[str, str],
    key: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = _get_str(env, key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(
            f"{key} must be an integer, got {raw!r}"
        ) from exc
    if minimum is not None and value < minimum:
        raise ConfigurationError(f"{key} must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise ConfigurationError(f"{key} must be <= {maximum}, got {value}")
    return value


def _get_float(
    env: Mapping[str, str],
    key: str,
    default: float,
    *,
    minimum: float | None = None,
) -> float:
    raw = _get_str(env, key)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(
            f"{key} must be a number, got {raw!r}"
        ) from exc
    if minimum is not None and value < minimum:
        raise ConfigurationError(f"{key} must be >= {minimum}, got {value}")
    return value


def _get_csv(env: Mapping[str, str], key: str) -> tuple[str, ...]:
    raw = _get_str(env, key)
    if raw is None:
        return ()
    return tuple(
        part.strip().lower() for part in raw.split(",") if part.strip()
    )

def _get_path_csv(env: Mapping[str, str], key: str) -> tuple[str, ...]:
    """Comma-separated file paths. Does NOT lowercase — paths are
    case-sensitive on some filesystems and mixed-case Windows paths
    are the norm."""
    raw = _get_str(env, key)
    if raw is None:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())

# --------------------------------------------------------------------------
# Loader
# --------------------------------------------------------------------------
def load_settings(
    *,
    env_file: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
    create_directories: bool = True,
) -> Settings:
    """Build a validated :class:`Settings` object."""
    if env_file is None:
        env_file = _PROJECT_ROOT / ".env"
    env_path = Path(env_file)

    file_values: dict[str, str] = {}
    if env_path.is_file():
        for key, value in dotenv_values(env_path).items():
            if value is not None:
                file_values[key] = value

    process_values = dict(os.environ if environ is None else environ)
    env: dict[str, str] = {**file_values, **process_values}

    env_name = _get_str(env, "AZMUTH_ENV", "development") or "development"
    is_dev = env_name.lower() in {"development", "dev", "local"}

    log_level = (
        _get_str(
            env, "AZMUTH_LOG_LEVEL", "DEBUG" if is_dev else "INFO"
        ) or "INFO"
    ).upper()
    if log_level not in _VALID_LOG_LEVELS:
        raise ConfigurationError(
            f"AZMUTH_LOG_LEVEL must be one of "
            f"{sorted(_VALID_LOG_LEVELS)}, got {log_level!r}"
        )

    home = _get_str(env, "AZMUTH_HOME")
    root = Path(home).expanduser().resolve() if home else _PROJECT_ROOT
    paths = build_paths(root)

    memory_database = _get_str(env, "MEMORY_DATABASE") or str(
        paths.memory / "azmuth_memory.db"
    )

    default_language = (
        _get_str(env, "AZMUTH_DEFAULT_LANGUAGE", "en") or "en"
    ).lower()
    extra_languages = _get_csv(env, "AZMUTH_EXTRA_LANGUAGES")
    supported = tuple(
        dict.fromkeys((*PRIMARY_LANGUAGES, *extra_languages))
    )

    settings = Settings(
        env=env_name,
        debug=_get_bool(env, "AZMUTH_DEBUG", is_dev),
        log_level=log_level,
        log_json=_get_bool(env, "AZMUTH_LOG_JSON", False),
        log_to_file=_get_bool(env, "AZMUTH_LOG_TO_FILE", True),
        log_to_console=_get_bool(env, "AZMUTH_LOG_TO_CONSOLE", is_dev),
        default_language=default_language,
        supported_languages=supported,
        llm_provider=(_get_str(env, "LLM_PROVIDER", "stub") or "stub").lower(),
        llm_model=_get_str(env, "LLM_MODEL", "") or "",
        llm_base_url=_get_str(env, "LLM_BASE_URL"),
        llm_api_key=_get_str(env, "LLM_API_KEY"),
        llm_timeout_seconds=_get_float(
            env, "LLM_TIMEOUT_SECONDS", 60.0, minimum=0.1
        ),
        llm_default_purpose=(
            _get_str(env, "LLM_DEFAULT_PURPOSE", "chat") or "chat"
        ).lower(),
        llm_prefer_local=_get_bool(env, "LLM_PREFER_LOCAL", True),
        ollama_base_url=(
            _get_str(
                env, "OLLAMA_BASE_URL", "http://localhost:11434/v1"
            )
            or "http://localhost:11434/v1"
        ),
        nvidia_base_url=(
            _get_str(
                env,
                "NVIDIA_BASE_URL",
                "https://integrate.api.nvidia.com/v1",
            )
            or "https://integrate.api.nvidia.com/v1"
        ),
        nvidia_api_key=_get_str(env, "NVIDIA_API_KEY"),
        groq_base_url=(
            _get_str(
                env, "GROQ_BASE_URL", "https://api.groq.com/openai/v1"
            )
            or "https://api.groq.com/openai/v1"
        ),
        groq_api_key=_get_str(env, "GROQ_API_KEY"),
        gemini_base_url=(
            _get_str(
                env,
                "GEMINI_BASE_URL",
                "https://generativelanguage.googleapis.com/v1beta/openai/",
            )
            or "https://generativelanguage.googleapis.com/v1beta/openai/"
        ),
        gemini_api_key=_get_str(env, "GEMINI_API_KEY"),
        search_provider=(
            _get_str(env, "SEARCH_PROVIDER", "null") or "null"
        ).lower(),
        search_api_key=_get_str(env, "SEARCH_API_KEY"),
        search_provider_order=(
            _get_csv(env, "SEARCH_PROVIDER_ORDER")
            or ("searxng", "duckduckgo", "null")
        ),
        searxng_base_url=(
            _get_str(env, "SEARXNG_BASE_URL", _DEFAULT_SEARXNG_URL)
            or _DEFAULT_SEARXNG_URL
        ),
        searxng_timeout_seconds=_get_float(
            env, "SEARXNG_TIMEOUT_SECONDS", 15.0, minimum=0.1
        ),
        searxng_language=(
            _get_str(env, "SEARXNG_LANGUAGE", "en") or "en"
        ).lower(),
        searxng_api_key=_get_str(env, "SEARXNG_API_KEY"),
        stt_provider=(_get_str(env, "STT_PROVIDER", "null") or "null").lower(),
        tts_provider=(_get_str(env, "TTS_PROVIDER", "null") or "null").lower(),
        wake_word_provider=(
            _get_str(env, "WAKE_WORD_PROVIDER", "null") or "null"
        ).lower(),
        wake_word_phrase=(
            _get_str(env, "WAKE_WORD_PHRASE", "azmuth") or "azmuth"
        ),
        memory_backend=(
            _get_str(env, "MEMORY_BACKEND", "sqlite") or "sqlite"
        ).lower(),
        memory_database=memory_database,
        embedding_provider=(
            _get_str(env, "EMBEDDING_PROVIDER", "null") or "null"
        ).lower(),
        creator_auth_method=(
            _get_str(env, "CREATOR_AUTH_METHOD", "pin") or "pin"
        ).lower(),
        session_max_turns=_get_int(
            env, "SESSION_MAX_TURNS", 20, minimum=1
        ),
        session_ttl_minutes=_get_int(
            env, "SESSION_TTL_MINUTES", 120, minimum=1
        ),
        task_max_concurrent=_get_int(
            env, "TASK_MAX_CONCURRENT", 4, minimum=1
        ),
        paths=paths,
        postgres_host=_get_str(env, "POSTGRES_HOST", "localhost") or "localhost",
        postgres_port=_get_int(env, "POSTGRES_PORT", 3207, minimum=1, maximum=65535),
        postgres_database=_get_str(env, "POSTGRES_DATABASE", "azmuth") or "azmuth",
        postgres_user=_get_str(env, "POSTGRES_USER", "postgres") or "postgres",
        postgres_password=_get_str(env, "POSTGRES_PASSWORD", "") or "",
        postgres_schema=_get_str(env, "POSTGRES_SCHEMA", "public") or "public",
        file_sandbox_roots=_get_path_csv(env, "FILE_SANDBOX_ROOTS")
        or (str(_PROJECT_ROOT), str(Path.home())),

        location_city=_get_str(env, "AZMUTH_LOCATION_CITY"),
        location_region=_get_str(env, "AZMUTH_LOCATION_REGION"),
        location_country=_get_str(env, "AZMUTH_LOCATION_COUNTRY"),
        location_country_code=_get_str(env, "AZMUTH_LOCATION_COUNTRY_CODE"),
        location_timezone=_get_str(env, "AZMUTH_LOCATION_TIMEZONE"),
        location_currency=_get_str(env, "AZMUTH_LOCATION_CURRENCY"),
        location_units=(_get_str(env, "AZMUTH_LOCATION_UNITS", "metric") or "metric").lower(),
        
    )

    if default_language not in supported:
        raise ConfigurationError(
            f"AZMUTH_DEFAULT_LANGUAGE {default_language!r} is not in "
            f"the supported language set {supported}"
        )

    if create_directories:
        paths.ensure()

    return settings
