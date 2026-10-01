"""Tamil language profile."""

from __future__ import annotations

from app.language.profiles import LanguageProfile

#: Tamil Unicode block: U+0B80 – U+0BFF.
_TAMIL_SCRIPT: tuple[tuple[int, int], ...] = ((0x0B80, 0x0BFF),)

#: Common Tanglish words. Deliberately word-level only — including suffixes
#: like "da"/"di"/"la" would cause too many false positives on English text.
#: This list is expected to grow as real usage is observed; the detector does
#: not need to change when it does.
_TANGLISH_TOKENS: frozenset[str] = frozenset(
    {
        "aama", "aamaa", "adhu",
        "enna", "ennaa", "ennada", "ennanga", "epdi", "eppadi", "eppdi",
        "ethu", "edhu", "enga", "engey",
        "illa", "illai", "ille",
        "konjam",
        "mudiyala", "mudiyadhu", "mudiyum",
        "nalla", "nallaa", "nandri",
        "paaru", "paarunga", "paatha", "pannu", "panna", "pannunga",
        "panra", "panren", "panni",
        "podu", "podhum", "poidu", "poidalam", "pothum",
        "poi", "puriyala", "puriyadhu", "puriyum",
        "romba", "rombha",
        "sari", "seri",
        "sollu", "sollunga", "sonna",
        "theriyala", "theriyadhu", "theriyum",
        "vaa", "vaanga", "vanakkam", "vanakam", "vanthu", "vendam", "venaam",
        "yaar", "yaaru",
    }
)

TAMIL_PROFILE = LanguageProfile(
    code="ta",
    display_name="Tamil",
    native_name="தமிழ்",
    script_ranges=_TAMIL_SCRIPT,
    romanized_tokens=_TANGLISH_TOKENS,
    aliases=frozenset({"ta-in", "ta-lk", "ta-my", "tamil", "tanglish"}),
)