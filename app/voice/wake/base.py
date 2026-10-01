"""The wake-word detector seam.

Defining this interface in STEP 12 lets the voice pipeline be complete and
testable end-to-end. Concrete detectors — one that runs STT and looks for
the phrase, one that wraps a dedicated wake-word engine, one that reads
from a key — arrive in STEP 13 and plug in without any change to the
manager.

**Independence from the LLM is a design requirement, not an
implementation detail.** A detector processes frames locally, without
network access, and makes a boolean decision. The manager is what emits
an event when that decision is positive; nothing in the pipeline sends
audio to a language model.
"""

from __future__ import annotations

from abc import abstractmethod

from app.core.interfaces import Provider
from app.voice.frames import AudioFrame


class WakeWordDetector(Provider):
    """Detects a wake phrase in a stream of audio frames.

    Implementations are stateful: :meth:`detect` is called once per frame
    in order, and the detector may buffer frames internally to look for
    the phrase across frame boundaries. :meth:`reset` clears that buffer
    (called by the manager after a wake or before a new listening
    session).
    """

    @property
    @abstractmethod
    def phrase(self) -> str:
        """The wake phrase this detector looks for, e.g. ``"azmuth"``."""

    @abstractmethod
    async def detect(self, frame: AudioFrame) -> bool:
        """Consume one frame; return True if the wake phrase just ended.

        Returning True means the detector is confident the phrase
        completed within the frames it has seen. It is the manager's job
        to reset the detector after that.
        """

    @abstractmethod
    def reset(self) -> None:
        """Clear internal state. Safe to call at any time."""