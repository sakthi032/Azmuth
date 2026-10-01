"""English language profile.

English has no distinguishing "romanized" list — its native script is Latin,
so script detection already covers it. The profile is script-only.
"""

from __future__ import annotations

from app.language.profiles import LanguageProfile

#: Basic Latin letters: A-Z / a-z.
_LATIN_SCRIPT: tuple[tuple[int, int], ...] = ((0x0041, 0x005A), (0x0061, 0x007A))

ENGLISH_PROFILE = LanguageProfile(
    code="en",
    display_name="English",
    native_name="English",
    script_ranges=_LATIN_SCRIPT,
    romanized_tokens=frozenset(),
    aliases=frozenset({"en-us", "en-gb", "en-in", "english"}),
)