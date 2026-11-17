"""Re-embed every memory record into the vector store.

Idempotent: re-running skips records already indexed. Safe to run any
time the embedding model changes.

Usage:
    py -m scripts.reindex_memory
"""

from __future__ import annotations

import sys

from app.config.logging_setup import setup_logging
from app.config.settings import load_settings
from app.memory import build_memory_manager


def main() -> int:
    settings = load_settings()
    setup_logging(settings)

    manager = build_memory_manager(settings)

    if not manager.supports_semantic_recall():
        print("Semantic recall is not enabled.")
        print(f"  EMBEDDING_PROVIDER = {settings.embedding_provider}")
        print(f"  EMBEDDING_MODEL    = {settings.embedding_model}")
        print(f"  VECTOR_STORE       = {settings.vector_store}")
        print()
        print("Check that Ollama is running and that the model exists:")
        print(f"  ollama list | findstr {settings.embedding_model}")
        return 1

    print("Loading all memory records...")
    records = manager.backend.list(limit=100_000)
    print(f"Found {len(records)} records.")

    indexed = 0
    failed = 0
    for i, record in enumerate(records, start=1):
        try:
            vector = manager.embeddings.embed(record.content)
            manager.vectors.add(
                record.id,
                vector,
                {"kind": record.kind.value, "user_id": record.user_id or ""},
            )
            indexed += 1
        except Exception as exc:
            failed += 1
            print(f"  [{i}] FAILED {record.id}: {exc}")

        if i % 25 == 0:
            print(f"  indexed {i}/{len(records)}...")

    print()
    print(f"Done. Indexed {indexed}, failed {failed}.")
    manager.close()
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())