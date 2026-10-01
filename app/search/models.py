
"""Search data types.

A :class:`SearchResult` is what a provider returns for one hit. A
:class:`SearchResponse` is what the manager hands to the brain - it
bundles the *original* results (structured) with the *sanitised
reference block* (the single string the LLM will actually see as
untrusted context).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """A single search request."""

    text: str
    max_results: int = 5
    language: str | None = None
    region: str | None = None
    safe_search: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        text = self.text.strip()
        if not text:
            raise ValueError("search query text must be non-empty")
        object.__setattr__(self, "text", text)
        if self.max_results < 1:
            raise ValueError("max_results must be >= 1")
        if self.max_results > 50:
            raise ValueError("max_results must be <= 50")


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One hit from a provider."""

    title: str
    url: str
    snippet: str
    source: str
    rank: int = 0
    retrieved_at: datetime = field(default_factory=_now)
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
            "rank": self.rank,
            "retrieved_at": self.retrieved_at.isoformat(),
            "extra": dict(self.extra),
        }


@dataclass(frozen=True, slots=True)
class SearchResponse:
    """The manager's answer to a search request."""

    query: SearchQuery
    results: tuple[SearchResult, ...]
    provider: str
    reference_block: str
    has_reference: bool = False
    elapsed_ms: float = 0.0
    error: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.results

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query.text,
            "provider": self.provider,
            "results": [r.as_dict() for r in self.results],
            "reference_block": self.reference_block,
            "has_reference": self.has_reference,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "error": self.error,
        }
