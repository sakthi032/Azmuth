
"""Windows settings that have a real scriptable API.

Only two settings qualify:

* **brightness** - via the WMI ``WmiMonitorBrightnessMethods`` class.
* **dark_mode** - via two registry keys under ``Themes\\Personalize``.

Everything else (Night Light, Bluetooth, focus assist, …) has no
documented scriptable API on Windows. Those are handled generically by
:class:`~app.tools.builtin.system.SetToggleTool` and
:class:`~app.tools.builtin.system.OpenSettingsPageTool`, which drive the
Settings UI directly.

This split is deliberate: fast paths for what's fast, generic UI driving
for everything else. The AI decides which tool to call.
"""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass

from app.config.logging_setup import get_logger
from app.core.errors import AutomationError

logger = get_logger("automation.settings")


@dataclass(frozen=True, slots=True)
class SettingResult:
    setting: str
    value: object
    success: bool
    message: str = ""


class SettingsController:
    """Only the settings that have a real API."""

    _TIMEOUT = 15.0

    async def set_brightness(self, level: int) -> SettingResult:
        if not 0 <= level <= 100:
            raise AutomationError("brightness must be between 0 and 100")
        script = (
            "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods)"
            f".WmiSetBrightness(1,{level})"
        )
        return await self._run_powershell(
            script, setting="brightness", value=level
        )

    async def set_dark_mode(self, on: bool) -> SettingResult:
        value = 0 if on else 1
        script = (
            "New-ItemProperty -Path "
            "'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize' "
            f"-Name AppsUseLightTheme -Value {value} -PropertyType DWord -Force | Out-Null; "
            "New-ItemProperty -Path "
            "'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize' "
            f"-Name SystemUsesLightTheme -Value {value} -PropertyType DWord -Force | Out-Null"
        )
        return await self._run_powershell(
            script, setting="dark_mode", value=on
        )

    # -- internals ---------------------------------------------------------
    async def _run_powershell(
        self, script: str, *, setting: str, value: object
    ) -> SettingResult:
        def _blocking() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                timeout=self._TIMEOUT,
                shell=False,
            )

        try:
            completed = await asyncio.to_thread(_blocking)
        except subprocess.TimeoutExpired as exc:
            raise AutomationError(f"{setting} command timed out") from exc
        except OSError as exc:
            raise AutomationError(f"could not run powershell: {exc}") from exc

        if completed.returncode != 0:
            raise AutomationError(
                f"{setting} failed: {completed.stderr.strip() or 'unknown error'}"
            )

        logger.info(
            "setting applied",
            extra={"event": "SETTING_APPLIED", "setting": setting, "value": value},
        )
        return SettingResult(setting=setting, value=value, success=True)