"""Microphone audio source via sounddevice.

Delivers 16 kHz mono int16 frames on a background thread, pushed into an
asyncio queue for the voice pipeline to consume. The format is fixed at
what Whisper expects, so no resampling is needed downstream.

Simple energy-based silence detection: frames whose RMS is below
``silence_rms`` are returned as empty frames. The voice manager treats
empty frames as silence and uses consecutive-silence counting to end an
utterance without waiting for the full max_utterance timeout.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from app.core.errors import ProviderUnavailableError
from app.voice.frames import AudioFrame
from app.voice.sources.base import AudioSource

logger = logging.getLogger(__name__)


def _coerce_device(device: int | str | None) -> int | str | None:
    """Normalise a device reference.

    * None or empty string → None (system default)
    * Digits-only string  → int index
    * Anything else       → original string (name-based lookup)
    """
    if device is None:
        return None
    if isinstance(device, int):
        return device
    if isinstance(device, str):
        stripped = device.strip()
        if not stripped:
            return None
        if stripped.isdigit():
            return int(stripped)
        return stripped
    return device

class MicrophoneAudioSource(AudioSource):
    """Capture audio from a microphone via sounddevice."""

    name = "microphone"

    def __init__(
        self,
        *,
        sample_rate: int = 16_000,
        channels: int = 1,
        sample_width: int = 2,
        block_duration_ms: int = 100,
        device: int | str | None = None,
        silence_rms: float = 180.0,
        queue_max: int = 100,
    ) -> None:
        self._sample_rate = sample_rate
        self._channels = channels
        self._sample_width = sample_width
        self._blocksize = max(
            1, int(sample_rate * block_duration_ms / 1000)
        )

        # sounddevice treats string device refs as *name patterns*, so
        # "9" fails to match anything. Convert numeric strings to int
        # so they're interpreted as device indices.
        self._device = _coerce_device(device)
        self._silence_rms = silence_rms if silence_rms is not None else 180.0
        self._queue_max = queue_max

        self._stream: Any = None
        self._queue: asyncio.Queue[bytes] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self._dropped = 0

    # -- probes ------------------------------------------------------------
    def is_available(self) -> bool:
        try:
            import sounddevice as sd  # noqa: F401
        except ImportError:
            return False
        try:
            sd.query_devices()
            return True
        except Exception:
            return False

    @property
    def is_streaming(self) -> bool:
        return self._stream is not None

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        with self._lock:
            if self._stream is not None:
                return

            try:
                import sounddevice as sd
            except ImportError as exc:
                raise ProviderUnavailableError(
                    "sounddevice is not installed. "
                    "Install it with: pip install sounddevice"
                ) from exc

            self._loop = asyncio.get_running_loop()
            self._queue = asyncio.Queue(maxsize=self._queue_max)
            self._dropped = 0

            def _callback(indata, _frames, _time_info, status) -> None:
                # Runs on sounddevice's worker thread — never block here.
                if status:
                    logger.debug(
                        "audio callback status", extra={"status": str(status)}
                    )

                # Silence gate: return b"" for low-energy frames so the
                # manager's consecutive-silence counter works.
                data = indata.tobytes()
                if self._silence_rms > 0 and data:
                    try:
                        import numpy as np
                        arr = np.frombuffer(data, dtype=np.int16)
                        if arr.size:
                            rms = float(
                                np.sqrt(
                                    np.mean(
                                        arr.astype(np.float32) ** 2
                                    )
                                )
                            )
                            if rms < self._silence_rms:
                                data = b""
                    except Exception:
                        pass

                if self._loop is None or self._queue is None:
                    return
                try:
                    self._loop.call_soon_threadsafe(
                        self._enqueue, data
                    )
                except RuntimeError:
                    # Loop closed — shutdown in progress.
                    pass

            stream = sd.InputStream(
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype="int16",
                blocksize=self._blocksize,
                device=self._device,
                callback=_callback,
            )
            stream.start()
            self._stream = stream
            logger.info(
                "microphone stream started",
                extra={
                    "event": "MIC_STARTED",
                    "sample_rate": self._sample_rate,
                    "channels": self._channels,
                    "blocksize": self._blocksize,
                },
            )

    async def stop(self) -> None:
        with self._lock:
            stream = self._stream
            self._stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                logger.exception("failed to close microphone stream")
        self._queue = None
        self._loop = None
        if self._dropped:
            logger.info(
                "microphone dropped %d frames", self._dropped,
                extra={"event": "MIC_FRAMES_DROPPED"},
            )

    # -- read ---------------------------------------------------------------
    async def read_frame(self, timeout: float = 1.0) -> AudioFrame | None:
        queue = self._queue
        if queue is None:
            return None
        try:
            data = await asyncio.wait_for(queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        return AudioFrame(
            data=data,
            sample_rate=self._sample_rate,
            channels=self._channels,
            sample_width=self._sample_width,
        )

    # -- internal -----------------------------------------------------------
    def _enqueue(self, data: bytes) -> None:
        q = self._queue
        if q is None:
            return
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            self._dropped += 1


__all__ = ["MicrophoneAudioSource"]