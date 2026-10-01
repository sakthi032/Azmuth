# app/core/errors.py

"""Azmuth exception hierarchy.

Every subsystem raises a subclass of :class:`AzmuthError` so the top level
can distinguish *expected* failures (recoverable, user-facing) from genuine
programming bugs. Subsystems must never let a provider-specific exception
escape their boundary; wrap it in the matching error below.
"""

from __future__ import annotations

__all__ = [
    "AzmuthError",
    "ConfigurationError",
    "ProviderError",
    "ProviderNotFoundError",
    "ProviderUnavailableError",
    "AuthenticationError",
    "PermissionDeniedError",
    "ConfirmationRequiredError",
    "ToolError",
    "ToolNotFoundError",
    "ToolValidationError",
    "ToolExecutionError",
    "AutomationError",
    "MemoryStoreError",
    "SessionNotFoundError",
    "TaskError",
    "TaskNotFoundError",
    "TaskCancelledError",
    "ProviderError",
    "ProviderNotFoundError",
    "ProviderUnavailableError",
    "LLMError",
]


class AzmuthError(Exception):
    """Base class for all expected Azmuth failures."""


# --------------------------------------------------------------------------
# Configuration / bootstrap
# --------------------------------------------------------------------------
class ConfigurationError(AzmuthError):
    """Invalid or missing configuration value."""


# --------------------------------------------------------------------------
# Providers (LLM, STT, TTS, wake word, search, embeddings, vector store)
# --------------------------------------------------------------------------
class ProviderError(AzmuthError):
    """A provider failed while performing an operation."""


class ProviderNotFoundError(ProviderError):
    """No provider is registered under the requested name."""


class ProviderUnavailableError(ProviderError):
    """The provider exists but is currently unreachable / not configured."""

class LLMError(ProviderError):
    """An LLM provider failed in a way specific to LLM semantics."""
    
class LanguageError(AzmuthError):
    """Language subsystem failure (unknown code, bad profile, etc.)."""
    

    
# --------------------------------------------------------------------------
# Security
# --------------------------------------------------------------------------
class AuthenticationError(AzmuthError):
    """Authentication failed or the requested method is unavailable."""


class PermissionDeniedError(AzmuthError):
    """The current principal is not allowed to perform the operation."""


class ConfirmationRequiredError(AzmuthError):
    """The operation needs explicit user confirmation before it may run."""


# --------------------------------------------------------------------------
# Tools / automation
# --------------------------------------------------------------------------
class ToolError(AzmuthError):
    """Base class for tool subsystem failures."""


class ToolNotFoundError(ToolError):
    """No tool is registered under the requested name."""


class ToolValidationError(ToolError):
    """Arguments supplied to a tool failed schema validation."""


class ToolExecutionError(ToolError):
    """A tool raised while executing."""


class AutomationError(AzmuthError):
    """A Windows automation primitive failed."""


# --------------------------------------------------------------------------
# Memory / conversation
# --------------------------------------------------------------------------
class MemoryStoreError(AzmuthError):
    """The long-term memory backend failed."""

class MemoryNotFoundError(MemoryStoreError):
    """The requested memory record does not exist."""
    
    
class SessionNotFoundError(AzmuthError):
    """The requested conversation session does not exist."""


# --------------------------------------------------------------------------
# Tasks
# --------------------------------------------------------------------------
class TaskError(AzmuthError):
    """Base class for background task failures."""


class TaskNotFoundError(TaskError):
    """No task is registered under the requested id."""


class TaskCancelledError(TaskError):
    """The task was cancelled before it completed."""