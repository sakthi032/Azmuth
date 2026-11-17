"""Edge TTS provider — high-quality cloud voices from Microsoft.

No API key. Requires internet. Supports en-IN (Indian English) and ta-IN
(Tamil) voices. Playback via pygame.mixer (MP3).

On network failure, returns False without raising — the manager's
invariant (never lose the text) still holds.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

from app.voice.tts.base import (
    SynthesisResult,
    TextToSpeechProvider,
    VoiceGender,
    VoiceProfile,
)

# Suppress pygame's startup banner before it can print it.
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

logger = logging.getLogger(__name__)


_VOICES: tuple[VoiceProfile, ...] = (
    VoiceProfile(
        "en-IN-NeerjaNeural", "Neerja (India English, F)",
        "en", VoiceGender.FEMININE,
    ),
    VoiceProfile(
        "en-IN-PrabhatNeural", "Prabhat (India English, M)",
        "en", VoiceGender.MASCULINE,
    ),
    VoiceProfile(
        "en-US-AriaNeural", "Aria (US English, F)",
        "en", VoiceGender.FEMININE,
    ),
    VoiceProfile(
        "ta-IN-PallaviNeural", "Pallavi (Tamil, F)",
        "ta", VoiceGender.FEMININE,
    ),
    VoiceProfile(
        "ta-IN-ValluvarNeural", "Valluvar (Tamil, M)",
        "ta", VoiceGender.MASCULINE,
    ),
)

_DEFAULT_VOICE_BY_LANG = {
    "en": "en-GB-MaisieNeural",      # ← was en-IN-NeerjaNeural
    "ta": "ta-IN-PallaviNeural",     # keep for Tamil
}

#: Tamil renderings of the assistant's name that must be corrected
#: before speech. The model sometimes generates alternative spellings;
#: hard-replace them at the TTS boundary so the voice always sounds
#: right regardless of what text was produced.
_NAME_FIXUPS: tuple[tuple[str, str], ...] = (
    ("அஸ்முத்", "ஆஸ்மத்"),
    ("அஸ்மத்", "ஆஸ்மத்"),
    ("ஆஸ்முத்", "ஆஸ்மத்"),
    ("அஜ்முத்", "ஆஸ்மத்"),
    ("அஜ்மத்", "ஆஸ்மத்"),
)


def _fix_tamil_names(text: str) -> str:
    """Replace alternative Tamil spellings of Azmuth with the canonical
    form before speech synthesis."""
    for wrong, right in _NAME_FIXUPS:
        text = text.replace(wrong, right)
    return text


class EdgeTTSProvider(TextToSpeechProvider):
    """Microsoft Edge cloud TTS via edge-tts."""

    name = "edge_tts"

    def __init__(
        self,
        *,
        default_language: str = "en",
        rate: str = "+0%",
        volume: str = "+0%",
    ) -> None:
        self._default_language = default_language
        self._rate = rate
        self._volume = volume

        # Pre-warm the mixer so first playback isn't delayed.
        try:
            import pygame
            if not pygame.mixer.get_init():
                pygame.mixer.init(
                    frequency=44100, size=-16, channels=2
                )
        except Exception:
            # _play_mp3 will retry with a clear error if needed.
            pass

    # -- probes ------------------------------------------------------------
    def is_available(self) -> bool:
        try:
            import edge_tts  # noqa: F401
            return True
        except ImportError:
            return False

    def list_voices(
        self, language: str | None = None
    ) -> tuple[VoiceProfile, ...]:
        if language is None:
            return _VOICES
        primary = language.split("-")[0].lower()
        return tuple(v for v in _VOICES if v.language == primary)

    def supports_language(self, language: str) -> bool:
        if not language:
            return False
        primary = language.split("-")[0].lower()
        return any(v.language == primary for v in _VOICES)

    # -- synthesis ---------------------------------------------------------
    async def synthesize(
        self,
        text: str,
        *,
        language: str | None = None,
        voice_id: str | None = None,
    ) -> SynthesisResult:
        # edge-tts outputs MP3, not PCM. We don't convert MP3 → AudioFrame
        # here because that needs an extra dependency. `speak` handles
        # playback directly. `synthesize` returns a placeholder.
        return SynthesisResult(
            text=text,
            audio=None,
            provider=self.name,
            language=language,
            voice_id=voice_id or self._pick_voice(language, voice_id),
            error=None,
        )

    @staticmethod
    def stop_playback() -> None:
        """Stop any in-progress MP3 playback. Called by SpeechQueue on
        cancel — the user sent a new message before the old answer
        finished."""
        try:
            import pygame
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
        except Exception:
            pass

    async def speak(
        self,
        text: str,
        *,
        language: str | None = None,
        voice_id: str | None = None,
    ) -> bool:
        text = _clean_for_speech(text)
        text = _fix_tamil_names(text)     # ← ADD THIS
        if not text:
            return False
        try:
            import edge_tts
        except ImportError:
            logger.warning("edge-tts not installed")
            return False

        voice = self._pick_voice(language, voice_id)
        if not voice:
            return False

        chunks = self._split_sentences(text)
        any_played = False
        for chunk in chunks:
            played = await self._speak_one(
                chunk, voice, edge_tts
            )
            any_played = any_played or played
        return any_played

    async def _speak_one(
        self, text: str, voice: str, edge_tts_module
    ) -> bool:
        path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".mp3", delete=False
            ) as f:
                path = Path(f.name)

            communicate = edge_tts_module.Communicate(
                text, voice, rate=self._rate, volume=self._volume
            )
            await communicate.save(str(path))

            return await asyncio.to_thread(self._play_mp3, path)
        except Exception:
            logger.exception("edge-tts chunk speak failed")
            return False
        finally:
            if path is not None:
                try:
                    path.unlink()
                except Exception:
                    pass

    @staticmethod
    def _split_sentences(text: str, max_chars: int = 240) -> list[str]:
        """Split text into sentence-sized chunks for chunked TTS.

        Splits on sentence boundaries first, then packs small sentences
        together up to ``max_chars`` to avoid one-word audio clips.
        """
        import re

        # Sentence boundary: [.!?] followed by space+capital, or newline.
        parts = re.split(r"(?<=[.!?])\s+|\n+", text)
        parts = [p.strip() for p in parts if p.strip()]

        chunks: list[str] = []
        buf = ""
        for p in parts:
            if not buf:
                buf = p
            elif len(buf) + 1 + len(p) <= max_chars:
                buf = buf + " " + p
            else:
                chunks.append(buf)
                buf = p
        if buf:
            chunks.append(buf)

        return chunks or [text]

    # -- internals ---------------------------------------------------------
    def _pick_voice(
        self, language: str | None, voice_id: str | None
    ) -> str | None:
        import os

        # Explicit per-call voice always wins.
        if voice_id:
            return voice_id

        lang = (language or self._default_language).split("-")[0].lower()

        # Per-language env override: TTS_VOICE_EN, TTS_VOICE_TA, ...
        per_lang_env = os.getenv(
            f"TTS_VOICE_{lang.upper()}", ""
        ).strip()
        if per_lang_env:
            return per_lang_env

        # For non-English languages, use the language default. This
        # prevents TTS_VOICE (usually set for a specific English voice)
        # from mangling Tamil, Hindi, Arabic, etc. by reading their
        # scripts with English phonemes.
        if lang != "en":
            default_for_lang = _DEFAULT_VOICE_BY_LANG.get(lang)
            if default_for_lang:
                return default_for_lang

        # English (or languages with no default) uses TTS_VOICE.
        env_voice = os.getenv("TTS_VOICE", "").strip()
        if env_voice:
            return env_voice

        return (
            _DEFAULT_VOICE_BY_LANG.get(lang)
            or _DEFAULT_VOICE_BY_LANG["en"]
        )

    @staticmethod
    def _play_mp3(path: Path) -> bool:
        """Play an MP3 through pygame.mixer.

        winsound.PlaySound only handles WAV, so MP3s need a real decoder.
        pygame.mixer is the most reliable cross-Python-version option.
        """
        try:
            import pygame
        except ImportError:
            logger.warning(
                "pygame not installed — cannot play MP3. "
                "Run: pip install pygame"
            )
            return False

        try:
            # Init once per process.
            if not pygame.mixer.get_init():
                pygame.mixer.init(
                    frequency=44100, size=-16, channels=2
                )

            pygame.mixer.music.load(str(path))
            pygame.mixer.music.play()
            # Block until playback finishes.
            clock = pygame.time.Clock()
            while pygame.mixer.music.get_busy():
                clock.tick(20)
            pygame.mixer.music.unload()
            return True
        except Exception:
            logger.exception("MP3 playback via pygame failed")
            return False


import re as _re
import unicodedata as _ud


#: Markdown structure — cannot be handled by unicode categories because
#: markdown characters overlap with natural punctuation (e.g. ``.`` is
#: both sentence-final and a markdown link separator). These regexes
#: strip the *grammar* of markdown; the category filter handles the rest.
_CODE_FENCE   = _re.compile(r"```[\s\S]*?```")
_INLINE_CODE  = _re.compile(r"`([^`\n]+)`")
_BOLD         = _re.compile(r"\*\*(.+?)\*\*", _re.DOTALL)
_ITALIC_STAR  = _re.compile(r"(?<!\*)\*(?!\*)([^*\n]+?)(?<!\*)\*(?!\*)")
_ITALIC_UNDER = _re.compile(r"(?<!\w)_([^_\n]+?)_(?!\w)")
_HEADER       = _re.compile(r"^#{1,6}\s+", _re.MULTILINE)
_BULLET       = _re.compile(r"^\s*[-*+]\s+", _re.MULTILINE)
_NUMBERED     = _re.compile(r"^\s*\d+\.\s+", _re.MULTILINE)
_MD_LINK      = _re.compile(r"\[([^\]]+)\]\([^)]+\)")
_URL          = _re.compile(r"https?://\S+")

#: Punctuation that means something when spoken (pause / intonation).
#: Everything else in the P* categories is dropped. This is the ONLY
#: hardcoded character set in the cleaner — it is unavoidable because
#: Unicode does not distinguish "period that ends a sentence" from
#: "period in a URL or ellipsis" — that's semantic, not syntactic.
_SPEECH_PUNCT = frozenset(".,?!;:'\"")

#: Collapse runs of identical punctuation after filtering.
_PUNCT_RUNS   = _re.compile(r"([.,?!;:])\1+")


def _clean_for_speech(text: str) -> str:
    """Strip markdown and non-speech content.

    Two-phase approach:

    Phase 1 — strip markdown grammar (code fences, emphasis, headers,
    links, URLs). Regex is necessary here: markdown chars share
    Unicode categories with natural punctuation, so no category rule
    can tell ``**bold**`` from ``5 * 3``. The regexes know markdown
    syntax; the category filter handles what's left.

    Phase 2 — unicode category filter. Every remaining character is
    classified by ``unicodedata.category`` and either kept (letters,
    digits, marks, natural punctuation), converted to whitespace
    (line breaks, brackets, dashes), or dropped (emoji, math symbols,
    currency, arrows, control chars). No per-character mapping.
    """
    if not text:
        return ""

    # ---- Phase 1: markdown structure ----
    text = _CODE_FENCE.sub(" (code block omitted) ", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _BOLD.sub(r"\1", text)
    text = _ITALIC_STAR.sub(r"\1", text)
    text = _ITALIC_UNDER.sub(r"\1", text)
    text = _HEADER.sub("", text)
    text = _BULLET.sub("", text)
    text = _NUMBERED.sub("", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _URL.sub(" (link) ", text)

    # ---- Phase 2: unicode category filter ----
    out: list[str] = []
    for ch in text:
        cat = _ud.category(ch)
        major = cat[0]

        if major in ("L", "N", "M"):
            # Letters, numbers, combining marks — always speak.
            # Includes ALL scripts (Latin, Tamil, Arabic, CJK, ...).
            out.append(ch)

        elif cat == "Zs":
            # Space separator.
            out.append(" ")

        elif cat in ("Zl", "Zp"):
            # Line or paragraph separator — sentence break.
            out.append(". ")

        elif cat in ("Ps", "Pe", "Pi", "Pf", "Pc", "Pd"):
            # Brackets, quotes-initial/final, connectors, dashes —
            # not spoken. Space prevents adjacent words merging.
            out.append(" ")

        elif major == "P":
            # Other punctuation (Po): keep the natural set, drop the rest
            # (asterisks, hashes, pipes, backslashes, tildes, etc.).
            out.append(ch if ch in _SPEECH_PUNCT else " ")

        elif major == "S":
            # Symbols: emoji, math, currency, arrows, modifiers, etc.
            # All dropped automatically. Space keeps words separated.
            out.append(" ")

        elif cat in ("Cc", "Cf"):
            # Control / format chars.
            if ch == "\n":
                out.append(". ")
            elif ch == "\t":
                out.append(" ")
            # Others (NUL, ZWJ, BOM, soft hyphen) drop silently.

        else:
            # Surrogates, private use, etc. — drop.
            pass

    result = "".join(out)

    # ---- Phase 3: cleanup ----
    # "word ." → "word."   (space before sentence punctuation)
    result = _re.sub(r"\s+([.,?!;:])", r"\1", result)
    # "..." → "."   (collapse runs; prevents "dot dot dot" reading)
    result = _PUNCT_RUNS.sub(r"\1", result)
    # Collapse whitespace
    result = _re.sub(r"\s+", " ", result).strip()

    return result

__all__ = ["EdgeTTSProvider"]