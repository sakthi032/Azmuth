
"""Verify the configured memory backend works from a worker thread.

Run:  py -m scripts.memory_backend_test
"""

from __future__ import annotations

import threading

from app.config.settings import load_settings
from app.memory import MemoryKind, build_memory_manager


def main() -> None:
    settings = load_settings()
    print(f"configured backend: {settings.memory_backend}")

    memory = build_memory_manager(settings)
    try:
        record = memory.store(
            "Sakthivel's favourite language is Tamil",
            user_id="creator",
            kind=MemoryKind.FACT,
            importance=0.9,
        )
        print(f"stored: {record.id[:8]}...")

        result: dict[str, object] = {}

        def worker() -> None:
            try:
                hits = memory.recall("Tamil", user_id="creator")
                result["hits"] = [h.content for h in hits]
            except Exception as exc:  # noqa: BLE001
                result["error"] = repr(exc)

        thread = threading.Thread(target=worker, name="worker")
        thread.start()
        thread.join()

        if "error" in result:
            print(f"FAILED from thread: {result['error']}")
        else:
            print(f"recall from worker thread: {result['hits']}")
            print("PASS")

        memory.delete(record.id)
    finally:
        memory.close()


if __name__ == "__main__":
    main()

# py -m scripts.memory_backend_test