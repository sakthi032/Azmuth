
"""Security policies enforced by the automation layer.

Two independent policies live here:

* :class:`ApplicationPolicy` - the allowlist plus optional per-alias
  command-line arguments. The allowlist makes ``open_application`` safe
  to grant to restricted principals: launching by arbitrary path is
  refused. The arguments field lets a single executable be launched in
  different modes (Chrome profiles, debug flags).
* :class:`PathPolicy` - the file sandbox. Every file operation resolves
  its path against this policy first.

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

    * an absolute path to the executable,
    * a bare executable name resolved via :func:`shutil.which`,
    * a Windows URI protocol such as ``ms-settings:``.

    ``alias_arguments`` optionally maps an alias to extra command-line
    arguments. Arguments are always passed as a tuple, never a string -
    no shell parsing happens, and no user-supplied text reaches the
    command line.
    """

    aliases: Mapping[str, str] = field(default_factory=dict)
    alias_arguments: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Normalise alias keys so lookup is case-insensitive.
        normalised = {
            key.strip().lower(): value
            for key, value in self.aliases.items()
            if key and key.strip()
        }
        object.__setattr__(self, "aliases", normalised)

        normalised_args = {
            key.strip().lower(): tuple(args)
            for key, args in self.alias_arguments.items()
            if key and key.strip()
        }
        object.__setattr__(self, "alias_arguments", normalised_args)

    @classmethod
    def default_windows(cls) -> "ApplicationPolicy":
        """A conservative default allowlist for Windows 11.

        Includes only Windows built-ins that are guaranteed present.
        Discovery (``list_installed_apps``) covers everything else at
        runtime; this list is what the runtime trusts unconditionally.
        """
        if sys.platform != "win32":
            return cls(aliases={})

        windir = os.environ.get("WINDIR", r"C:\Windows")
        local_appdata = os.environ.get("LOCALAPPDATA", "")

        chrome_path = ""
        for candidate in (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ):
            if Path(candidate).is_file():
                chrome_path = candidate
                break

        aliases: dict[str, str] = {
            "notepad":    str(Path(windir) / "System32" / "notepad.exe"),
            "calc":       str(Path(windir) / "System32" / "calc.exe"),
            "calculator": str(Path(windir) / "System32" / "calc.exe"),
            "mspaint":    str(Path(windir) / "System32" / "mspaint.exe"),
            "paint":      str(Path(windir) / "System32" / "mspaint.exe"),
            "explorer":   str(Path(windir) / "explorer.exe"),
            "cmd":        str(Path(windir) / "System32" / "cmd.exe"),
            "powershell": str(Path(windir) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"),
            "taskmgr":    str(Path(windir) / "System32" / "Taskmgr.exe"),
            "settings":   "ms-settings:",
        }

        alias_arguments: dict[str, tuple[str, ...]] = {}

        if chrome_path:
            aliases["chrome"] = chrome_path
            aliases["chrome-debug"] = chrome_path
            aliases["chrome-personal"] = chrome_path
            aliases["chrome-work"] = chrome_path

            debug_profile = str(Path(local_appdata) / "Azmuth" / "chrome-debug-profile") if local_appdata else ""
            # Chrome refuses --remote-debugging-port on the default
            # profile since v136, so we use a copy of the user's profile
            # that has their logins but is a separate directory. See
            # scripts/chrome_cdp.ps1 for the copy + launch workflow.
            cdp_profile = (
                str(Path(local_appdata) / "Azmuth" / "chrome-cdp-profile")
                if local_appdata else ""
            )
            alias_arguments["chrome-debug"] = (
                f"--user-data-dir={cdp_profile}",
                "--profile-directory=Default",
                "--remote-debugging-port=9222",
            )
            alias_arguments["chrome-personal"] = ("--profile-directory=Default",)
            alias_arguments["chrome-work"] = ("--profile-directory=Profile 1",)

        return cls(aliases=aliases, alias_arguments=alias_arguments)

    def resolve(self, alias: str) -> Path:
        """Return the absolute executable path for ``alias``, or raise."""
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

        located = shutil.which(target)
        if located is None:
            raise AutomationError(
                f"application {alias!r} (exe {target!r}) is not on PATH"
            )
        return Path(located)

    def arguments_for(self, alias: str) -> tuple[str, ...]:
        """Return the extra command-line arguments for ``alias``.

        Empty tuple when the alias has no arguments. Never raises.
        """
        return tuple(self.alias_arguments.get(alias.strip().lower(), ()))

    def known_aliases(self) -> tuple[str, ...]:
        return tuple(sorted(self.aliases))


# --------------------------------------------------------------------------
# File sandbox
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class PathPolicy:
    """A set of allowed roots for file operations."""

    roots: tuple[Path, ...] = ()

    @classmethod
    def from_paths(cls, paths: Iterable[str | Path]) -> "PathPolicy":
        resolved: list[Path] = []
        for raw in paths:
            path = Path(raw).expanduser()
            try:
                resolved.append(path.resolve(strict=False))
            except (OSError, RuntimeError) as exc:
                raise AutomationError(
                    f"could not resolve sandbox root {raw!r}: {exc}"
                ) from exc
        return cls(roots=tuple(resolved))

    def resolve_and_check(self, path: str | Path) -> Path:
        if isinstance(path, str):
            if not path.strip():
                raise AutomationError("path must be non-empty")
            candidate: Path = Path(path)
        else:
            candidate = path

        try:
            resolved = candidate.expanduser().resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise AutomationError(
                f"could not resolve path {candidate}: {exc}"
            ) from exc

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
    try:
        return candidate == root or candidate.is_relative_to(root)
    except (OSError, RuntimeError):
        return False
