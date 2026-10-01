
"""Decide whether a request needs external information."""

from __future__ import annotations

import re

_LIVE_KEYWORDS: frozenset[str] = frozenset(
    {
        "weather", "temperature", "forecast",
        "news", "headline", "breaking",
        "stock price", "share price", "market",
        "current price", "exchange rate",
        "today", "tonight", "yesterday", "tomorrow",
        "this week", "this month", "right now",
        "score", "match result", "election",
        "who is the current", "who won",
        "latest", "recent",
        "vanilai", "vaanilai", "vani", "vaana",
        "seythi", "seyithi",
        "innaiku", "innikku", "naetru", "nalai",
        "ippo", "ippothu",
    }
)

_LIVE_KEYWORDS_TAMIL: tuple[str, ...] = (
    "வானிலை",
    "செய்தி",
    "இன்று",
    "நேற்று",
    "நாளை",
    "இப்போது",
    "விலை",
    "மதிப்பெண்",
)

_WORD_SPLIT = re.compile(r"[\s\.,;:!\?\(\)\[\]\{\}]+")


def needs_search(text: str, *, session_language: str | None = None) -> bool:
    del session_language
    if not text or not text.strip():
        return False
    lowered = text.lower()
    if any(keyword in lowered for keyword in _LIVE_KEYWORDS):
        return True
    if any(keyword in text for keyword in _LIVE_KEYWORDS_TAMIL):
        return True
    return False


def keyword_hits(text: str) -> list[str]:
    if not text:
        return []
    lowered = text.lower()
    hits = [kw for kw in _LIVE_KEYWORDS if kw in lowered]
    hits.extend(kw for kw in _LIVE_KEYWORDS_TAMIL if kw in text)
    return hits
