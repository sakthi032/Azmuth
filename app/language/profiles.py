"""Language data types.

A :class:`LanguageProfile` is *pure data*. Adding a language is adding a
profile — no branches, no per-language functions, no changes to the detector
or manager. This is what keeps the architecture extensible for languages we
have not thought of yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class LanguageCode(str, Enum):
    """Canonical BCP-47 primary subtags used inside Azmuth.

    Subclassing ``str`` keeps values JSON-friendly. Only the languages
    Azmuth gives a special name to appear here; everything else is still
    represented as a plain two-letter string.
    """

    TAMIL = "ta"
    ENGLISH = "en"
    UNKNOWN = "und"  # ISO 639-3 code for "undetermined"


@dataclass(frozen=True, slots=True)
class LanguageProfile:
    """Everything the system needs to know about one language.

    Attributes:
        code: BCP-47 primary subtag, lowercase (``"ta"``, ``"en"``, …).
        display_name: English name, for logs and UI.
        native_name: The language's own name, for UI.
        script_ranges: Inclusive Unicode codepoint ranges this language's
            native script occupies. Used for script-based detection.
        romanized_tokens: Lowercase words commonly used when the language is
            written with the Latin alphabet (e.g. Tanglish for Tamil). May be
            empty — script-only languages simply leave it empty.
        aliases: Additional codes to accept from callers
            (e.g. ``("ta-in", "tamil")``).
        rtl: True for right-to-left scripts.
    """

    code: str
    display_name: str
    native_name: str
    script_ranges: tuple[tuple[int, int], ...] = field(default_factory=tuple)
    romanized_tokens: frozenset[str] = field(default_factory=frozenset)
    aliases: frozenset[str] = field(default_factory=frozenset)
    rtl: bool = False

    def __post_init__(self) -> None:
        code = self.code.strip().lower()
        if not code or len(code) > 8:
            raise ValueError(f"invalid language code: {self.code!r}")
        object.__setattr__(self, "code", code)

    def matches_codepoint(self, cp: int) -> bool:
        """True if ``cp`` belongs to this language's native script."""
        return any(start <= cp <= end for start, end in self.script_ranges)


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """Outcome of a detection pass.

    ``primary`` is the best guess. ``detected`` contains every language the
    text showed evidence of, ordered strongest-first — so a Tanglish
    sentence can legitimately have both ``"en"`` and ``"ta"``. ``is_mixed``
    is a shortcut for ``len(detected) > 1``.
    """

    primary: str
    detected: tuple[str, ...]
    is_mixed: bool
    confidence: float
    script_hits: dict[str, int] = field(default_factory=dict)
    romanized_hits: dict[str, int] = field(default_factory=dict)

    @property
    def is_unknown(self) -> bool:
        return self.primary == LanguageCode.UNKNOWN.value

    def as_dict(self) -> dict[str, object]:
        return {
            "primary": self.primary,
            "detected": list(self.detected),
            "is_mixed": self.is_mixed,
            "confidence": round(self.confidence, 3),
            "script_hits": dict(self.script_hits),
            "romanized_hits": dict(self.romanized_hits),
        }