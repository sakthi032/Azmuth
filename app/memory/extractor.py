"""Rule-based fact extractor for personal memory.

Produces atomic, clean facts like ``"Name: Sakthi"`` and
``"Likes: dark mode"`` instead of storing raw sentences. The output is
deterministic, synchronous, and testable — no LLM dependency.

Design
------
* Each rule matches one fact type (name, likes, employer, role, …).
* The captured value is cleaned (greetings stripped, trailing noise
  like ``"preference"`` removed).
* The stored content is ``"{Label}: {value}"`` so retrieval and display
  are unambiguous.
* Secrets are rejected before storage — a label like ``"password"`` or
  a value that looks like an API token is dropped.
* Conflicts: if a fact of the same label already exists for this user,
  the old record is deleted and the new one is stored (latest wins).

The FAST-model extraction path (strict JSON) is a planned enhancement.
Callers can pass ``llm=None`` today and rule-based runs; when the
pipeline wires an LLM through, rule-based becomes the fallback.
"""

from __future__ import annotations

import re

from app.config.logging_setup import get_logger
from app.memory.manager import MemoryManager
from app.memory.models import MemoryKind

logger = get_logger("memory.extractor")

_GREETING_PREFIX = re.compile(
    r"^(?:hi|hello|hey|hai|vanakkam|good morning|good evening|good night)"
    r"[,.!\s]+",
    re.IGNORECASE,
)

_TRAILING_NOISE = re.compile(
    r"\s+(?:preference|preferences|please|thanks|thank you|"
    r"only|actually)[.!?]*$",
    re.IGNORECASE,
)

_SECRET_LABELS = frozenset({
    "password", "passwd", "pwd", "secret", "token", "api", "apikey",
    "api_key", "access_key", "auth", "bearer", "cvv", "ssn", "pin",
    "credit", "debit",
})

_SECRET_VALUE = re.compile(
    r"\b(?:password|passwd|api[_ ]?key|secret|token|bearer|"
    r"access[_ ]?key|auth[_ ]?token)\b",
    re.IGNORECASE,
)

_OPAQUE_STRING = re.compile(r"\b[A-Za-z0-9+/=_-]{32,}\b")

# (pattern, fixed_label_or_None, kind)
_FACT_RULES: list[tuple[re.Pattern[str], str | None, MemoryKind]] = [
    # Name
    (
        re.compile(
            r"\bmy name is\s+([A-Za-z][A-Za-z' .-]*?)"
            r"(?:[,.]|\s+and\s+|$)",
            re.IGNORECASE,
        ),
        "Name",
        MemoryKind.FACT,
    ),
    (
        re.compile(
            r"\bi(?:'m| am) called\s+([A-Za-z][A-Za-z' .-]*?)"
            r"(?:[,.]|\s+and\s+|$)",
            re.IGNORECASE,
        ),
        "Name",
        MemoryKind.FACT,
    ),
    (
        re.compile(
            r"\bcall me\s+([A-Za-z][A-Za-z' .-]*?)"
            r"(?:[,.]|\s+and\s+|$)",
            re.IGNORECASE,
        ),
        "Name",
        MemoryKind.FACT,
    ),
    # Likes
    (
        re.compile(
            r"\bi (?:like|love|enjoy|prefer)\s+(.+?)"
            r"(?:[,.]|\s+and\s+|$)",
            re.IGNORECASE,
        ),
        "Likes",
        MemoryKind.PREFERENCE,
    ),
    # Dislikes
    (
        re.compile(
            r"\bi (?:hate|dislike|don't like|do not like)\s+(.+?)"
            r"(?:[,.]|\s+and\s+|$)",
            re.IGNORECASE,
        ),
        "Dislikes",
        MemoryKind.PREFERENCE,
    ),
    # Employer
    (
        re.compile(
            r"\bi work (?:at|for)\s+(.+?)(?:[,.]|\s+and\s+|$)",
            re.IGNORECASE,
        ),
        "Works at",
        MemoryKind.FACT,
    ),
    # Role
    (
        re.compile(
            r"\bi work as (?:a |an )?(.+?)(?:[,.]|\s+and\s+|$)",
            re.IGNORECASE,
        ),
        "Role",
        MemoryKind.FACT,
    ),
    (
        re.compile(
            r"\bi(?:'m| am) (?:a|an) (.+?)(?:[,.]|\s+and\s+|$)",
            re.IGNORECASE,
        ),
        "Role",
        MemoryKind.FACT,
    ),
    # City
    (
        re.compile(
            r"\bi (?:live|stay|reside) in\s+([A-Za-z][A-Za-z' .-]*?)"
            r"(?:[,.]|\s+and\s+|$)",
            re.IGNORECASE,
        ),
        "City",
        MemoryKind.FACT,
    ),
    # Generic "my X is Y"
    (
        re.compile(
            r"\bmy (\w+) is\s+(.+?)(?:[,.]|\s+and\s+|$)",
            re.IGNORECASE,
        ),
        None,  # Label derived from group(1)
        MemoryKind.FACT,
    ),
]


