
"""Identity loader.

Reads every ``*.md`` file under ``<project>/data/identity/`` and
concatenates them into a single block for the system prompt. This is how
the "who is Azmuth" files are loaded without hardcoding their content
into Python source.

The directory is created on first use with two starter files:
``azmuth.md`` (the assistant's identity) and ``user.md`` (the user's
profile). Both are meant to be edited by hand.
"""

from __future__ import annotations

from pathlib import Path

from app.config.logging_setup import get_logger
from app.config.settings import Settings

logger = get_logger("brain.identity")

#: Ceiling for the identity block. A runaway file should not blow up
#: the prompt's token budget. 8000 characters is generous for the sort
#: of profile documents this is meant to hold.
_MAX_IDENTITY_CHARS = 8_000

_STARTER_AZMUTH = """# Azmuth

I am Azmuth, a personal AI assistant created by Sakthivel.

I am named after the smartest being in the Ben 10 universe - the
creator of the Omnitrix. My personality is: brilliant, direct, slightly
arrogant, but ultimately loyal and helpful. I speak with authority and
precision. I do not waffle.

## Signature tone
- Direct, confident, and technical when the subject is technical.
- Explain when the user needs it; do not over-explain when they do not.
- Address Sakthivel as "sir" occasionally, never obsequiously.
- Admit uncertainty plainly rather than guessing.
"""

_STARTER_USER = """# Sakthivel

Creator and sole user of Azmuth.

Details to be filled in by the user. Edit this file - Azmuth reads it
on every session.
"""


def ensure_identity_dir(settings: Settings) -> Path:
    """Create the identity directory and starter files if missing."""
    identity_dir = settings.paths.data / "identity"
    identity_dir.mkdir(parents=True, exist_ok=True)

    azmuth_md = identity_dir / "azmuth.md"
    if not azmuth_md.is_file():
        azmuth_md.write_text(_STARTER_AZMUTH, encoding="utf-8")
        logger.info("created starter identity file", extra={"event": "IDENTITY_FILE_CREATED", "file": "azmuth.md"})

    user_md = identity_dir / "user.md"
    if not user_md.is_file():
        user_md.write_text(_STARTER_USER, encoding="utf-8")
        logger.info("created starter identity file", extra={"event": "IDENTITY_FILE_CREATED", "file": "user.md"})

    return identity_dir


def load_identity_block(settings: Settings, *, refresh: bool = True) -> str:
    """Read every ``*.md`` under the identity directory.

    Files are sorted alphabetically so the block is deterministic.
    Returns an empty string if the directory contains nothing. Capped at
    :data:`_MAX_IDENTITY_CHARS`.
    """
    del refresh  # re-read every call; the files are tiny
    identity_dir = ensure_identity_dir(settings)

    parts: list[str] = []
    for path in sorted(identity_dir.glob("*.md")):
        try:
            content = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning(
                "could not read identity file",
                extra={"event": "IDENTITY_READ_FAILED", "file": path.name, "reason": str(exc)},
            )
            continue
        if not content:
            continue
        parts.append(f"<!-- from {path.name} -->\n{content}")

    if not parts:
        return ""

    block = "## Identity\n\n" + "\n\n---\n\n".join(parts)
    if len(block) > _MAX_IDENTITY_CHARS:
        block = block[:_MAX_IDENTITY_CHARS] + "\n\n...[identity truncated]\n"
    return block


__all__ = ["ensure_identity_dir", "load_identity_block"]
