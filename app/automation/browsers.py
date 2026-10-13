
"""Registry of Chromium-based browsers.

Chrome, Edge, Brave, Opera, and Vivaldi all use the same DevTools
Protocol. They differ only in executable path, port, and where they
store their profile. This module holds those differences as data.

A :class:`BrowserProfile` says:

* how to find the executable
* which TCP port to expose the debug endpoint on
* where the user's profile lives (so we can copy it)

Adding a browser is adding one entry. No code changes elsewhere.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BrowserProfile:
    """Everything the launcher and CDP client need to know about a browser."""

    #: short identifier used in tool names: "chrome", "edge", ...
    name: str

    #: human-readable name for prompts and error messages
    display_name: str

    #: candidate absolute paths to the executable, tried in order
    executable_candidates: tuple[str, ...]

    #: the port the debug endpoint will listen on
    debug_port: int

    #: subdirectory name under %LOCALAPPDATA%\\Azmuth\\browsers\\ where
    #: we keep the CDP-enabled profile copy for this browser
    profile_key: str

    def find_executable(self) -> Path | None:
        """Return the first existing path from the candidates, or None."""
        for candidate in self.executable_candidates:
            p = Path(os.path.expandvars(candidate))
            if p.is_file():
                return p
        return None

    def is_installed(self) -> bool:
        return self.find_executable() is not None


def _env(*names: str) -> str:
    """Return the first environment variable that is set and non-empty."""
    for name in names:
        value = os.environ.get(name, "")
        if value:
            return value
    return ""


def _build_registry() -> tuple[BrowserProfile, ...]:
    """Return profiles for every Chromium browser we know about.

    Paths are looked up from the standard Windows install locations.
    On non-Windows this returns an empty tuple.
    """
    if sys.platform != "win32":
        return ()

    local = _env("LOCALAPPDATA")
    pf = _env("ProgramFiles")
    pf86 = _env("ProgramFiles(x86)")
    appdata = _env("APPDATA")

    profiles: list[BrowserProfile] = []

    # ---- Google Chrome ---------------------------------------------------
    profiles.append(BrowserProfile(
        name="chrome",
        display_name="Google Chrome",
        executable_candidates=(
            rf"{pf}\Google\Chrome\Application\chrome.exe",
            rf"{pf86}\Google\Chrome\Application\chrome.exe",
            rf"{local}\Google\Chrome\Application\chrome.exe",
        ),
        debug_port=9222,
        profile_key="chrome",
    ))

    # ---- Microsoft Edge --------------------------------------------------
    profiles.append(BrowserProfile(
        name="edge",
        display_name="Microsoft Edge",
        executable_candidates=(
            rf"{pf86}\Microsoft\Edge\Application\msedge.exe",
            rf"{pf}\Microsoft\Edge\Application\msedge.exe",
        ),
        debug_port=9223,
        profile_key="edge",
    ))

    # ---- Brave -----------------------------------------------------------
    profiles.append(BrowserProfile(
        name="brave",
        display_name="Brave",
        executable_candidates=(
            rf"{pf}\BraveSoftware\Brave-Browser\Application\brave.exe",
            rf"{pf86}\BraveSoftware\Brave-Browser\Application\brave.exe",
            rf"{local}\BraveSoftware\Brave-Browser\Application\brave.exe",
        ),
        debug_port=9224,
        profile_key="brave",
    ))

    # ---- Opera -----------------------------------------------------------
    profiles.append(BrowserProfile(
        name="opera",
        display_name="Opera",
        executable_candidates=(
            rf"{local}\Programs\Opera\opera.exe",
            rf"{pf}\Opera\opera.exe",
        ),
        debug_port=9225,
        profile_key="opera",
    ))

    # ---- Vivaldi ---------------------------------------------------------
    profiles.append(BrowserProfile(
        name="vivaldi",
        display_name="Vivaldi",
        executable_candidates=(
            rf"{local}\Vivaldi\Application\vivaldi.exe",
            rf"{pf}\Vivaldi\Application\vivaldi.exe",
        ),
        debug_port=9226,
        profile_key="vivaldi",
    ))

    # ---- Chromium (open-source build) -----------------------------------
    profiles.append(BrowserProfile(
        name="chromium",
        display_name="Chromium",
        executable_candidates=(
            rf"{local}\Chromium\Application\chrome.exe",
            rf"{pf}\Chromium\Application\chrome.exe",
        ),
        debug_port=9227,
        profile_key="chromium",
    ))

    return tuple(profiles)


_REGISTRY: tuple[BrowserProfile, ...] = _build_registry()


def all_profiles() -> tuple[BrowserProfile, ...]:
    return _REGISTRY


def installed_profiles() -> tuple[BrowserProfile, ...]:
    """Only the browsers actually present on this machine."""
    return tuple(p for p in _REGISTRY if p.is_installed())


def get_profile(name: str) -> BrowserProfile | None:
    key = name.strip().lower()
    for p in _REGISTRY:
        if p.name == key:
            return p
    return None


def cdp_profile_dir(profile: BrowserProfile) -> Path:
    """Where the CDP-enabled profile copy lives for this browser."""
    local = _env("LOCALAPPDATA") or str(Path.home())
    return Path(local) / "Azmuth" / "browsers" / profile.profile_key


__all__ = [
    "BrowserProfile",
    "all_profiles",
    "cdp_profile_dir",
    "get_profile",
    "installed_profiles",
]
