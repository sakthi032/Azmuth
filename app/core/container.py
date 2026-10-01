"""Minimal service container.

The container holds one instance of each *subsystem* — ``Settings``,
``EventBus``, ``LanguageManager``, ``MemoryManager``, ``ToolManager``,
``TaskManager``, … The brain asks the container for a subsystem; subsystems
never import each other's concrete modules.

Construction is lazy: register a factory, and the container calls it the
first time the key is requested. Cycles are detected and reported with a
clear error instead of a ``RecursionError``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from app.core.errors import ConfigurationError

Factory = Callable[["Container"], Any]


class Container:
    """Lazy service graph keyed by string."""

    def __init__(self) -> None:
        self._factories: dict[str, Factory] = {}
        self._instances: dict[str, Any] = {}
        self._resolving: list[str] = []

    # -- registration ------------------------------------------------------
    def register(
        self,
        key: str,
        factory: Factory,
        *,
        replace: bool = False,
    ) -> None:
        if not key:
            raise ValueError("service key must be non-empty")
        if not replace and (key in self._factories or key in self._instances):
            raise ValueError(f"service {key!r} is already registered")
        self._factories[key] = factory
        self._instances.pop(key, None)

    def register_instance(
        self,
        key: str,
        instance: Any,
        *,
        replace: bool = False,
    ) -> None:
        if not key:
            raise ValueError("service key must be non-empty")
        if not replace and (key in self._factories or key in self._instances):
            raise ValueError(f"service {key!r} is already registered")
        self._factories.pop(key, None)
        self._instances[key] = instance

    # -- introspection -----------------------------------------------------
    def has(self, key: str) -> bool:
        return key in self._factories or key in self._instances

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(set(self._factories) | set(self._instances)))

    def __iter__(self) -> Iterator[str]:
        return iter(self.keys())

    # -- resolution --------------------------------------------------------
    def get(self, key: str) -> Any:
        if key in self._instances:
            return self._instances[key]

        factory = self._factories.get(key)
        if factory is None:
            known = ", ".join(self.keys()) or "(none)"
            raise ConfigurationError(
                f"service {key!r} is not registered. Registered: {known}"
            )

        if key in self._resolving:
            chain = " -> ".join([*self._resolving, key])
            raise ConfigurationError(f"circular service dependency: {chain}")

        self._resolving.append(key)
        try:
            instance = factory(self)
        finally:
            self._resolving.pop()

        self._instances[key] = instance
        return instance

    def clear(self) -> None:
        """Drop every cached instance. Factories remain registered."""
        self._instances.clear()