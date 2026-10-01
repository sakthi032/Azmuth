"""Security policies enforced by the automation layer.

Two independent policies live here:

* :class:`ApplicationPolicy` — the allowlist that makes
  ``open_application`` safe to grant to restricted principals. Without it,
  "open an application" is equivalent to "run any executable", which is
  not what OPEN_APPLICATION should mean.
* :class:`PathPolicy` — the file sandbox. Every file operation resolves
  its path against this policy first. Paths outside the allowed roots are
  rejected; ``..`` traversal and symlink escapes are caught by resolving
  to a canonical absolute path before the containment check.

Both policies are data. They hold no OS state, do nothing on their own,
and are trivially unit-testable.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from app.core.errors import AutomationError


# --------------------------------------------------------------------------
# Application allowlist
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ApplicationPolicy:
    """Whitelisted applications and how to resolve their executable paths.

    ``aliases`` maps a lowercased short name to one of:

    * an absolute path to the executable, or
    * a bare executable name that will be resolved with
      :func:`shutil.which` (searched on ``PATH`` only — never the current
      directory, which would be a code-execution vector).

    The alias → path indirection is deliberate: users type aliases, and
    the automation layer decides what they map to. There is no code path
    from a user-supplied string to a raw subprocess call.
    """

    aliases: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def default_windows(cls) -> "ApplicationPolicy":
        """A conservative default allowlist for Windows 11.

        Includes only commonly-installed apps and Windows built-ins. The
        mapping keys are always lowercased for case-insensitive lookup.
        """
        if sys.platform != "win32":
            return cls(aliases={})

        windir = os.environ.get("WINDIR", r"C:\Windows")
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        program_files_x86 = os.environ.get(
            "ProgramFiles(x86)", r"C:\Program Files (x86)"
        )
        local_appdata = os.environ.get("LOCALAPPDATA", "")

        return cls(
            aliases={
                # Windows built-ins — always present.
                "notepad":       str(Path(windir) / "System32" / "notepad.exe"),
                "calc":          str(Path(windir) / "System32" / "calc.exe"),
                "calculator":    str(Path(windir) / "System32" / "calc.exe"),
                "mspaint":       str(Path(windir) / "System32" / "mspaint.exe"),
                "paint":         str(Path(windir) / "System32" / "mspaint.exe"),
                "explorer":      str(Path(windir) / "explorer.exe"),
                "cmd":           str(Path(windir) / "System32" / "cmd.exe"),
                "powershell":    str(Path(windir) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"),
                "taskmgr":       str(Path(windir) / "System32" / "Taskmgr.exe"),
                # Common browsers — path may not exist, resolution just fails
                # gracefully and the alias is unusable.
                "chrome":        str(Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe"),
                "edge":          str(Path(program_files_x86) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
                "firefox":       str(Path(program_files) / "Mozilla Firefox" / "firefox.exe"),
                # Anything in LOCALAPPDATA only if that folder exists.
                "vscode":        str(Path(local_appdata) / "Programs" / "Microsoft VS Code" / "Code.exe") if local_appdata else "",
                "settings":      "ms-settings:",
            }
        )
    def __post_init__(self) -> None:
        """Normalise alias keys so lookup is genuinely case-insensitive.

        Keys are stripped and lowercased; empty keys are dropped. The
        ``aliases`` mapping is replaced, not mutated — a frozen dataclass
        must not have its values edited in place.
        """
        normalised = {
            key.strip().lower(): value
            for key, value in self.aliases.items()
            if key and key.strip()
        }
        object.__setattr__(self, "aliases", normalised)
        
        
    def resolve(self, alias: str) -> Path:
        """Return the absolute executable path for ``alias``, or raise.

        Lookup is case-insensitive. Both the alias and the resolved path
        are validated: an alias with no mapping is rejected, and a mapped
        path that does not exist is rejected.
        """
        key = alias.strip().lower()
        if not key:
            raise AutomationError("application alias must be non-empty")

        target = self.aliases.get(key)
        if not target:
            known = ", ".join(sorted(self.aliases)) or "(none)"
            raise AutomationError(
                f"application {alias!r} is not in the allowlist. "
                f"Known aliases: {known}"
            )

        candidate = Path(target)
        # Distinguish "explicit path to a file" from "bare command name to
        # look up on PATH". ``Path.is_absolute()`` alone is not enough:
        # on Windows a rooted path without a drive letter
        # (``/no/such/binary``, ``\foo\bar.exe``) is not "absolute" by
        # ``pathlib``'s definition, yet it is unambiguously a path. Any
        # separator character forces the path interpretation.
        looks_like_path = (
            candidate.is_absolute()
            or os.sep in target
            or (os.altsep is not None and os.altsep in target)
        )
        if looks_like_path:
            if not candidate.is_file():
                raise AutomationError(
                    f"application {alias!r} resolves to {candidate}, "
                    f"which does not exist on this system"
                )
            return candidate

        # Bare name — resolve via PATH only.
        located = shutil.which(target)
        if located is None:
            raise AutomationError(
                f"application {alias!r} (exe {target!r}) is not on PATH"
            )
        return Path(located)

    def known_aliases(self) -> tuple[str, ...]:
        return tuple(sorted(self.aliases))


# --------------------------------------------------------------------------
# File sandbox
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class PathPolicy:
    """A set of allowed roots for file operations.

    A path is *inside the sandbox* if its canonical absolute form is equal
    to, or a descendant of, one of the configured roots. Canonicalisation
    is done via :meth:`pathlib.Path.resolve` with ``strict=False``, which
    folds ``..`` segments and follows symlinks. The containment check is
    then performed on the resolved paths.
    """

    roots: tuple[Path, ...] = ()

    @classmethod
    def from_paths(cls, paths: Iterable[str | Path]) -> "PathPolicy":
        resolved: list[Path] = []
        for raw in paths:
            path = Path(raw).expanduser()
            try:
                resolved.append(path.resolve(strict=False))
            except (OSError, RuntimeError) as exc:
                # A root that cannot be resolved is worse than useless —
                # it silently drops the protection it was meant to give.
                raise AutomationError(
                    f"could not resolve sandbox root {raw!r}: {exc}"
                ) from exc
        return cls(roots=tuple(resolved))

    def resolve_and_check(self, path: str | Path) -> Path:
        """Return the canonical path, or raise :class:`AutomationError`.

        Raises if the input is empty, if the path cannot be resolved, or
        if the resolved path lies outside every allowed root.
        """
        if isinstance(path, str):
            if not path.strip():
                raise AutomationError("path must be non-empty")
            candidate: Path = Path(path)
        else:
            candidate = path

        try:
            resolved = candidate.expanduser().resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise AutomationError(f"could not resolve path {candidate}: {exc}") from exc

        if not self.roots:
            raise AutomationError(
                "no sandbox roots are configured; file operations are disabled"
            )

        for root in self.roots:
            if _is_within(resolved, root):
                return resolved

        allowed = ", ".join(str(r) for r in self.roots)
        raise AutomationError(
            f"path {resolved} is outside the allowed sandbox ({allowed})"
        )

    def contains(self, path: str | Path) -> bool:
        try:
            self.resolve_and_check(path)
        except AutomationError:
            return False
        return True


def _is_within(candidate: Path, root: Path) -> bool:
    """True iff ``candidate`` is ``root`` or lies under it.

    Uses :meth:`Path.is_relative_to` on the *resolved* forms — this is
    what catches ``../`` traversal and symlink escape attempts.
    """
    try:
        return candidate == root or candidate.is_relative_to(root)
    except (OSError, RuntimeError):
        return False