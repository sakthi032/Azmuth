"""PC controls: volume, mute, brightness, lock, shutdown timer,
open folder, open website.

All operations use either hardcoded Windows APIs (ctypes), argv-only
subprocess (``subprocess.run``/``Popen`` with ``shell=False``), or
``webbrowser.open``. No tool accepts a caller-supplied shell command.

Volume and mute control require ``pycaw``. If pycaw is not installed,
a clear :class:`ProviderUnavailableError` is raised. The remaining
controls work without it.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from app.config.logging_setup import get_logger
from app.core.errors import AutomationError, ProviderUnavailableError

logger = get_logger("automation.pc_controls")


@dataclass(frozen=True, slots=True)
class PCRunResult:
    action: str
    value: object
    success: bool
    message: str = ""


class PCController:
    """Reversible and disruptive PC operations. Never accepts raw commands."""

    _TIMEOUT = 10.0

    # -- audio (pycaw) ------------------------------------------------
    async def set_volume(self, level: int) -> PCRunResult:
        if not 0 <= level <= 100:
            raise AutomationError("volume must be between 0 and 100")
        await asyncio.to_thread(_set_volume_sync, level)
        logger.info(
            "volume set",
            extra={"event": "VOLUME_SET", "level": level},
        )
        return PCRunResult(
            action="set_volume", value=level, success=True,
        )

    async def mute(self, muted: bool) -> PCRunResult:
        await asyncio.to_thread(_mute_sync, muted)
        logger.info(
            "mute toggled",
            extra={"event": "MUTE_SET", "muted": muted},
        )
        return PCRunResult(
            action="mute", value=muted, success=True,
        )

    # -- display ------------------------------------------------------
    async def set_brightness(self, level: int) -> PCRunResult:
        from app.automation.settings import SettingsController
        if not 0 <= level <= 100:
            raise AutomationError("brightness must be between 0 and 100")
        result = await SettingsController().set_brightness(level)
        return PCRunResult(
            action="set_brightness", value=level, success=result.success,
        )

    # -- session ------------------------------------------------------
    async def lock_screen(self) -> PCRunResult:
        if sys.platform != "win32":
            raise ProviderUnavailableError(
                "lock_screen is only available on Windows"
            )
        def _do() -> None:
            import ctypes
            if not ctypes.windll.user32.LockWorkStation():
                raise AutomationError("LockWorkStation returned 0")
        await asyncio.to_thread(_do)
        logger.info("screen locked", extra={"event": "SCREEN_LOCKED"})
        return PCRunResult(
            action="lock_screen", value=True, success=True,
        )

    # -- shutdown timer -----------------------------------------------
    async def schedule_shutdown(self, seconds: int) -> PCRunResult:
        if not 0 <= seconds <= 86400:
            raise AutomationError(
                "shutdown delay must be between 0 and 86400 seconds"
            )
        await asyncio.to_thread(
            _run_argv, ["shutdown", "/s", "/t", str(seconds)]
        )
        logger.info(
            "shutdown scheduled",
            extra={"event": "SHUTDOWN_SCHEDULED", "seconds": seconds},
        )
        return PCRunResult(
            action="shutdown_timer", value=seconds, success=True,
            message=f"shutdown scheduled in {seconds} seconds",
        )

    async def cancel_shutdown(self) -> PCRunResult:
        await asyncio.to_thread(_run_argv, ["shutdown", "/a"])
        logger.info(
            "shutdown cancelled", extra={"event": "SHUTDOWN_CANCELLED"}
        )
        return PCRunResult(
            action="cancel_shutdown", value=True, success=True,
            message="shutdown cancelled",
        )

    # -- filesystem ---------------------------------------------------
    async def open_folder(self, path: str) -> PCRunResult:
        if sys.platform != "win32":
            raise ProviderUnavailableError(
                "open_folder is only available on Windows"
            )
        resolved = Path(path).expanduser().resolve(strict=False)
        if not resolved.is_dir():
            raise AutomationError(f"{resolved} is not a directory")
        def _do() -> None:
            subprocess.Popen(
                ["explorer.exe", str(resolved)],
                close_fds=True,
            )
        await asyncio.to_thread(_do)
        logger.info(
            "folder opened",
            extra={"event": "FOLDER_OPENED", "path": str(resolved)},
        )
        return PCRunResult(
            action="open_folder", value=str(resolved), success=True,
        )

    # -- web ----------------------------------------------------------
    async def open_website(self, url: str) -> PCRunResult:
        lowered = url.strip().lower()
        if not (
            lowered.startswith("http://")
            or lowered.startswith("https://")
        ):
            raise AutomationError(
                "url must start with http:// or https://"
            )
        def _do() -> bool:
            return webbrowser.open(url, new=2)
        opened = await asyncio.to_thread(_do)
        if not opened:
            raise AutomationError("no browser available to open URL")
        logger.info(
            "website opened",
            extra={"event": "WEBSITE_OPENED", "url": url},
        )
        return PCRunResult(
            action="open_website", value=url, success=True,
        )


# --------------------------------------------------------------------------
# Sync helpers — run via asyncio.to_thread
# --------------------------------------------------------------------------
def _with_endpoint_volume(action_name: str, callback):
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume  # type: ignore
        import comtypes  # type: ignore
        from comtypes import CLSCTX_ALL  # type: ignore
        from ctypes import POINTER, cast
    except ImportError as exc:
        raise ProviderUnavailableError(
            f"{action_name} requires pycaw (pip install pycaw comtypes)"
        ) from exc

    comtypes.CoInitialize()
    try:
        devices = AudioUtilities.GetSpeakers()
        if devices is None:
            raise ProviderUnavailableError("no audio output device found")
        if hasattr(devices, "EndpointVolume"):
            volume = devices.EndpointVolume
        else:
            interface = devices.Activate(
                IAudioEndpointVolume._iid_, CLSCTX_ALL, None
            )
            volume = cast(interface, POINTER(IAudioEndpointVolume))
        return callback(volume)
    finally:
        comtypes.CoUninitialize()


def _get_volume_sync() -> int:
    return int(round(_with_endpoint_volume(
        "get_volume",
        lambda vol: vol.GetMasterVolumeLevelScalar() * 100.0,
    )))


def _get_mute_sync() -> bool:
    return bool(_with_endpoint_volume(
        "get_mute",
        lambda vol: vol.GetMute(),
    ))


def _set_volume_sync(level: int) -> None:
    _with_endpoint_volume(
        "set_volume",
        lambda vol: vol.SetMasterVolumeLevelScalar(level / 100.0, None),
    )


def _mute_sync(muted: bool) -> None:
    _with_endpoint_volume(
        "mute",
        lambda vol: vol.SetMute(1 if muted else 0, None),
    )


def _run_argv(argv: list[str]) -> None:
    """Run an argv-only command. Never shell=True."""
    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=10.0,
        shell=False,
    )
    if completed.returncode != 0:
        raise AutomationError(
            f"command {argv[0]} failed: "
            f"{completed.stderr.strip() or 'unknown error'}"
        )


__all__ = ["PCController", "PCRunResult"]