"""Plugin subsystem public API.

    from app.plugins import build_plugin_manager, PluginManager, Plugin

Plugins extend Azmuth's tool surface without touching core files. Every
plugin tool goes through the same :class:`ToolManager` pipeline as a
built-in — permissions, validation, and confirmation all apply.
"""

from app.plugins.base import Plugin, PluginMetadata
from app.plugins.loader import (
    LoadedPlugin,
    PluginLoadFailure,
    PluginLoadReport,
    PluginLoader,
)
from app.plugins.manager import PluginManager, build_plugin_manager
from app.plugins.manifest import (
    MANIFEST_SCHEMA,
    PluginManifest,
    parse_manifest,
    parse_version,
    version_satisfies,
)

__all__ = [
    "LoadedPlugin",
    "MANIFEST_SCHEMA",
    "Plugin",
    "PluginLoadFailure",
    "PluginLoadReport",
    "PluginLoader",
    "PluginManager",
    "PluginManifest",
    "PluginMetadata",
    "build_plugin_manager",
    "parse_manifest",
    "parse_version",
    "version_satisfies",
]