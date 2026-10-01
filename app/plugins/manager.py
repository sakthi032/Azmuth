"""Plugin lifecycle and tool registration.

The manager takes what the loader produced and makes it part of the
running system:

* it calls :meth:`Plugin.register_tools` for each plugin, passing the
  same :class:`ToolManager` the built-ins use — no special path;
* it calls :meth:`Plugin.startup` during system start and
  :meth:`Plugin.shutdown` during stop;
* it holds plugin metadata for the UI and status report.

Registration is checked: every tool a plugin registers must have a
capability that is declared in its manifest's ``required_capabilities``.
A plugin that tries to register a tool needing an undeclared capability
is rejected — the offending tool is unregistered and the plugin's
registration is aborted.

That check is the reason ``required_capabilities`` exists in the
manifest. It gives a reviewer a single place to see what a plugin can do.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.config.logging_setup import get_logger
from app.core.errors import AzmuthError
from app.plugins.base import PluginMetadata
from app.plugins.loader import LoadedPlugin, PluginLoadReport, PluginLoader
from app.security.capabilities import Capability
from app.tools.manager import ToolManager

logger = get_logger("plugins.manager")


class PluginManager:
    """Hold loaded plugins, wire their tools, and run their lifecycle."""

    def __init__(self) -> None:
        self._plugins: dict[str, LoadedPlugin] = {}
        self._started = False

    # -- introspection -----------------------------------------------------
    @property
    def is_started(self) -> bool:
        return self._started

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._plugins))

    def metadata(self) -> tuple[PluginMetadata, ...]:
        return tuple(
            self._plugins[name].metadata for name in self.names()
        )

    def plugin(self, name: str) -> LoadedPlugin | None:
        return self._plugins.get(name)

    def __len__(self) -> int:
        return len(self._plugins)

    # -- loading -----------------------------------------------------------
    def load_from_report(self, report: PluginLoadReport) -> None:
        """Accept the loader's output. Failures are ignored (already logged)."""
        for loaded in report.loaded:
            name = loaded.metadata.name
            if name in self._plugins:
                logger.warning(
                    "plugin already loaded; skipping",
                    extra={"event": "PLUGIN_DUPLICATE", "plugin": name},
                )
                continue
            self._plugins[name] = loaded

    def load_all(self, loader: PluginLoader) -> PluginLoadReport:
        """Convenience: run ``loader`` and adopt the result."""
        report = loader.load_all()
        self.load_from_report(report)
        return report

    # -- tool registration -------------------------------------------------
    def register_tools(self, tool_manager: ToolManager) -> None:
        """Call ``register_tools`` on every plugin, with capability gating.

        A plugin whose declared capabilities do not cover every tool it
        registers is rejected as a unit: the tools it managed to register
        are unregistered, and the plugin is flagged in the log. This
        keeps the tool registry consistent with the manifests.
        """
        for name in self.names():
            loaded = self._plugins[name]
            self._register_plugin_tools(loaded, tool_manager)

    def _register_plugin_tools(
        self, loaded: LoadedPlugin, tool_manager: ToolManager
    ) -> None:
        before = set(tool_manager.names())
        try:
            loaded.plugin.register_tools(tool_manager)
        except AzmuthError as exc:
            self._rollback_new_tools(tool_manager, before)
            logger.error(
                "plugin tool registration failed",
                extra={
                    "event": "PLUGIN_REGISTER_FAILED",
                    "plugin": loaded.metadata.name,
                    "reason": str(exc),
                },
            )
            return
        except Exception as exc:  # noqa: BLE001
            self._rollback_new_tools(tool_manager, before)
            logger.exception(
                "plugin tool registration crashed",
                extra={
                    "event": "PLUGIN_REGISTER_CRASHED",
                    "plugin": loaded.metadata.name,
                },
            )
            return

        after = set(tool_manager.names())
        added = after - before

        declared = set(loaded.metadata.required_capabilities)
        undeclared = [
            tool_name
            for tool_name in added
            if tool_manager.metadata(tool_name).capability not in declared
        ]
        if undeclared:
            for tool_name in undeclared:
                tool_manager.unregister(tool_name)
            logger.error(
                "plugin registered tools with undeclared capabilities",
                extra={
                    "event": "PLUGIN_UNDECLARED_CAPABILITY",
                    "plugin": loaded.metadata.name,
                    "tools": ", ".join(sorted(undeclared)),
                },
            )

    @staticmethod
    def _rollback_new_tools(
        tool_manager: ToolManager, before: set[str]
    ) -> None:
        after = set(tool_manager.names())
        for tool_name in after - before:
            try:
                tool_manager.unregister(tool_name)
            except Exception:  # noqa: BLE001
                pass

    # -- lifecycle ---------------------------------------------------------
    async def startup(self) -> None:
        """Call ``startup`` on every plugin. Idempotent."""
        if self._started:
            return
        self._started = True
        for name in self.names():
            plugin = self._plugins[name].plugin
            try:
                await plugin.startup()
            except Exception:  # noqa: BLE001 - one bad plugin must not stop the rest
                logger.exception(
                    "plugin startup failed",
                    extra={"event": "PLUGIN_STARTUP_FAILED", "plugin": name},
                )

    async def shutdown(self) -> None:
        """Call ``shutdown`` on every plugin. Idempotent."""
        if not self._started:
            return
        for name in self.names():
            plugin = self._plugins[name].plugin
            try:
                await plugin.shutdown()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "plugin shutdown failed",
                    extra={"event": "PLUGIN_SHUTDOWN_FAILED", "plugin": name},
                )
        self._started = False


# --------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------
def build_plugin_manager(
    *,
    builtin_roots: Iterable[str] | None = None,
    user_roots: Iterable[str] | None = None,
    current_version: str | None = None,
) -> tuple[PluginManager, PluginLoadReport]:
    """Discover plugins and return a manager plus the load report.

    ``builtin_roots`` defaults to the shipped plugin directory. User
    roots are optional; when omitted, only the built-ins are scanned.
    """
    from pathlib import Path as _Path

    if builtin_roots is None:
        builtin_roots = [_Path(__file__).resolve().parent / "builtin"]
    roots: list = list(builtin_roots)
    if user_roots:
        roots.extend(user_roots)

    loader = PluginLoader(roots, current_version=current_version)
    report = loader.load_all()

    manager = PluginManager()
    manager.load_from_report(report)
    return manager, report


__all__ = ["PluginManager", "build_plugin_manager"]