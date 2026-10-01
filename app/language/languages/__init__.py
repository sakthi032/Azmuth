"""Concrete language profiles and the default profile set."""

from __future__ import annotations

from app.language.languages.english import ENGLISH_PROFILE
from app.language.languages.tamil import TAMIL_PROFILE
from app.language.profiles import LanguageProfile


def build_default_profiles() -> dict[str, LanguageProfile]:
    """Return the profiles Azmuth ships with, keyed by language code.

    Adding a language is adding its profile here (or registering it at
    runtime through :meth:`LanguageManager.register_profile`). Nothing else
    in the language subsystem needs to change.
    """
    return {
        TAMIL_PROFILE.code: TAMIL_PROFILE,
        ENGLISH_PROFILE.code: ENGLISH_PROFILE,
    }


__all__ = [
    "ENGLISH_PROFILE",
    "TAMIL_PROFILE",
    "build_default_profiles",
]