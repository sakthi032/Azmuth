"""Built-in tools shipped with Azmuth."""

from __future__ import annotations

from app.automation.resolver import TargetResolver
from app.tools.builtin.visual import ClickVisualTool
from app.automation.base import WindowsAutomation
from app.automation.browsers import installed_profiles
from app.automation.files import FileController
from app.automation.input import InputController
from app.automation.settings import SettingsController
from app.automation.uia import UIAutomationController
from app.search.manager import SearchManager
from app.tools.base import Tool
from app.tools.builtin.browser_tools import build_browser_tools
from app.tools.builtin.chrome_extension_tools import build_extension_tools
from app.tools.builtin.echo import EchoTool
from app.tools.builtin.get_time import GetTimeTool
from app.tools.builtin.screen_state import (
    GetScreenStateTool,
    GetWindowStateTool,
    VerifyElementTool,
)
from app.tools.builtin.search_web import SearchWebTool
from app.tools.builtin.system import (
    ClickAtTool,
    ClickElementTool,
    CloseApplicationTool,
    FocusWindowTool,
    GetCursorPositionTool,
    ListProcessesTool,
    ListUIElementsTool,
    ListWindowsTool,
    MoveMouseTool,
    OpenApplicationTool,
    OpenSettingsPageTool,
    OpenUrlTool,
    PressKeysTool,
    ScrollTool,
    SetSettingTool,
    SetToggleTool,
    SetWindowStateTool,
    TypeTextTool,
    TypeIntoElementTool,
)
from app.tools.builtin.files import (
    FileCopyTool,
    FileDeleteTool,
    FileListTool,
    FileMakeDirTool,
    FileMoveTool,
    FileReadTool,
    FileStatTool,
    FileWriteTool,
)



def build_builtin_tools(
    *,
    search: SearchManager | None = None,
    automation: WindowsAutomation | None = None,
    settings: SettingsController | None = None,
    uia: UIAutomationController | None = None,
    input_ctl: InputController | None = None,
    files: FileController | None = None,
    include_browsers: bool = False,
    include_extension: bool = False,
) -> tuple[Tool, ...]:
    """Instantiate the tools Azmuth ships with.

    Every subsystem argument is optional. When a subsystem is omitted,
    the tools that depend on it are not registered. When
    ``include_browsers`` is True, one tool set is registered per
    Chromium browser installed. When ``include_extension`` is True,
    the extension-backed tab tools are registered too.
    """
    tools: list[Tool] = [EchoTool(), GetTimeTool()]

    if search is not None:
        tools.append(SearchWebTool(search))

    if automation is not None:
        tools.extend([
            OpenApplicationTool(automation),
            OpenUrlTool(automation),
            ListProcessesTool(automation),
            CloseApplicationTool(automation),
            ListWindowsTool(automation),
            FocusWindowTool(automation),
            SetWindowStateTool(automation),
        ])

    if settings is not None:
        tools.append(SetSettingTool(settings))

    if uia is not None:
        tools.extend([
            OpenSettingsPageTool(),
            # -- Observation ------------------------------------------------
            GetScreenStateTool(uia),
            GetWindowStateTool(uia),
            VerifyElementTool(uia),
            # -- Discovery / search -----------------------------------------
            ListUIElementsTool(uia),
            # -- Action -----------------------------------------------------
            SetToggleTool(uia),
            ClickElementTool(uia),
        ])

    if input_ctl is not None:
        tools.extend([
            TypeTextTool(input_ctl),
            PressKeysTool(input_ctl),
            MoveMouseTool(input_ctl),
            ClickAtTool(input_ctl),
            ScrollTool(input_ctl),
            GetCursorPositionTool(input_ctl),
        ])
        
    if uia is not None and input_ctl is not None:
        resolver = TargetResolver(uia=uia, input_ctl=input_ctl)
        tools.append(ClickVisualTool(resolver, input_ctl))
        
    if files is not None:
        tools.extend([
            FileReadTool(files),
            FileWriteTool(files),
            FileDeleteTool(files),
            FileListTool(files),
            FileMoveTool(files),
            FileCopyTool(files),
            FileMakeDirTool(files),
            FileStatTool(files),
        ])


    if include_browsers:
        tools.extend(build_browser_tools(installed_profiles()))

    if include_extension:
        tools.extend(
            build_extension_tools(
                tuple(p.name for p in installed_profiles())
            )
        )
    return tuple(tools)


__all__ = ["build_builtin_tools"]