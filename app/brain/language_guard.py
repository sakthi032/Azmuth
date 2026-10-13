
"""Output guard against fabricated scripts.

When a model tries to write Tamil but its training data is thin, it
sometimes invents characters by mixing scripts. The output looks Tamil
from a distance but is not — it is Bengali, Devanagari, Telugu, and
Japanese characters scrambled together.

This guard catches that. It never rewrites text; it either accepts the
response unchanged or signals the pipeline to fall back to English.
"""

from __future__ import annotations

#: Unicode ranges for scripts that should NOT appear in a Tamil reply.
_OTHER_SCRIPTS: dict[str, tuple[int, int]] = {
    "bengali": (0x0980, 0x09FF),
    "devanagari": (0x0900, 0x097F),
    "telugu": (0x0C00, 0x0C7F),
    "kannada": (0x0C80, 0x0CFF),
    "malayalam": (0x0D00, 0x0D7F),
    "sinhala": (0x0D80, 0x0DFF),
    "cjk_unified": (0x4E00, 0x9FFF),
    "hiragana": (0x3040, 0x309F),
    "katakana": (0x30A0, 0x30FF),
    "hangul": (0xAC00, 0xD7AF),
    "thai": (0x0E00, 0x0E7F),
    "arabic": (0x0600, 0x06FF),
    "hebrew": (0x0590, 0x05FF),
}

_TAMIL_RANGE = (0x0B80, 0x0BFF)
_LATIN_RANGE = (0x0041, 0x007A)


def _classify(ch: str) -> str:
    cp = ord(ch)
    if _TAMIL_RANGE[0] <= cp <= _TAMIL_RANGE[1]:
        return "tamil"
    if _LATIN_RANGE[0] <= cp <= _LATIN_RANGE[1]:
        return "latin"
    for name, (start, end) in _OTHER_SCRIPTS.items():
        if start <= cp <= end:
            return name
    return "other"


def is_suspect_response(text: str, *, expected_language: str | None) -> bool:
    """True if the response is likely fabricated Tamil.

    Rules:
    - If we are not expecting Tamil, only flag when *many* foreign script
      characters appear. A single Korean word in an English reply is
      usually fine (a name, a quote).
    - If we ARE expecting Tamil, flag when Tamil characters appear
      alongside characters from two or more other non-Latin scripts.
      Real Tamil text never mixes three scripts in one paragraph.
    """
    if not text:
        return False

    counts: dict[str, int] = {}
    for ch in text:
        if not ch.isalpha():
            continue
        kind = _classify(ch)
        counts[kind] = counts.get(kind, 0) + 1

    foreign_scripts = {
        name: n for name, n in counts.items()
        if name in _OTHER_SCRIPTS and n >= 3
    }

    if expected_language == "ta":
        # Two or more foreign scripts alongside Tamil = fabrication.
        tamil_count = counts.get("tamil", 0)
        if tamil_count >= 3 and len(foreign_scripts) >= 2:
            return True
        # A single foreign script with many chars is still suspicious.
        return len(foreign_scripts) >= 2

    # Not expecting Tamil: only flag heavy contamination.
    return sum(foreign_scripts.values()) >= 15


def fallback_message(*, expected_language: str | None) -> str:
    if expected_language == "ta":
        return (
            "மன்னிக்கவும், இந்த பதிலை தமிழில் துல்லியமாக உருவாக்க முடியவில்லை. "
            "English-ல் சொல்கிறேன்:\n\n"
            "(The model could not produce clean Tamil for this reply. "
            "Please try again, or ask in English.)"
        )
    return (
        "Sorry — I could not produce a clean response in the requested "
        "language. Please try again."
    )


__all__ = ["fallback_message", "is_suspect_response"]
