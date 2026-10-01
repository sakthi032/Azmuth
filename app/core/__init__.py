"""Core primitives: errors, events, interfaces, registry, container."""

from app.core.container import Container
from app.core.errors import (
    AuthenticationError,
    AutomationError,
    AzmuthError,
    ConfigurationError,
    ConfirmationRequiredError,
    MemoryStoreError,
    PermissionDeniedError,
    ProviderError,
    ProviderNotFoundError,
    ProviderUnavailableError,
    SessionNotFoundError,
    TaskCancelledError,
    TaskError,
    TaskNotFoundError,
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolValidationError,
)
from app.core.events import Event, EventBus, EventName, emit
from app.core.interfaces import Lifecycle, Provider
from app.core.registry import Registry

__all__ = [
    # errors
    "AuthenticationError",
    "AutomationError",
    "AzmuthError",
    "ConfigurationError",
    "ConfirmationRequiredError",
    "MemoryStoreError",
    "PermissionDeniedError",
    "ProviderError",
    "ProviderNotFoundError",
    "ProviderUnavailableError",
    "SessionNotFoundError",
    "TaskCancelledError",
    "TaskError",
    "TaskNotFoundError",
    "ToolError",
    "ToolExecutionError",
    "ToolNotFoundError",
    "ToolValidationError",
    # primitives
    "Container",
    "Event",
    "EventBus",
    "EventName",
    "Lifecycle",
    "Provider",
    "Registry",
    "emit",
]