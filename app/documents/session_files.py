"""Per-session allowlist for files picked outside the workspace sandbox.

The file picker (B3) lets the user hand Azmuth files that live anywhere
on disk. Those paths are approved for THIS process only and are never
persisted. When the process exits, the allowlist is gone.

The document tools consult this registry as a fallback after the
workspace policy check fails.
"""

from __future__ import annotations

import threading
from pathlib import Path


class SessionFileRegistry:
    """Thread-safe set of user-picked paths allowed for this session."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._files: dict[Path, str] = {}  # resolved path -> display name

    def allow(self, path: str | Path) -> Path:
        """Register ``path`` as readable for this session."""
        resolved = Path(path).expanduser().resolve(strict=False)
        with self._lock:
            self._files[resolved] = resolved.name
        return resolved

    def is_allowed(self, path: str | Path) -> bool:
        try:
            resolved = Path(path).expanduser().resolve(strict=False)
        except (OSError, RuntimeError):
            return False
        with self._lock:
            return resolved in self._files

    def clear(self) -> None:
        with self._lock:
            self._files.clear()

    def paths(self) -> list[Path]:
        with self._lock:
            return list(self._files.keys())


_REGISTRY: SessionFileRegistry | None = None
_REGISTRY_LOCK = threading.Lock()


def get_registry() -> SessionFileRegistry:
    """Return the process-wide session file registry (lazily created)."""
    global _REGISTRY
    if _REGISTRY is None:
        with _REGISTRY_LOCK:
            if _REGISTRY is None:
                _REGISTRY = SessionFileRegistry()
    return _REGISTRY


__all__ = ["SessionFileRegistry", "get_registry"]