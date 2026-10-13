
"""Discover what's installed on this machine.

Rather than maintaining a hardcoded list of application paths, Azmuth
asks Windows. Two sources, both read-only:

1. **Start Menu shortcuts** - one ``.lnk`` file per installed user-facing
   app. This is the source that matches what the user sees when they hit
   the Windows key and start typing.
2. **The registry uninstall keys** - a longer list that includes things
   without Start Menu entries.

The output is a list of ``InstalledApp`` records. Discovery caches its
result for the process lifetime; callers can force a refresh.

No external dependency. Uses ``winreg`` from the standard library and
``pathlib`` for shortcut enumeration.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.config.logging_setup import get_logger

logger = get_logger("automation.discovery")


@dataclass(frozen=True, slots=True)
class InstalledApp:
    """One app found on this machine."""

    name: str
    target: str            # resolved exe path, or "" if unresolvable
    source: str            # "start-menu" or "registry"
    shortcut: str = ""     # path to the .lnk, when source is start-menu

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "target": self.target,
            "source": self.source,
            "shortcut": self.shortcut,
        }


class AppDiscovery:
    """Find every user-facing app installed on this Windows machine."""

    def __init__(self) -> None:
        self._cache: list[InstalledApp] | None = None

    def list_installed(self, *, force: bool = False) -> list[InstalledApp]:
        if self._cache is not None and not force:
            return self._cache
        apps: list[InstalledApp] = []
        apps.extend(self._from_start_menu())
        apps.extend(self._from_registry())
        # Dedupe by lowercased name.
        seen: set[str] = set()
        unique: list[InstalledApp] = []
        for app in apps:
            key = app.name.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(app)
        unique.sort(key=lambda a: a.name.lower())
        self._cache = unique
        logger.info(
            "app discovery complete",
            extra={"event": "APP_DISCOVERY_COMPLETE", "count": len(unique)},
        )
        return unique

    # -- start menu --------------------------------------------------------
    def _from_start_menu(self) -> list[InstalledApp]:
        if sys.platform != "win32":
            return []
        roots = [
            Path(os.environ.get("APPDATA", ""))
            / "Microsoft" / "Windows" / "Start Menu" / "Programs",
            Path(os.environ.get("ProgramData", r"C:\ProgramData"))
            / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        ]
        found: list[InstalledApp] = []
        for root in roots:
            if not root.is_dir():
                continue
            for lnk in root.rglob("*.lnk"):
                name = lnk.stem
                if name.lower() in {"uninstall", "readme", "help", "documentation"}:
                    continue
                target = _resolve_lnk(lnk)
                found.append(
                    InstalledApp(
                        name=name,
                        target=str(target) if target else "",
                        source="start-menu",
                        shortcut=str(lnk),
                    )
                )
        return found

    # -- registry ----------------------------------------------------------
    def _from_registry(self) -> list[InstalledApp]:
        if sys.platform != "win32":
            return []
        try:
            import winreg  # type: ignore[import-untyped]
        except ImportError:
            return []

        # Registry hive / path pairs to scan.
        locations = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        found: list[InstalledApp] = []
        for hive, path in locations:
            try:
                key = winreg.OpenKey(hive, path)
            except OSError:
                continue
            with key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        subkey_name = winreg.EnumKey(key, i)
                    except OSError:
                        break
                    with winreg.OpenKey(key, subkey_name) as sub:
                        try:
                            display_name, _ = winreg.QueryValueEx(sub, "DisplayName")
                        except OSError:
                            continue
                        try:
                            install_location, _ = winreg.QueryValueEx(sub, "InstallLocation")
                        except OSError:
                            install_location = ""
                        try:
                            icon, _ = winreg.QueryValueEx(sub, "DisplayIcon")
                        except OSError:
                            icon = ""
                        found.append(
                            InstalledApp(
                                name=str(display_name),
                                target=_clean_icon_path(str(icon)) or str(install_location),
                                source="registry",
                            )
                        )
        return found


# --------------------------------------------------------------------------
# Shortcut resolution
# --------------------------------------------------------------------------
def _resolve_lnk(path: Path) -> Path | None:
    """Best-effort extraction of the target from a .lnk shortcut.

    Parsing the full binary format is out of scope. We extract the
    printable target path that appears near the start of the shortcut
    and check that it points to an .exe that exists. Anything else
    returns ``None`` - the caller then falls back to the app name.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return None
    # Scan for a run of ASCII printable bytes ending in .exe.
    current = bytearray()
    for byte in data:
        if 32 <= byte <= 126:
            current.append(byte)
        else:
            if current:
                text = current.decode("ascii", errors="ignore")
                if text.lower().endswith(".exe") and ":" in text:
                    candidate = Path(text)
                    if candidate.is_file():
                        return candidate
                current.clear()
    if current:
        text = current.decode("ascii", errors="ignore")
        if text.lower().endswith(".exe") and ":" in text:
            candidate = Path(text)
            if candidate.is_file():
                return candidate
    return None


def _clean_icon_path(value: str) -> str:
    """Registry DisplayIcon values can be 'C:\\x.exe,0' - strip the index."""
    return value.split(",", 1)[0].strip()


__all__ = ["AppDiscovery", "InstalledApp"]
