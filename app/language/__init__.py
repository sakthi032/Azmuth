"""Language subsystem public API.

    from app.language import build_language_manager, LanguageManager

Never import from ``app.language.languages.*`` outside this package.
"""

from app.language.base import LanguageDetector
from app.language.detector import HeuristicLanguageDetector
from app.language.languages import ENGLISH_PROFILE, TAMIL_PROFILE, build_default_profiles
from app.language.manager import LanguageManager, build_language_manager
from app.language.profiles import DetectionResult, LanguageCode, LanguageProfile

__all__ = [
    "DetectionResult",
    "ENGLISH_PROFILE",
    "HeuristicLanguageDetector",
    "LanguageCode",
    "LanguageDetector",
    "LanguageManager",
    "LanguageProfile",
    "TAMIL_PROFILE",
    "build_default_profiles",
    "build_language_manager",
]