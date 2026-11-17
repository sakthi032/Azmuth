"""BM25 first-stage retriever over Azmuth's SQLite memory backend.

Falls back gracefully if ``rank-bm25`` is not installed (returns empty list).

Usage::

    retriever = BM25Retriever(records)   # list[MemoryRecord]
    results = retriever.retrieve("query about Python", top_k=5)
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.memory.backends.sqlite_backend import MemoryRecord  # noqa: F401

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional rank-bm25
# ---------------------------------------------------------------------------
try:
    from rank_bm25 import BM25Okapi  # type: ignore[import-untyped]

    HAS_BM25 = True
except ModuleNotFoundError:
    HAS_BM25 = False
    logger.warning(
        "rank-bm25 not installed — BM25Retriever will return empty results. "
        "Install with: pip install rank-bm25"
    )


def _tokenize(text: str) -> list[str]:
    """Lowercase whitespace-split tokeniser (no extra deps)."""
    return text.lower().split()


class BM25Retriever:
    """Thread-safe BM25 retriever that indexes a list of MemoryRecord objects.

    Parameters
    ----------
    records:
        Initial list of records to index. Call :meth:`rebuild` to update.
    """

    def __init__(self, records: "list[MemoryRecord] | None" = None) -> None:
        self._lock = threading.RLock()
        self._records: list[Any] = []
        self._index: "BM25Okapi | None" = None
        if records:
            self.rebuild(records)

    def rebuild(self, records: "list[MemoryRecord]") -> None:
        """Re-index a new set of records.  Thread-safe."""
        if not HAS_BM25:
            return
        with self._lock:
            self._records = list(records)
            corpus = [_tokenize(r.content) for r in self._records]
            self._index = BM25Okapi(corpus) if corpus else None

    def retrieve(
        self,
        query: str,
        top_k: int = 10,
    ) -> "list[MemoryRecord]":
        """Return up to ``top_k`` records ranked by BM25 score.

        Returns an empty list if ``rank-bm25`` is not installed or the
        index is empty.
        """
        if not HAS_BM25 or self._index is None:
            return []

        with self._lock:
            tokens = _tokenize(query)
            scores = self._index.get_scores(tokens)
            ranked = sorted(
                zip(scores, self._records),
                key=lambda x: x[0],
                reverse=True,
            )
            return [rec for score, rec in ranked[:top_k] if score > 0]


# Needed for TYPE_CHECKING guard above when running without full app installed
from typing import Any
