"""Tests for BM25 and Hybrid (BM25 + Vector RRF) retrieval."""

from dataclasses import dataclass
from typing import Any
import pytest

from app.memory.retrieval.bm25_retriever import BM25Retriever, HAS_BM25
from app.memory.retrieval.hybrid_retriever import HybridRetriever


@dataclass
class MockRecord:
    id: str
    content: str
    user_id: str = "creator"


@pytest.fixture
def sample_records():
    return [
        MockRecord(id="rec-1", content="Python asyncio event loop and networking"),
        MockRecord(id="rec-2", content="PostgreSQL relational database schema design"),
        MockRecord(id="rec-3", content="ChromaDB vector embeddings and semantic search"),
        MockRecord(id="rec-4", content="Azmuth personal AI assistant architecture"),
        MockRecord(id="rec-5", content="Tamil and English multilingual speech processing"),
    ]


def test_bm25_retriever_keyword_search(sample_records):
    """BM25 retrieves and ranks documents by keyword relevance."""
    if not HAS_BM25:
        pytest.skip("rank-bm25 not installed")

    retriever = BM25Retriever(sample_records)
    results = retriever.retrieve("PostgreSQL database", top_k=2)
    assert len(results) >= 1
    assert results[0].id == "rec-2"
    assert "PostgreSQL" in results[0].content


def test_bm25_retriever_rebuild(sample_records):
    """Rebuild updates the index dynamically."""
    if not HAS_BM25:
        pytest.skip("rank-bm25 not installed")

    retriever = BM25Retriever(sample_records[:2])
    # rec-3 is not in the initial index
    assert len(retriever.retrieve("embeddings", top_k=2)) == 0

    # Rebuild with full list
    retriever.rebuild(sample_records)
    hits = retriever.retrieve("embeddings", top_k=2)
    assert len(hits) == 1
    assert hits[0].id == "rec-3"


class MockVectorStore:
    def __init__(self, records: list[MockRecord]):
        self._records = {r.id: r for r in records}

    async def search(self, query: str, top_k: int = 5, user_id: str | None = None) -> list[Any]:
        # Return rec-4 and rec-3 for testing
        return [self._records["rec-4"], self._records["rec-3"]][:top_k]


@pytest.mark.asyncio
async def test_hybrid_retriever_rrf_fusion(sample_records):
    """HybridRetriever merges BM25 keyword candidates with dense vector search candidates."""
    bm25 = BM25Retriever(sample_records)
    vector_store = MockVectorStore(sample_records)

    hybrid = HybridRetriever(bm25_retriever=bm25, chroma_store=vector_store, rrf_k=60)
    results = await hybrid.retrieve("Azmuth assistant architecture", top_k=3)

    assert len(results) <= 3
    ids = [r.id for r in results]
    assert "rec-4" in ids  # Matches both BM25 and Vector, should be top ranked


@pytest.mark.asyncio
async def test_hybrid_retriever_graceful_vector_failure(sample_records):
    """If vector search fails, HybridRetriever gracefully falls back to BM25 results."""
    bm25 = BM25Retriever(sample_records)

    class BrokenVectorStore:
        async def search(self, *args, **kwargs):
            raise RuntimeError("Chroma connection refused")

    hybrid = HybridRetriever(bm25_retriever=bm25, chroma_store=BrokenVectorStore())
    # Should not raise exception
    results = await hybrid.retrieve("Python asyncio", top_k=2)
    assert isinstance(results, list)
