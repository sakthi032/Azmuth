"""Plugin manifest schema, parser, and validation.

The manifest is a small JSON document called ``plugin.json`` that sits
beside a plugin's Python module. It declares:

* who the plugin is (name, version, description),
* what it needs from Azmuth (minimum core version, required capabilities),
* where the entry point lives (module file + class name).

Validation uses the same JSON-schema subset as the tool system (STEP 8),
so a malformed manifest produces a list of specific, human-readable
errors rather than a traceback.

Two gates are enforced during parsing:

* the ``min_azmuth_version`` must not be above the running core version;
* every entry in ``required_capabilities`` must be a member of the
  :class:`~app.security.capabilities.Capability` enum.

Both are checked here, at parse time, so the loader never sees a
manifest it cannot trust.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from app.core.errors import ConfigurationError
from app.security.capabilities import Capability
from app.tools.schema import validate as validate_schema

#: The version of Azmuth this build represents. Imported lazily inside
#: :func:`_current_version` to avoid a circular import at module load.
CORE_VERSION_FALLBACK: Final[str] = "0.0.0"


def _current_version() -> str:
    from app import __version__
    return __version__


#: JSON Schema for the manifest document.
MANIFEST_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 64, "pattern": r"^[a-z][a-z0-9_-]*$"},
        "version": {"type": "string", "minLength": 1, "maxLength": 32},
        "description": {"type": "string", "maxLength": 512},
        "author": {"type": "string", "maxLength": 128},
        "min_azmuth_version": {"type": "string", "maxLength": 32},
        "required_capabilities": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 32,
        },
        "entry_point": {
            "type": "string",
            "minLength": 1,
            "maxLength": 256,
            "pattern": r"^[A-Za-z_][\w./]*\.py:[A-Za-z_]\w*$",
        },
    },
    "required": ["name", "version", "description", "entry_point"],
    "additionalProperties": True,
}


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """Parsed, validated plugin manifest."""

    name: str
    version: str
    description: str
    entry_point: str
    author: str | None = None
    min_azmuth_version: str = "0.0.0"
    required_capabilities: tuple[Capability, ...] = field(default_factory=tuple)
    extra: dict[str, Any] = field(default_factory=dict)

    # -- entry point decomposition ----------------------------------------
    @property
    def module_file(self) -> str:
        """The ``module.py`` half of ``module.py:ClassName``."""
        return self.entry_point.split(":", 1)[0]

    @property
    def class_name(self) -> str:
        """The ``ClassName`` half of ``module.py:ClassName``."""
        return self.entry_point.split(":", 1)[1]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "min_azmuth_version": self.min_azmuth_version,
            "required_capabilities": [c.value for c in self.required_capabilities],
            "entry_point": self.entry_point,
        }


# --------------------------------------------------------------------------
# Version comparison
# --------------------------------------------------------------------------
def parse_version(text: str) -> tuple[int, ...]:
    """Best-effort numeric tuple from a version string.

    Handles ``"1.2.3"``, ``"1.2.3-beta"``, ``"1.2.3+build42"``, and
    ``"1.2"``. Non-numeric segments terminate parsing. Never raises.
    """
    if not text:
        return (0,)
    core = text.split("-", 1)[0].split("+", 1)[0].strip()
    parts: list[int] = []
    for chunk in core.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            break
    return tuple(parts) or (0,)


def version_satisfies(required: str, current: str) -> bool:
    """True when ``current >= required`` under tuple comparison.

    Pads the shorter tuple with zeros so ``(0, 1)`` and ``(0, 1, 0)``
    compare equal, matching the usual reading of semver.
    """
    req = parse_version(required)
    cur = parse_version(current)
    width = max(len(req), len(cur))
    req_padded = req + (0,) * (width - len(req))
    cur_padded = cur + (0,) * (width - len(cur))
    return cur_padded >= req_padded


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def parse_manifest(
    path: str | Path,
    *,
    current_version: str | None = None,
    enforce_version: bool = True,
) -> PluginManifest:
    """Read and validate ``plugin.json``.

    Raises :class:`ConfigurationError` on any problem. The message is
    always specific enough to log without further context.
    """
    manifest_path = Path(path)
    if not manifest_path.is_file():
        raise ConfigurationError(f"plugin manifest not found: {manifest_path}")

    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(
            f"could not read plugin manifest {manifest_path}: {exc}"
        ) from exc

    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"plugin manifest {manifest_path} is not valid JSON: {exc}"
        ) from exc

    if not isinstance(document, dict):
        raise ConfigurationError(
            f"plugin manifest {manifest_path} must be a JSON object"
        )

    schema_errors = validate_schema(document, MANIFEST_SCHEMA)
    if schema_errors:
        joined = "; ".join(schema_errors)
        raise ConfigurationError(
            f"plugin manifest {manifest_path} is invalid: {joined}"
        )

    required_capabilities = _resolve_capabilities(
        document.get("required_capabilities") or [],
        manifest_path=manifest_path,
    )

    min_version = str(document.get("min_azmuth_version") or "0.0.0")
    if enforce_version:
        running = current_version if current_version is not None else _current_version()
        if not version_satisfies(min_version, running):
            raise ConfigurationError(
                f"plugin {document['name']!r} requires Azmuth >= "
                f"{min_version}, but this build is {running}"
            )

    extra = {
        key: value
        for key, value in document.items()
        if key not in {
            "name", "version", "description", "author",
            "min_azmuth_version", "required_capabilities", "entry_point",
        }
    }

    return PluginManifest(
        name=str(document["name"]),
        version=str(document["version"]),
        description=str(document["description"]),
        entry_point=str(document["entry_point"]),
        author=document.get("author"),
        min_azmuth_version=min_version,
        required_capabilities=required_capabilities,
        extra=extra,
    )


def _resolve_capabilities(
    values: object, *, manifest_path: Path
) -> tuple[Capability, ...]:
    if not isinstance(values, list):
        raise ConfigurationError(
            f"plugin manifest {manifest_path}: required_capabilities must be a list"
        )
    resolved: list[Capability] = []
    seen: set[Capability] = set()
    for index, value in enumerate(values):
        if not isinstance(value, str):
            raise ConfigurationError(
                f"plugin manifest {manifest_path}: required_capabilities[{index}] "
                f"must be a string, got {type(value).__name__}"
            )
        try:
            capability = Capability(value)
        except ValueError as exc:
            known = ", ".join(sorted(c.value for c in Capability))
            raise ConfigurationError(
                f"plugin manifest {manifest_path}: unknown capability "
                f"{value!r}. Known capabilities: {known}"
            ) from exc
        if capability not in seen:
            seen.add(capability)
            resolved.append(capability)
    return tuple(resolved)