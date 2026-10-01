"""The Plugin interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.security.capabilities import Capability
from app.tools.manager import ToolManager


@dataclass(frozen=True, slots=True)
class PluginMetadata:
    """Runtime-visible description of a loaded plugin.

    Constructed from the plugin's manifest plus the directory it was
    loaded from. The UI and status report use this; the plugin itself
    does not create it.
    """

    name: str
    version: str
    description: str
    directory: str
    author: str | None = None
    min_azmuth_version: str = "0.0.0"
    required_capabilities: tuple[Capability, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "directory": self.directory,
            "min_azmuth_version": self.min_azmuth_version,
            "required_capabilities": [c.value for c in self.required_capabilities],
        }


class Plugin(ABC):
    """Base class for every Azmuth plugin.

    A plugin **must not**:

    * reach into ``app.core`` internals — it uses the public APIs it is
      handed (a ``ToolManager``), nothing else;
    * register capabilities the manifest did not declare — the loader
      enforces this by checking each registered tool's capability against
      the manifest's ``required_capabilities``;
    * perform privileged work in ``__init__`` — construction must be
      cheap and side-effect-free. Use :meth:`startup` for setup.

    A plugin **may**:

    * register any number of tools via :meth:`register_tools`;
    * keep its own internal state between tool calls;
    * perform async setup/teardown in :meth:`startup` / :meth:`shutdown`.

    The base class provides no-op implementations of the two lifecycle
    hooks so a plugin that needs neither can just implement
    :meth:`register_tools`.
    """

    #: Populated by the loader after construction. Plugins must read the
    #: manifest via this attribute rather than keeping a copy of their
    #: own — the manifest is the source of truth the loader validated.
    manifest: "PluginManifest"  # forward ref; imported lazily below

    # -- lifecycle ---------------------------------------------------------
    async def startup(self) -> None:
        """Optional setup hook. Default is a no-op."""

    async def shutdown(self) -> None:
        """Optional teardown hook. Default is a no-op. Must be idempotent."""

    # -- tool registration -------------------------------------------------
    @abstractmethod
    def register_tools(self, tool_manager: ToolManager) -> None:
        """Register this plugin's tools with ``tool_manager``.

        The tool manager will apply the full tool pipeline (permission
        check, argument validation, confirmation) to every invocation —
        plugin tools are not special in any way.
        """


# Late import to avoid a cycle with manifest importing Capability.
from app.plugins.manifest import PluginManifest  # noqa: E402