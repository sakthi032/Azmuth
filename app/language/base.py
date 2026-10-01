"""The language detector interface."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Iterable

from app.core.interfaces import Provider
from app.language.profiles import DetectionResult, LanguageProfile


class LanguageDetector(Provider):
    """Detect which language(s) a piece of text contains.

    Implementations must never raise on ordinary input: an empty string, an
    emoji, or pure punctuation must all yield an ``UNKNOWN`` result rather
    than an exception. That keeps callers free of try/except noise.
    """

    @abstractmethod
    def detect(
        self, text: str, profiles: Iterable[LanguageProfile]
    ) -> DetectionResult:
        """Return the detection result for ``text``."""