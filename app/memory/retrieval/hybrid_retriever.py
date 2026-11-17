"""Hybrid retriever: BM25 (keyword) + ChromaDB (semantic) fusion.

Strategy
--------
1. Run BM25 on the SQLite-backed record store   → keyword candidates
2. Run ChromaDB vector search                    → semantic candidates
3. Merge by Reciprocal Rank Fusion (RRF)
4. Optional ColBERT reranking (requires ``colbert-ai``)

Usage::

    retriever = HybridRetriever(
        bm25_retriever=bm25,
        chroma_store=chroma,
    )
    results = await retriever.retrieve("my query", top_k=5)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional ColBERT reranker
# ---------------------------------------------------------------------------
try:
    from colbert.infra import Run, RunConfig  # type: ignore[import-untyped]
    from colbert.modeling.checkpoint import Checkpoint  # type: ignore[import-untyped]

    HAS_COLBERT = True
    logger.info("ColBERT reranker available")
except ModuleNotFoundError:
    HAS_COLBERT = False


class HybridRetriever:
    """Two-stage retriever combining BM25 and dense vector search.

    Parameters
    ----------
    bm25_retriever:
        A :class:`~app.memory.retrieval.bm25_retriever.BM25Retriever` instance.
    chroma_store:
        A :class:`~app.memory.vectors.chroma_store.ChromaStore` instance.
    rrf_k:
        RRF constant (default 60; higher → smoother rank fusion).
    colbert_enabled:
        If True and ``colbert-ai`` is installed, apply ColBERT reranking.
    colbert_checkpoint:
        Path or HuggingFace model name for the ColBERT checkpoint.
    """

    def __init__(
        self,
        bm25_retriever: Any,
        chroma_store: Any,
        rrf_k: int = 60,
        colbert_enabled: bool = False,
        colbert_checkpoint: str = "colbert-ir/colbertv2.0",
    ) -> None:
        self._bm25 = bm25_retriever
        self._chroma = chroma_store
        self._rrf_k = rrf_k
        self._colbert_enabled = colbert_enabled and HAS_COLBERT
        self._colbert_checkpoint = colbert_checkpoint
        self._colbert_model: Any = None

        if colbert_enabled and not HAS_COLBERT:
            logger.warning(
                "colbert_enabled=True but colbert-ai not installed. "
                "Reranking disabled. Install with: pip install colbert-ai"
            )

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        user_id: str | None = None,
        bm25_top_k: int = 20,
        vector_top_k: int = 20,
    ) -> list[Any]:
        """Return the top-k most relevant records using hybrid retrieval.

        Parameters
        ----------
        query:
            The user query string.
        top_k:
            Number of results to return after merging.
        user_id:
            Optional filter for user-specific memory isolation.
        bm25_top_k:
            Candidates fetched from BM25 (pre-merge).
        vector_top_k:
            Candidates fetched from ChromaDB (pre-merge).
        """
        # Stage 1 — BM25
        bm25_results: list[Any] = self._bm25.retrieve(query, top_k=bm25_top_k)

        # Stage 2 — Dense vector search
        vector_results: list[Any] = []
        try:
            if self._chroma is not None:
                vector_results = await self._chroma.search(
                    query=query,
                    top_k=vector_top_k,
                    user_id=user_id,
                )
        except Exception as exc:
            logger.warning("ChromaDB search failed, using BM25 only: %s", exc)

        # Stage 3 — Reciprocal Rank Fusion
        merged = self._rrf_merge(bm25_results, vector_results, top_k=top_k * 2)

        # Stage 4 — Optional ColBERT reranking
        if self._colbert_enabled and len(merged) > top_k:
            merged = await self._colbert_rerank(query, merged, top_k=top_k)

        return merged[:top_k]

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _rrf_merge(
        self,
        list_a: list[Any],
        list_b: list[Any],
        top_k: int,
    ) -> list[Any]:
        """Merge two ranked lists using Reciprocal Rank Fusion."""
        scores: dict[str, float] = {}
        id_to_record: dict[str, Any] = {}

        def _id(record: Any) -> str:
            return getattr(record, "id", None) or str(id(record))

        for rank, rec in enumerate(list_a):
            rid = _id(rec)
            scores[rid] = scores.get(rid, 0.0) + 1.0 / (self._rrf_k + rank + 1)
            id_to_record[rid] = rec

        for rank, rec in enumerate(list_b):
            rid = _id(rec)
            scores[rid] = scores.get(rid, 0.0) + 1.0 / (self._rrf_k + rank + 1)
            id_to_record[rid] = rec

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [id_to_record[rid] for rid, _ in ranked[:top_k]]

    async def _colbert_rerank(
        self,
        query: str,
        candidates: list[Any],
        top_k: int,
    ) -> list[Any]:
        """Apply ColBERT MaxSim reranking (requires ``colbert-ai``)."""
        if not HAS_COLBERT or not candidates:
            return candidates

        try:
            if self._colbert_model is None:
                from colbert.modeling.checkpoint import Checkpoint  # type: ignore[import-untyped]
                self._colbert_model = Checkpoint(self._colbert_checkpoint, colbert_config=None)

            passages = [getattr(c, "content", str(c)) for c in candidates]
            # ColBERT MaxSim scoring
            scores = self._colbert_model.score(query, passages)
            ranked = sorted(
                zip(scores, candidates), key=lambda x: x[0], reverse=True
            )
            return [rec for _, rec in ranked[:top_k]]
        except Exception as exc:
            logger.warning("ColBERT reranking failed, returning pre-reranked list: %s", exc)
            return candidates[:top_k]