def _is_secret_label(label: str) -> bool:
    return label.strip().lower().replace(" ", "_") in _SECRET_LABELS


def _looks_like_secret(value: str) -> bool:
    return bool(
        _SECRET_VALUE.search(value) or _OPAQUE_STRING.search(value)
    )


def _clean_value(raw: str) -> str:
    value = raw.strip()
    value = _GREETING_PREFIX.sub("", value)
    value = _TRAILING_NOISE.sub("", value)
    value = re.sub(r"^(?:a|an|the)\s+", "", value, flags=re.IGNORECASE)
    return value.strip(" ,.;!?")


def _extract_pairs(text: str) -> list[tuple[str, str, MemoryKind]]:
    """Return [(label, value, kind), …] from ``text``."""
    if not text:
        return []
    out: list[tuple[str, str, MemoryKind]] = []
    seen: set[tuple[str, str]] = set()

    for pattern, fixed_label, kind in _FACT_RULES:
        for m in pattern.finditer(text):
            if fixed_label is None:
                label = m.group(1).strip().capitalize()
                raw_value = m.group(2)
            else:
                label = fixed_label
                raw_value = m.group(1)

            if _is_secret_label(label):
                logger.info(
                    "secret-shaped label skipped",
                    extra={
                        "event": "EXTRACT_SECRET_SKIPPED",
                        "label": label,
                    },
                )
                continue

            value = _clean_value(raw_value)
            if not value or len(value) > 200:
                continue
            if _looks_like_secret(value):
                logger.info(
                    "secret-shaped value skipped",
                    extra={
                        "event": "EXTRACT_SECRET_SKIPPED",
                        "label": label,
                    },
                )
                continue

            key = (label, value.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append((label, value, kind))

    return out


def extract_facts(
    text: str,
    *,
    user_id: str,
    memory: MemoryManager,
    llm=None,
) -> int:
    """Extract and store atomic personal facts. Returns count stored.

    ``llm`` is accepted for future FAST-model extraction but is unused
    today — rule-based covers the common fact forms deterministically.
    """
    if not text or not user_id:
        return 0

    logger.info(
        "extract_facts called",
        extra={
            "event": "EXTRACT_CALLED",
            "user_id": user_id,
            "text_len": len(text),
        },
    )

    pairs = _extract_pairs(text)
    if not pairs:
        return 0

    stored = 0
    for label, value, kind in pairs:
        new_content = f"{label}: {value}"
        # Conflict check: any existing fact with the same label?
        try:
            candidates = memory.search(
                f"{label}:", user_id=user_id, limit=20,
            )
        except Exception:
            logger.exception("extractor search failed")
            candidates = []

        conflicts = [
            r for r in candidates
            if r.content.startswith(f"{label}:")
        ]

        # Skip exact duplicate
        if any(r.content.strip() == new_content for r in conflicts):
            continue

        # Latest wins: drop conflicting older records for the same label
        for old in conflicts:
            try:
                memory.delete(old.id)
            except Exception:
                logger.exception(
                    "extractor conflict delete failed",
                    extra={
                        "event": "EXTRACT_CONFLICT_DELETE_FAILED",
                        "old_id": old.id,
                    },
                )

        try:
            memory.store(
                new_content,
                user_id=user_id,
                kind=kind,
                importance=0.85 if label == "Name" else 0.7,
            )
            stored += 1
        except Exception:
            logger.exception(
                "extractor store failed",
                extra={
                    "event": "EXTRACT_STORE_FAILED",
                    "content": new_content,
                },
            )

    logger.info(
        "extract_facts result",
        extra={
            "event": "EXTRACT_RESULT",
            "user_id": user_id,
            "count": stored,
        },
    )
    return stored