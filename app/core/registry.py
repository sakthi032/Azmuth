"""Generic name → factory registry.

A registry answers the question *"which implementations of this kind of
thing exist, and how do I build one by name?"*. Provider families use one
registry each: an LLM registry, an STT registry, a search registry, and so on.

For the app's *services* (one instance of each — ``LanguageManager``,
``MemoryManager``, ``ToolManager``, …) use :class:`app.core.container.Container`
instead. The two are deliberately separate concepts:

* ``Registry[T]`` — many implementations of the same kind of thing.
* ``Container``  — one of each subsystem.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Generic, TypeVar

from app.core.errors import ProviderNotFoundError

T = TypeVar("T")
Factory = Callable[..., T]


class Registry(Generic[T]):
    """Name → factory map with lazy, cached instantiation."""

    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._factories: dict[str, Factory[T]] = {}
        self._instances: dict[str, T] = {}
        self._singletons: dict[str, bool] = {}

    # -- introspection -----------------------------------------------------
    @property
    def kind(self) -> str:
        return self._kind

    def has(self, name: str) -> bool:
        return name.strip().lower() in self._factories

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def __iter__(self) -> Iterator[str]:
        return iter(self.names())

    def __len__(self) -> int:
        return len(self._factories)

    # -- registration ------------------------------------------------------
    def register(
        self,
        name: str,
        factory: Factory[T],
        *,
        singleton: bool = True,
        replace: bool = False,
    ) -> None:
        key = name.strip().lower()
        if not key:
            raise ValueError("registry name must be non-empty")
        if key in self._factories and not replace:
            raise ValueError(f"{self._kind} {key!r} is already registered")

        self._factories[key] = factory
        self._singletons[key] = singleton
        # Drop any cached instance so ``replace=True`` actually takes effect.
        self._instances.pop(key, None)

    def unregister(self, name: str) -> None:
        key = name.strip().lower()
        self._factories.pop(key, None)
        self._instances.pop(key, None)
        self._singletons.pop(key, None)

    # -- instantiation -----------------------------------------------------
    def create(self, name: str, **kwargs: object) -> T:
        """Build (or return the cached) instance for ``name``.

        Keyword arguments are forwarded to the factory. When any are given,
        the singleton cache is bypassed so configuration call sites always
        get a fresh, fully configured instance.
        """
        key = name.strip().lower()
        factory = self._factories.get(key)
        if factory is None:
            known = ", ".join(self.names()) or "(none)"
            raise ProviderNotFoundError(
                f"{self._kind} provider {name!r} is not registered. "
                f"Known providers: {known}"
            )

        use_cache = self._singletons[key] and not kwargs
        if use_cache and key in self._instances:
            return self._instances[key]

        instance = factory(**kwargs)
        if use_cache:
            self._instances[key] = instance
        return instance