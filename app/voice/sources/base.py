"""The audio source interface."""

from __future__ import annotations

from abc import abstractmethod

from app.core.interfaces import Provider
from app.voice.frames import AudioFrame


class AudioSource(Provider):
    """A source of PCM audio frames.

    Implementations own the underlying device or stream. The contract is
    narrow on purpose:

    * :meth:`start` acquires the device; may raise if the device is
      unavailable.
    * :meth:`stop` releases it; must be idempotent.
    * :meth:`read_frame` returns either one frame or ``None`` on timeout.
      ``None`` is not an error — it means "no audio available right now".

    A source that cannot start must report :meth:`is_available` as
    ``False`` so callers fall back without needing try/except noise.
    """

    @abstractmethod
    async def start(self) -> None:
        """Acquire the device. Idempotent — a second call is a no-op."""

    @abstractmethod
    async def stop(self) -> None:
        """Release the device. Idempotent — safe if never started."""

    @property
    @abstractmethod
    def is_streaming(self) -> bool:
        """True between a successful :meth:`start` and :meth:`stop`."""

    @abstractmethod
    async def read_frame(self, timeout: float = 1.0) -> AudioFrame | None:
        """Return the next frame or ``None`` on timeout.

        Must not raise for a normal timeout. Genuine device errors should
        propagate as :class:`~app.core.errors.ProviderError`; callers
        (the voice manager) wrap them so voice failures never crash the
        assistant.
        """