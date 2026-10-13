
"""Read Chrome's profile list from its own state file.

Chrome stores a JSON file at
``%LOCALAPPDATA%\\Google\\Chrome\\User Data\\Local State``
listing every configured profile. The ``profile.info_cache`` section has
one entry per profile, keyed by directory name, with a user-visible name.

Reading it means we never hardcode profile directories. Adding a new
profile in Chrome makes it available to Azmuth automatically.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from app.config.logging_setup import get_logger
from app.core.errors import AutomationError

logger = get_logger("automation.chrome_profiles")


@dataclass(frozen=True, slots=True)
class ChromeProfile:
    directory: str
    display_name: str

    def as_dict(self) -> dict[str, str]:
        return {"directory": self.directory, "name": self.display_name}


def list_chrome_profiles() -> list[ChromeProfile]:
    """Return every Chrome profile on this machine. Empty on non-Windows."""
    if sys.platform != "win32":
        return []

    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return []

    state_path = (
        Path(local) / "Google" / "Chrome" / "User Data" / "Local State"
    )
    if not state_path.is_file():
        raise AutomationError(
            f"Chrome's profile state file was not found at {state_path}. "
            f"Chrome may not be installed for this user."
        )

    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AutomationError(
            f"could not read Chrome's profile state: {exc}"
        ) from exc

    info_cache = data.get("profile", {}).get("info_cache", {})
    profiles: list[ChromeProfile] = []
    for directory, info in info_cache.items():
        display_name = ""
        if isinstance(info, dict):
            display_name = str(info.get("name") or directory)
        else:
            display_name = str(directory)
        profiles.append(ChromeProfile(directory=directory, display_name=display_name))

    profiles.sort(key=lambda p: p.display_name.lower())
    logger.info(
        "chrome profiles discovered",
        extra={"event": "CHROME_PROFILES_DISCOVERED", "count": len(profiles)},
    )
    return profiles


__all__ = ["ChromeProfile", "list_chrome_profiles"]
