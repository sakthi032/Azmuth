"""Heuristic, dependency-free language detector.

Two signals are used:

1. **Script** — codepoints are matched against each profile's
   ``script_ranges`` (Tamil block, Latin block, …). This is reliable and
   cheap.
2. **Romanized tokens** — the text is lowercased, split on non-letters, and
   each token is looked up in each profile's ``romanized_tokens`` set. This
   is what lets ``"Chrome open pannu"`` register as containing Tamil.

The Romanized-token signal is a *heuristic*. It will miss unknown Tanglish
words and can be fooled by coincidental spellings. It is deliberately kept
small and word-level to avoid false positives, and it is the piece that will
be replaced first when a real ML detector is available — hence the
:class:`LanguageDetector` interface.

Nothing here is English- or Tamil-specific: the detector works on profiles,
so a new language participates automatically.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.language.base import LanguageDetector
from app.language.profiles import DetectionResult, LanguageCode, LanguageProfile

_TOKEN_SPLIT = re.compile(r"[^\w\u0B80-\u0BFF]+", re.UNICODE)

#: Each Romanized word is treated as worth this many script characters when
#: combining signals for the primary-language decision. A whole word carries
#: more evidence than a single character, but not so much that one borrowed
#: word flips the classification of an otherwise-English sentence.
_ROMANIZED_WEIGHT = 5


class HeuristicLanguageDetector(LanguageDetector):
    """Script + Romanized-token detection, no external dependencies."""

    name = "heuristic"

    def is_available(self) -> bool:
        return True

    def detect(
        self, text: str, profiles: Iterable[LanguageProfile]
    ) -> DetectionResult:
        profile_list = list(profiles)
        if not text or not text.strip() or not profile_list:
            return self._unknown()

        script_hits = self._count_script_hits(text, profile_list)
        romanized_hits = self._count_romanized_hits(text, profile_list)

        detected_set = {
            code for code, count in script_hits.items() if count > 0
        } | {
            code for code, count in romanized_hits.items() if count > 0
        }

        if not detected_set:
            return self._unknown(script_hits=script_hits, romanized_hits=romanized_hits)

        signals = {
            code: script_hits.get(code, 0)
            + romanized_hits.get(code, 0) * _ROMANIZED_WEIGHT
            for code in detected_set
        }
        total_signal = sum(signals.values()) or 1

        ordered = tuple(
            code
            for code, _ in sorted(
                signals.items(), key=lambda item: (-item[1], item[0])
            )
        )
        primary = ordered[0]
        confidence = min(1.0, signals[primary] / total_signal)
        is_mixed = len(detected_set) > 1

        return DetectionResult(
            primary=primary,
            detected=ordered,
            is_mixed=is_mixed,
            confidence=confidence,
            script_hits=script_hits,
            romanized_hits=romanized_hits,
        )

    # -- internals ---------------------------------------------------------
    @staticmethod
    def _count_script_hits(
        text: str, profiles: list[LanguageProfile]
    ) -> dict[str, int]:
        hits: dict[str, int] = {p.code: 0 for p in profiles}
        for char in text:
            cp = ord(char)
            for profile in profiles:
                if profile.matches_codepoint(cp):
                    hits[profile.code] += 1
                    break  # a codepoint belongs to at most one script here
        return hits

    @staticmethod
    def _count_romanized_hits(
        text: str, profiles: list[LanguageProfile]
    ) -> dict[str, int]:
        hits: dict[str, int] = {p.code: 0 for p in profiles}
        words = [tok for tok in _TOKEN_SPLIT.split(text.lower()) if tok]
        if not words:
            return hits
        for word in words:
            for profile in profiles:
                if profile.romanized_tokens and word in profile.romanized_tokens:
                    hits[profile.code] += 1
                    break
        return hits

    @staticmethod
    def _unknown(
        *,
        script_hits: dict[str, int] | None = None,
        romanized_hits: dict[str, int] | None = None,
    ) -> DetectionResult:
        return DetectionResult(
            primary=LanguageCode.UNKNOWN.value,
            detected=(),
            is_mixed=False,
            confidence=0.0,
            script_hits=script_hits or {},
            romanized_hits=romanized_hits or {},
        )