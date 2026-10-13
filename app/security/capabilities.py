"""Capability taxonomy and the default authorization policy.

A :class:`Capability` is a *verb* Azmuth can perform: ``CHAT``,
``DELETE_FILE``, ``MODIFY_SYSTEM_CONFIG``, and so on. Tools declare which
capability they need; the :class:`PermissionManager` decides whether the
current Principal may do it.

The default policy table is the single source of truth for the mapping
between :class:`PermissionMode` and capabilities. Every entry states:

* the minimum mode needed,
* whether the operation additionally requires explicit user confirmation.

Adding a capability = adding an enum member and one row to
:data:`DEFAULT_POLICY`. Anything not in the table is denied by default with
``CREATOR`` + confirmation, which is the safe choice for a new verb nobody
has thought through yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.security.modes import PermissionMode


class Capability(str, Enum):
    """What Azmuth can be asked to do."""

    # -------- safe for anyone, including anonymous restricted sessions -----
    CHAT = "chat"
    WEB_SEARCH = "web_search"
    VIEW_SYSTEM_STATUS = "view_system_status"

    # -------- safe for restricted users -----------------------------------
    # Non-destructive, does not expose personal data.
    USE_BASIC_TOOLS = "use_basic_tools"
    OPEN_APPLICATION = "open_application"
    CLOSE_APPLICATION = "close_application"
    SCREENSHOT = "screenshot"
    READ_PUBLIC_FILE = "read_public_file"

    # -------- creator-only, non-destructive -------------------------------
    READ_PERSONAL_FILE = "read_personal_file"
    WRITE_FILE = "write_file"
    LIST_FILES = "list_files"
    ACCESS_PERSONAL_MEMORY = "access_personal_memory"
    RUN_SAFE_COMMAND = "run_safe_command"
    DEVELOPER_TOOLS = "developer_tools"
    ADVANCED_AUTOMATION = "advanced_automation"

    # -------- creator-only, destructive — confirmation required -----------
    DELETE_FILE = "delete_file"
    RUN_SYSTEM_COMMAND = "run_system_command"
    MODIFY_SYSTEM_CONFIG = "modify_system_config"
    MANAGE_SECURITY = "manage_security"
    MANAGE_PLUGINS = "manage_plugins"


@dataclass(frozen=True, slots=True)
class CapabilityPolicy:
    """Authorization rule for one capability."""

    minimum_mode: PermissionMode
    requires_confirmation: bool = False
    description: str = ""


#: Default policy. Read-only by convention; :class:`PermissionManager`
#: copies it at construction so callers cannot mutate the global table.
DEFAULT_POLICY: dict[Capability, CapabilityPolicy] = {
    # ---- safe for anyone ----
    Capability.CHAT: CapabilityPolicy(
        PermissionMode.RESTRICTED, description="General conversation."
    ),
    Capability.WEB_SEARCH: CapabilityPolicy(
        PermissionMode.RESTRICTED, description="Search the public web."
    ),
    Capability.VIEW_SYSTEM_STATUS: CapabilityPolicy(
        PermissionMode.RESTRICTED, description="Read subsystem status."
    ),

    # ---- safe for restricted users ----
    Capability.USE_BASIC_TOOLS: CapabilityPolicy(
        PermissionMode.RESTRICTED, description="Non-destructive basic tools."
    ),
    Capability.OPEN_APPLICATION: CapabilityPolicy(
        PermissionMode.RESTRICTED, description="Launch an application."
    ),
    Capability.CLOSE_APPLICATION: CapabilityPolicy(
        PermissionMode.RESTRICTED, description="Close an application."
    ),
    Capability.SCREENSHOT: CapabilityPolicy(
        PermissionMode.RESTRICTED, description="Capture the screen."
    ),
    Capability.READ_PUBLIC_FILE: CapabilityPolicy(
        PermissionMode.RESTRICTED, description="Read from the public folder."
    ),

    # ---- creator-only, safe ----
    Capability.READ_PERSONAL_FILE: CapabilityPolicy(
        PermissionMode.CREATOR, description="Read personal files."
    ),
    Capability.WRITE_FILE: CapabilityPolicy(
        PermissionMode.CREATOR, description="Write a file."
    ),
    Capability.LIST_FILES: CapabilityPolicy(
        PermissionMode.CREATOR, description="Browse the filesystem."
    ),
    Capability.ACCESS_PERSONAL_MEMORY: CapabilityPolicy(
        PermissionMode.CREATOR, description="Read/write personal memory."
    ),
    Capability.RUN_SAFE_COMMAND: CapabilityPolicy(
        PermissionMode.CREATOR, description="Run a whitelisted command."
    ),
    Capability.DEVELOPER_TOOLS: CapabilityPolicy(
        PermissionMode.CREATOR, description="Developer tooling."
    ),
    Capability.ADVANCED_AUTOMATION: CapabilityPolicy(
        PermissionMode.CREATOR,
        requires_confirmation=True,
        description="Advanced device automation. Creator + confirmation required.",
    ),

    # ---- creator-only, destructive ----
    Capability.DELETE_FILE: CapabilityPolicy(
        PermissionMode.CREATOR,
        requires_confirmation=True,
        description="Delete a file.",
    ),
    Capability.RUN_SYSTEM_COMMAND: CapabilityPolicy(
        PermissionMode.CREATOR,
        requires_confirmation=True,
        description="Run an arbitrary system command.",
    ),
    Capability.MODIFY_SYSTEM_CONFIG: CapabilityPolicy(
        PermissionMode.CREATOR,
        requires_confirmation=True,
        description="Change system configuration.",
    ),
    Capability.MANAGE_SECURITY: CapabilityPolicy(
        PermissionMode.CREATOR,
        requires_confirmation=True,
        description="Change security settings.",
    ),
    Capability.MANAGE_PLUGINS: CapabilityPolicy(
        PermissionMode.CREATOR,
        requires_confirmation=True,
        description="Install or remove plugins.",
    ),
}