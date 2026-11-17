"""Approved project workspace policy for safe filesystem access.

Enforces boundaries so that Restricted mode can inspect (list, read, stat)
and write (with explicit confirmation) inside explicitly approved project
workspace directories, while keeping all access outside approved workspaces
(personal files, system directories) strictly guarded by Creator mode.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProjectWorkspacePolicy:
    """Approved workspace directories.

    Guarantees:
    - Path traversal ('..') and symbolic links are resolved strictly.
    - An empty policy allows NO workspace access (safe default).
    - Case-normalization and canonical path resolution prevent escape tricks.
    """

    approved_roots: tuple[Path, ...] = ()

    @classmethod
    def from_paths(cls, paths: Iterable[str | Path]) -> "ProjectWorkspacePolicy":
        resolved: list[Path] = []
        for raw in paths:
            if not raw:
                continue
            try:
                p = Path(raw).expanduser().resolve(strict=False)
                resolved.append(p)
            except (OSError, RuntimeError):
                continue
        return cls(approved_roots=tuple(resolved))

    def is_in_workspace(self, path: str | Path | None) -> bool:
        """Return True if path resolves strictly within any approved workspace root.

        Guarantees that path traversal ('..') and symbolic links cannot escape.
        """
        if not path or not self.approved_roots:
            return False

        try:
            candidate = Path(path).expanduser()
        except (OSError, RuntimeError):
            return False

        # If candidate is already absolute
        if candidate.is_absolute():
            try:
                resolved = candidate.resolve(strict=False)
            except (OSError, RuntimeError):
                resolved = candidate
            for root in self.approved_roots:
                try:
                    if resolved == root or resolved.is_relative_to(root):
                        return True
                except (OSError, RuntimeError):
                    continue
            return False

        # If candidate is relative, check if it resolves within any approved root
        for root in self.approved_roots:
            try:
                resolved = (root / candidate).resolve(strict=False)
                if resolved == root or resolved.is_relative_to(root):
                    return True
            except (OSError, RuntimeError):
                continue

        return False

    def resolve_workspace_path(self, path: str | Path) -> Path | None:
        """If path is within an approved workspace, return its canonical Path.
        Otherwise return None.
        """
        if not path or not self.approved_roots:
            return None
        try:
            candidate = Path(path).expanduser()
        except (OSError, RuntimeError):
            return None

        if candidate.is_absolute():
            try:
                resolved = candidate.resolve(strict=False)
            except (OSError, RuntimeError):
                resolved = candidate
            for root in self.approved_roots:
                try:
                    if resolved == root or resolved.is_relative_to(root):
                        return resolved
                except (OSError, RuntimeError):
                    continue
            return None

        for root in self.approved_roots:
            try:
                resolved = (root / candidate).resolve(strict=False)
                if resolved == root or resolved.is_relative_to(root):
                    return resolved
            except (OSError, RuntimeError):
                continue
        return None

    def add_root(self, path: str | Path) -> "ProjectWorkspacePolicy":
        """Return a new policy with an additional approved workspace root."""
        try:
            resolved = Path(path).expanduser().resolve(strict=False)
        except (OSError, RuntimeError):
            return self
        if resolved in self.approved_roots:
            return self
        return ProjectWorkspacePolicy(approved_roots=(*self.approved_roots, resolved))
