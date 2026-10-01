"""Language subsystem façade.

The rest of Azmuth talks only to :class:`LanguageManager`. It wraps:

* the set of registered :class:`LanguageProfile` objects,
* a pluggable :class:`LanguageDetector`,
* the default language from :class:`Settings`,
* and the small policy decision "which language should we answer in?".

Per-conversation language preference is *not* stored here — that belongs to
the session (STEP 5). This manager is stateless with respect to any given
conversation.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.config.logging_setup import get_logger
from app.config.settings import Settings
from app.core.errors import LanguageError
from app.language.base import LanguageDetector
from app.language.detector import HeuristicLanguageDetector
from app.language.languages import build_default_profiles
from app.language.profiles import DetectionResult, LanguageCode, LanguageProfile

logger = get_logger("language")


def _normalise(code: str) -> str:
    """Reduce a caller-supplied tag to its primary subtag, lowercase.

    ``"en-US"`` → ``"en"``; ``"TA"`` → ``"ta"``; ``"und"`` → ``"und"``.
    """
    return code.strip().split("-", 1)[0].split("_", 1)[0].lower()


class LanguageManager:
    """Detect languages and decide response language."""

    def __init__(
        self,
        settings: Settings,
        detector: LanguageDetector | None = None,
        profiles: Iterable[LanguageProfile] | None = None,
    ) -> None:
        self._settings = settings
        self._detector = detector or HeuristicLanguageDetector()
        self._profiles: dict[str, LanguageProfile] = {}
        initial = (profiles if profiles is not None else build_default_profiles().values())
        for profile in initial:
            self._profiles[profile.code] = profile

        self._default_language = _normalise(settings.default_language)
        if self._default_language not in self._profiles:
            raise LanguageError(
                f"default language {self._default_language!r} has no profile; "
                f"registered: {sorted(self._profiles)}"
            )

    # -- introspection -----------------------------------------------------
    @property
    def default_language(self) -> str:
        return self._default_language

    @property
    def detector(self) -> LanguageDetector:
        return self._detector

    def supported_languages(self) -> tuple[str, ...]:
        return tuple(sorted(self._profiles))

    def is_supported(self, code: str) -> bool:
        return _normalise(code) in self._profiles

    def get_profile(self, code: str) -> LanguageProfile | None:
        return self._profiles.get(_normalise(code))

    def profile_for(self, code: str) -> LanguageProfile:
        """Like :meth:`get_profile`, but raises if the code is unknown."""
        normalised = _normalise(code)
        profile = self._profiles.get(normalised)
        if profile is None:
            raise LanguageError(
                f"no profile registered for language {code!r} "
                f"(normalised to {normalised!r})"
            )
        return profile

    # -- registration ------------------------------------------------------
    def register_profile(self, profile: LanguageProfile) -> None:
        """Add or replace a profile at runtime.

        Deliberately does not validate against ``Settings.supported_languages``:
        profiles are a superset that the manager may know about, while
        settings express *preference*. A profile registered here that is not
        in settings is simply never chosen as a default.
        """
        self._profiles[profile.code] = profile
        logger.info(
            "language profile registered",
            extra={"event": "LANGUAGE_PROFILE_REGISTERED", "code": profile.code},
        )

    # -- detection ---------------------------------------------------------
    def detect(self, text: str) -> DetectionResult:
        return self._detector.detect(text, self._profiles.values())

    def resolve_response_language(
        self, text: str, *, session_language: str | None = None
    ) -> str:
        """Pick the language Azmuth should respond in for the given input.

        Policy:

        * Unknown text → the session language if set, otherwise the default.
        * Single language detected → that language.
        * Mixed (e.g. Tanglish, or Tamil script + English words) → the
          session language if it is one of the detected ones (preserving an
          established conversation language), otherwise the dominant one.
        """
        result = self.detect(text)
        session = _normalise(session_language) if session_language else None

        if result.is_unknown:
            return session if session and session in self._profiles else self._default_language

        if result.is_mixed and session and session in result.detected:
            return session

        if result.primary in self._profiles:
            return result.primary

        # Defensive: a custom detector could report a code we have no profile
        # for. Fall back to the default rather than returning an unknown tag.
        return self._default_language


def build_language_manager(settings: Settings) -> LanguageManager:
    """Construct a manager using the built-in detector and profiles."""
    return LanguageManager(
        settings,
        detector=HeuristicLanguageDetector(),
        profiles=build_default_profiles().values(),
    )