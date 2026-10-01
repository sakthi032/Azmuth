"""Core interface contracts shared by every subsystem.

Subsystem-specific provider interfaces (``LLMProvider``,
``SpeechToTextProvider``, ``SearchProvider``, …) live next to their subsystem,
in that subsystem's own folder. Only the *smallest common base* lives here, so
``app.core`` never has to import any concrete subsystem.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar


class Provider(ABC):
    """Base for anything pluggable and replaceable.

    A provider is looked up by name (see :class:`app.core.registry.Registry`)
    and asked whether it can serve requests before use. The rest of the
    system must keep working when any provider is unavailable.
    """

    #: Stable, unique name used for registration. Subclasses must set this.
    name: ClassVar[str]

    @abstractmethod
    def is_available(self) -> bool:
        """Return ``True`` if the provider can currently serve requests.

        Implementations must not raise. A failure while *checking*
        availability is itself a signal that the provider is unavailable.
        """


class Lifecycle(ABC):
    """For components that need explicit asynchronous startup/shutdown.

    ``start`` is called once during system startup. ``stop`` must be safe to
    call more than once, and must not raise if the component never started.
    """

    @abstractmethod
    async def start(self) -> None:
        """Acquire resources."""

    @abstractmethod
    async def stop(self) -> None:
        """Release resources."""