"""Migrate the detailed identity files into semantic memory.

After running this once, you can shrink the files in data/identity/
down to azmuth.md and user.md — everything else will be recalled
on demand by semantic search.

Idempotent: re-running overwrites the same memory entries (matched
by a marker in the metadata), so edits to the source files propagate
on the next run.
"""

from __future__ import annotations

from pathlib import Path

from app.config.logging_setup import setup_logging
from app.config.settings import load_settings
from app.memory import build_memory_manager
from app.memory.models import MemoryKind

#: Files that should be stored as long-term facts, not in the
#: always-present identity block. (filename, kind, importance)
MIGRATE: list[tuple[str, MemoryKind, float]] = [
    ("user_profile.md",        MemoryKind.FACT,       0.9),
    ("user.md",                MemoryKind.FACT,       0.9),
    ("azmuth_profile.md",      MemoryKind.FACT,       0.7),
    ("coding_rules.md",        MemoryKind.PREFERENCE, 0.8),
    ("personal_goals.md",      MemoryKind.EVENT,      0.7),
    ("azmuth_response_style.md", MemoryKind.PREFERENCE, 0.8),
]

#: Files that stay in the identity block (always present, never migrated).
KEEP_IN_IDENTITY = frozenset({"azmuth.md"})


def main() -> int:
    settings = load_settings()
    setup_logging(settings)

    identity_dir = settings.paths.data / "identity"
    if not identity_dir.is_dir():
        print(f"No identity directory at {identity_dir}")
        return 1

    manager = build_memory_manager(settings)
    if not manager.supports_semantic_recall():
        print("Semantic recall is not enabled — check .env")
        return 1

    # -- Clean out previous migrations so re-runs are idempotent --------
    print("Removing previous identity migrations...")
    removed = 0
    for record in manager.list_for_user("creator", limit=10_000):
        if record.metadata.get("source") == "identity_migration":
            manager.delete(record.id)
            removed += 1
    print(f"  removed {removed} prior migrations")

    # -- Store each migrated file as a memory ---------------------------
    stored = 0
    for filename, kind, importance in MIGRATE:
        path = identity_dir / filename
        if not path.is_file():
            print(f"  skip {filename}: not found")
            continue

        content = path.read_text(encoding="utf-8").strip()
        if not content:
            print(f"  skip {filename}: empty")
            continue

        # Prefix with the file's purpose so retrieval context is clear
        memory_content = f"[{filename}] {content}"

        record = manager.store(
            memory_content,
            user_id="creator",
            kind=kind,
            importance=importance,
            metadata={
                "source": "identity_migration",
                "source_file": filename,
            },
        )
        print(f"  stored {filename} ({len(content)} chars) → {record.id}")
        stored += 1

    print()
    print(f"Done. Stored {stored} files as memories.")
    print()
    print("Next steps:")
    print("  1. Trim data/identity/ down to azmuth.md and user.md only")
    print("     (keep the removed files in docs/identity_archive/ for reference)")
    print("  2. Restart Azmuth — the identity block becomes tiny")
    print("  3. Ask 'what's my tech stack?' — semantic search finds user_profile.md")
    print()
    manager.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())