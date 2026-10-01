"""Windows automation subsystem public API.

    from app.automation import WindowsAutomation, WindowsAutomationImpl

The automation layer performs operations. It does **not** enforce
permissions — that is the Tool Manager's job (STEP 8). What this layer
guarantees is that once a tool is authorized to act, the act itself is
constrained: applications are launched from an allowlist, files are
confined to a sandbox, subprocesses are always argv lists, and URLs are
restricted to http/https.
"""

from app.automation.base import (
    AppLaunchResult,
    FileInfo,
    ProcessInfo,
    WindowInfo,
    WindowsAutomation,
)
from app.automation.null import NullAutomation
from app.automation.policy import ApplicationPolicy, PathPolicy
from app.automation.windows import WindowsAutomationImpl

__all__ = [
    "AppLaunchResult",
    "ApplicationPolicy",
    "FileInfo",
    "NullAutomation",
    "PathPolicy",
    "ProcessInfo",
    "WindowInfo",
    "WindowsAutomation",
    "WindowsAutomationImpl",
]