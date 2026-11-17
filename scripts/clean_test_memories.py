"""Delete the two test memories created during semantic recall setup."""

from app.config.settings import load_settings
from app.memory import build_memory_manager

TEST_MARKERS = (
    "dark mode in all applications",
    "Windows automation project",
)


def main() -> int:
    settings = load_settings()
    manager = build_memory_manager(settings)

    before = manager.count(user_id="creator")
    print(f"Before: {before} records")

    deleted = 0
    for record in manager.list_for_user("creator"):
        if any(marker in record.content for marker in TEST_MARKERS):
            manager.delete(record.id)
            print(f"  deleted: {record.content[:60]}")
            deleted += 1

    after = manager.count(user_id="creator")
    print(f"After:  {after} records  ({deleted} deleted)")

    manager.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())