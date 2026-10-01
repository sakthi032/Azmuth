"""Discovery and loading of plugins.

The loader scans one or more plugin roots for subdirectories containing a
``plugin.json`` manifest. For each one it:

1. parses and validates the manifest (see :mod:`app.plugins.manifest`),
2. loads the Python module referenced by ``entry_point``,
3. finds the class in that module,
4. instantiates it with no arguments,
5. attaches the manifest as ``plugin.manifest``,
6. verifies the plugin can be constructed as declared.

The loader **does not** call :meth:`Plugin.register_tools` — that is
the manager's job, so the loader can be used to *inspect* a plugin
directory without modifying any global state.

Failures are collected, not raised. A single broken plugin should not
prevent the rest of the system from starting; the caller receives a
:class:`PluginLoadReport` listing successes and failures with reasons.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from app.config.logging_setup import get_logger
from app.core.errors import ConfigurationError
from app.plugins.base import Plugin, PluginMetadata
from app.plugins.manifest import PluginManifest, parse_manifest

logger = get_logger("plugins.loader")

_MANIFEST_FILENAME = "plugin.json"


@dataclass(frozen=True, slots=True)
class LoadedPlugin:
    """A successfully loaded plugin plus its metadata."""

    plugin: Plugin
    metadata: PluginMetadata
    directory: Path


@dataclass(frozen=True, slots=True)
class PluginLoadFailure:
    """One plugin that could not be loaded, with the reason."""

    directory: Path
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {"directory": str(self.directory), "reason": self.reason}


@dataclass
class PluginLoadReport:
    """Aggregated outcome of one scan across all plugin roots."""

    loaded: list[LoadedPlugin] = field(default_factory=list)
    failures: list[PluginLoadFailure] = field(default_factory=list)

    @property
    def loaded_count(self) -> int:
        return len(self.loaded)

    @property
    def failure_count(self) -> int:
        return len(self.failures)

    def as_dict(self) -> dict[str, object]:
        return {
            "loaded": [lp.metadata.as_dict() for lp in self.loaded],
            "failures": [f.as_dict() for f in self.failures],
        }


class PluginLoader:
    """Discover and load plugins from one or more directories."""

    def __init__(
        self,
        roots: Iterable[str | Path],
        *,
        current_version: str | None = None,
    ) -> None:
        self._roots: tuple[Path, ...] = tuple(
            Path(root).expanduser() for root in roots
        )
        self._current_version = current_version

    # -- introspection -----------------------------------------------------
    @property
    def roots(self) -> tuple[Path, ...]:
        return self._roots

    # -- loading -----------------------------------------------------------
    def load_all(self) -> PluginLoadReport:
        """Scan every root; return a report. Never raises."""
        report = PluginLoadReport()
        seen_names: set[str] = set()

        for root in self._roots:
            if not root.is_dir():
                continue
            for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
                if not entry.is_dir():
                    continue
                if entry.name.startswith("_") or entry.name.startswith("."):
                    continue
                self._load_one(entry, report, seen_names)
        return report

    # -- internals ---------------------------------------------------------
    def _load_one(
        self,
        directory: Path,
        report: PluginLoadReport,
        seen_names: set[str],
    ) -> None:
        manifest_path = directory / _MANIFEST_FILENAME
        if not manifest_path.is_file():
            # Not a plugin directory — silently skip. This is the expected
            # state for any helper folder a plugin author happens to keep.
            return

        # ---- manifest ----------------------------------------------------
        try:
            manifest = parse_manifest(
                manifest_path,
                current_version=self._current_version,
            )
        except ConfigurationError as exc:
            report.failures.append(
                PluginLoadFailure(directory=directory, reason=str(exc))
            )
            logger.warning(
                "plugin manifest rejected",
                extra={
                    "event": "PLUGIN_MANIFEST_REJECTED",
                    "directory": str(directory),
                    "reason": str(exc),
                },
            )
            return

        if manifest.name in seen_names:
            report.failures.append(
                PluginLoadFailure(
                    directory=directory,
                    reason=f"duplicate plugin name {manifest.name!r}",
                )
            )
            return

        # ---- module ------------------------------------------------------
        module_path = directory / manifest.module_file
        if not module_path.is_file():
            report.failures.append(
                PluginLoadFailure(
                    directory=directory,
                    reason=f"entry module not found: {manifest.module_file}",
                )
            )
            return

        try:
            module = _import_module(module_path, manifest.name)
        except Exception as exc:  # noqa: BLE001 - import errors are diverse
            report.failures.append(
                PluginLoadFailure(
                    directory=directory,
                    reason=f"failed to import {manifest.module_file}: {exc}",
                )
            )
            logger.exception(
                "plugin import failed",
                extra={
                    "event": "PLUGIN_IMPORT_FAILED",
                    "plugin": manifest.name,
                    "directory": str(directory),
                },
            )
            return

        # ---- class -------------------------------------------------------
        try:
            plugin_class = getattr(module, manifest.class_name)
        except AttributeError:
            report.failures.append(
                PluginLoadFailure(
                    directory=directory,
                    reason=(
                        f"class {manifest.class_name!r} not found in "
                        f"{manifest.module_file}"
                    ),
                )
            )
            return

        if not isinstance(plugin_class, type) or not issubclass(plugin_class, Plugin):
            report.failures.append(
                PluginLoadFailure(
                    directory=directory,
                    reason=(
                        f"{manifest.class_name} does not subclass "
                        f"app.plugins.base.Plugin"
                    ),
                )
            )
            return

        # ---- instantiation ----------------------------------------------
        try:
            plugin = plugin_class()
        except Exception as exc:  # noqa: BLE001
            report.failures.append(
                PluginLoadFailure(
                    directory=directory,
                    reason=f"could not instantiate {manifest.class_name}: {exc}",
                )
            )
            return

        # Attach the manifest so the plugin can read it without keeping
        # its own copy. Set on the instance, not the class, so multiple
        # plugins loaded from different directories cannot collide.
        try:
            plugin.manifest = manifest  # type: ignore[assignment]
        except Exception as exc:  # noqa: BLE001 - frozen instance, __slots__, etc.
            report.failures.append(
                PluginLoadFailure(
                    directory=directory,
                    reason=f"could not attach manifest to plugin: {exc}",
                )
            )
            return

        metadata = PluginMetadata(
            name=manifest.name,
            version=manifest.version,
            description=manifest.description,
            author=manifest.author,
            directory=str(directory),
            min_azmuth_version=manifest.min_azmuth_version,
            required_capabilities=manifest.required_capabilities,
        )
        report.loaded.append(
            LoadedPlugin(plugin=plugin, metadata=metadata, directory=directory)
        )
        seen_names.add(manifest.name)

        logger.info(
            "plugin loaded",
            extra={
                "event": "PLUGIN_LOADED",
                "plugin": manifest.name,
                "version": manifest.version,
            },
        )


# --------------------------------------------------------------------------
# Module import helper
# --------------------------------------------------------------------------
def _import_module(module_path: Path, plugin_name: str) -> ModuleType:
    """Import ``module_path`` under a synthetic, unique module name.

    The module name is derived from the plugin name and the file stem so
    two plugins with the same file name do not collide in ``sys.modules``.
    Loading happens via :mod:`importlib.util` from the file path, not
    from ``sys.path`` — the plugin directory is not added to the import
    path, which keeps the surrounding process namespace clean.
    """
    module_name = f"azmuth_plugin_{plugin_name}_{module_path.stem}"
    # If somehow the module is already loaded (repeated load_all in a
    # long-running process), drop it so we get a fresh instance.
    sys.modules.pop(module_name, None)

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not build import spec for {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


__all__ = [
    "LoadedPlugin",
    "PluginLoadFailure",
    "PluginLoadReport",
    "PluginLoader",
]