"""Serialised speech queue.

Multiple speech sources exist in one turn: progress narration during
tool execution and the final response. They must play in order, never
overlap, and progress messages must be droppable when the user sends
another message.

The queue is the single arbiter of "what plays next".
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

class SpeechQueue:
    """Async FIFO queue for TTS playback."""

    def __init__(self, tts) -> None:
        self._tts = tts
        self._queue = None                    # ← None until start()
        self._worker: asyncio.Task | None = None
        self._generation = 0
        self._started = False

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        """Create the worker task. Must run on the loop thread."""
        if self._started:
            return
        self._started = True
        self._queue = asyncio.Queue()          # ← created here, on the loop
        self._worker = asyncio.create_task(
            self._run(), name="speech_queue_worker"
        )
        logger.info("speech queue started")

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):
                pass
            self._worker = None
        self._started = False

    # -- public ------------------------------------------------------------
    async def enqueue(
        self, text: str, *, language: str | None = None
    ) -> None:
        if not text or not text.strip() or self._queue is None:
            return
        await self._queue.put((text, language, self._generation))

    async def cancel_pending(self) -> None:
        self._generation += 1
        if self._queue is None:
            return
        drained = 0
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                drained += 1
            except asyncio.QueueEmpty:
                break
        self._stop_playback()
        if drained:
            logger.info(
                "speech queue cancelled %d pending segments", drained,
                extra={"event": "SPEECH_QUEUE_CANCELLED", "count": drained},
            )

    def pending(self) -> int:
        return self._queue.qsize() if self._queue else 0

    # -- internal ----------------------------------------------------------
    async def _run(self) -> None:
        while True:
            try:
                text, lang, gen = await self._queue.get()
            except asyncio.CancelledError:
                return
            if gen != self._generation:
                continue
            try:
                await self._tts.speak_response(text, language=lang)
            except Exception:
                logger.exception(
                    "speech queue playback failed",
                    extra={"event": "SPEECH_QUEUE_PLAYBACK_FAIL"},
                )

    def _stop_playback(self) -> None:
        provider = getattr(self._tts, "provider", None)
        if provider is None:
            return
        stop = getattr(provider, "stop_playback", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                logger.exception("provider.stop_playback failed")


__all__ = ["SpeechQueue"]