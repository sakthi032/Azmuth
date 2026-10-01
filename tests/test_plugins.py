from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, ClassVar

import pytest

from app.core.errors import ConfigurationError
from app.plugins import (
    LoadedPlugin,
    Plugin,
    PluginLoadReport,
    PluginLoader,
    PluginManager,
    PluginManifest,
    PluginMetadata,
    build_plugin_manager,
    parse_manifest,
    parse_version,
    version_satisfies,
)
from app.security.capabilities import Capability
from app.security.modes import AssuranceLevel, PermissionMode, Principal
from app.security.permissions import PermissionManager
from app.tools.base import RiskLevel, Tool
from app.tools.manager import ToolManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def restricted() -> Principal:
    return Principal.anonymous()


@pytest.fixture()
def creator() -> Principal:
    return Principal(
        user_id="creator",
        mode=PermissionMode.CREATOR,
        assurance=AssuranceLevel.HIGH,
        methods=("pin",),
    )


@pytest.fixture()
def tool_manager() -> ToolManager:
    return ToolManager(PermissionManager())


def _run(coro):  # noqa: ANN001
    return asyncio.run(coro)


def _write_plugin(
    directory: Path,
    *,
    name: str = "test-plugin",
    version: str = "1.0.0",
    description: str = "A test plugin.",
    entry_point: str = "plugin.py:TestPlugin",
    min_version: str = "0.1.0",
    capabilities: list[str] | None = None,
    module_body: str = "",
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": name,
        "version": version,
        "description": description,
        "min_azmuth_version": min_version,
        "required_capabilities": capabilities if capabilities is not None else ["chat"],
        "entry_point": entry_point,
    }
    (directory / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")

    if not module_body:
        module_body = (
            "from app.plugins.base import Plugin\n"
            "from app.tools.manager import ToolManager\n"
            "class TestPlugin(Plugin):\n"
            "    def register_tools(self, tool_manager: ToolManager) -> None:\n"
            "        pass\n"
        )
    (directory / "plugin.py").write_text(module_body, encoding="utf-8")
    return directory


# ---------------------------------------------------------------------------
# Version parsing and comparison
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        ("", (0,)),
        ("1", (1,)),
        ("1.2", (1, 2)),
        ("1.2.3", (1, 2, 3)),
        ("1.2.3-beta", (1, 2, 3)),
        ("1.2.3+build", (1, 2, 3)),
        ("1.2.x", (1, 2)),
        ("0.1.0", (0, 1, 0)),
    ],
)
def test_parse_version(text: str, expected: tuple[int, ...]) -> None:
    assert parse_version(text) == expected


@pytest.mark.parametrize(
    "required, current, expected",
    [
        ("1.0.0", "1.0.0", True),
        ("1.0.0", "1.0.1", True),
        ("1.0.1", "1.0.0", False),
        ("0.1.0", "0.1.0", True),
        ("1.0.0", "1.1.0", True),
        ("1.1.0", "1.0.0", False),
        ("2.0.0", "1.99.99", False),
        ("1.2", "1.2.0", True),
        ("1.2.0", "1.2", True),
    ],
)
def test_version_satisfies(required: str, current: str, expected: bool) -> None:
    assert version_satisfies(required, current) is expected


# ---------------------------------------------------------------------------
# parse_manifest — happy path
# ---------------------------------------------------------------------------
def test_parse_manifest_minimal(tmp_path: Path) -> None:
    _write_plugin(tmp_path / "plug")
    manifest = parse_manifest(tmp_path / "plug" / "plugin.json")

    assert manifest.name == "test-plugin"
    assert manifest.version == "1.0.0"
    assert manifest.description == "A test plugin."
    assert manifest.entry_point == "plugin.py:TestPlugin"
    assert manifest.module_file == "plugin.py"
    assert manifest.class_name == "TestPlugin"
    assert manifest.min_azmuth_version == "0.1.0"
    assert manifest.required_capabilities == (Capability.CHAT,)


def test_manifest_roundtrip_as_dict(tmp_path: Path) -> None:
    _write_plugin(tmp_path / "plug")
    manifest = parse_manifest(tmp_path / "plug" / "plugin.json")
    assert manifest.as_dict()["required_capabilities"] == ["chat"]


def test_parse_manifest_deduplicates_capabilities(tmp_path: Path) -> None:
    _write_plugin(tmp_path / "plug", capabilities=["chat", "chat", "web_search"])
    manifest = parse_manifest(tmp_path / "plug" / "plugin.json")
    assert manifest.required_capabilities == (Capability.CHAT, Capability.WEB_SEARCH)


def test_parse_manifest_preserves_extra_fields(tmp_path: Path) -> None:
    manifest_path = tmp_path / "plug" / "plugin.json"
    _write_plugin(tmp_path / "plug")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["homepage"] = "https://example.com"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")

    manifest = parse_manifest(manifest_path)
    assert manifest.extra == {"homepage": "https://example.com"}


# ---------------------------------------------------------------------------
# parse_manifest — validation failures
# ---------------------------------------------------------------------------
def test_parse_manifest_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found"):
        parse_manifest(tmp_path / "plugin.json")


def test_parse_manifest_invalid_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "plugin.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="not valid JSON"):
        parse_manifest(path)


def test_parse_manifest_non_object_raises(tmp_path: Path) -> None:
    path = tmp_path / "plugin.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="must be a JSON object"):
        parse_manifest(path)


def test_parse_manifest_missing_required_field_raises(tmp_path: Path) -> None:
    path = tmp_path / "plugin.json"
    path.write_text(json.dumps({"name": "x"}), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="is invalid"):
        parse_manifest(path)


def test_parse_manifest_invalid_name_pattern_raises(tmp_path: Path) -> None:
    path = tmp_path / "plugin.json"
    path.write_text(
        json.dumps({
            "name": "Has Uppercase",
            "version": "1.0.0",
            "description": "x",
            "entry_point": "plugin.py:Plugin",
        }),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="is invalid"):
        parse_manifest(path)


def test_parse_manifest_unknown_capability_raises(tmp_path: Path) -> None:
    _write_plugin(tmp_path / "plug", capabilities=["chat", "invent_new_capability"])
    with pytest.raises(ConfigurationError, match="unknown capability"):
        parse_manifest(tmp_path / "plug" / "plugin.json")


def test_parse_manifest_rejects_version_above_core(tmp_path: Path) -> None:
    _write_plugin(tmp_path / "plug", min_version="99.0.0")
    with pytest.raises(ConfigurationError, match="requires Azmuth"):
        parse_manifest(tmp_path / "plug" / "plugin.json")


def test_parse_manifest_version_check_can_be_bypassed(tmp_path: Path) -> None:
    _write_plugin(tmp_path / "plug", min_version="99.0.0")
    manifest = parse_manifest(
        tmp_path / "plug" / "plugin.json", enforce_version=False
    )
    assert manifest.min_azmuth_version == "99.0.0"


# ---------------------------------------------------------------------------
# PluginLoader — discovery
# ---------------------------------------------------------------------------
def test_loader_finds_builtin_plugin() -> None:
    roots = [Path(__file__).resolve().parents[1] / "app" / "plugins" / "builtin"]
    loader = PluginLoader(roots)
    report = loader.load_all()

    names = {lp.metadata.name for lp in report.loaded}
    assert "uppercase" in names
    assert report.failure_count == 0


def test_loader_skips_directories_without_manifest(tmp_path: Path) -> None:
    (tmp_path / "not-a-plugin").mkdir()
    (tmp_path / "not-a-plugin" / "readme.txt").write_text("hi")

    loader = PluginLoader([tmp_path])
    report = loader.load_all()
    assert report.loaded_count == 0
    assert report.failure_count == 0  # skipped, not failed


def test_loader_ignores_underscore_and_dot_dirs(tmp_path: Path) -> None:
    (tmp_path / "_hidden").mkdir()
    (tmp_path / ".hidden").mkdir()
    _write_plugin(tmp_path / "_hidden", name="_shouldnotload")
    _write_plugin(tmp_path / ".hidden", name="alsoshouldnotload")

    loader = PluginLoader([tmp_path])
    report = loader.load_all()
    assert report.loaded_count == 0


def test_loader_reports_manifest_failure(tmp_path: Path) -> None:
    plug = tmp_path / "bad"
    plug.mkdir()
    (plug / "plugin.json").write_text("{not json", encoding="utf-8")

    loader = PluginLoader([tmp_path])
    report = loader.load_all()
    assert report.loaded_count == 0
    assert report.failure_count == 1
    assert "not valid JSON" in report.failures[0].reason


def test_loader_reports_missing_module(tmp_path: Path) -> None:
    plug = tmp_path / "missing"
    _write_plugin(plug)
    (plug / "plugin.py").unlink()

    loader = PluginLoader([tmp_path])
    report = loader.load_all()
    assert report.loaded_count == 0
    assert report.failure_count == 1
    assert "entry module not found" in report.failures[0].reason


def test_loader_reports_missing_class(tmp_path: Path) -> None:
    plug = tmp_path / "nocls"
    _write_plugin(
        plug,
        module_body=(
            "from app.plugins.base import Plugin\n"
            "class SomethingElse(Plugin):\n"
            "    def register_tools(self, tool_manager):\n"
            "        pass\n"
        ),
    )
    loader = PluginLoader([tmp_path])
    report = loader.load_all()
    assert report.loaded_count == 0
    assert report.failure_count == 1
    assert "not found in" in report.failures[0].reason


def test_loader_reports_class_not_subclassing_plugin(tmp_path: Path) -> None:
    plug = tmp_path / "notsubclass"
    _write_plugin(
        plug,
        module_body=(
            "class TestPlugin:\n"
            "    def register_tools(self, tool_manager):\n"
            "        pass\n"
        ),
    )
    loader = PluginLoader([tmp_path])
    report = loader.load_all()
    assert report.loaded_count == 0
    assert report.failure_count == 1
    assert "does not subclass" in report.failures[0].reason


def test_loader_reports_constructor_failure(tmp_path: Path) -> None:
    plug = tmp_path / "badctor"
    _write_plugin(
        plug,
        module_body=(
            "from app.plugins.base import Plugin\n"
            "class TestPlugin(Plugin):\n"
            "    def __init__(self):\n"
            "        raise RuntimeError('construction exploded')\n"
            "    def register_tools(self, tool_manager):\n"
            "        pass\n"
        ),
    )
    loader = PluginLoader([tmp_path])
    report = loader.load_all()
    assert report.failure_count == 1
    assert "could not instantiate" in report.failures[0].reason


def test_loader_deduplicates_plugin_names(tmp_path: Path) -> None:
    _write_plugin(tmp_path / "a", name="samename")
    _write_plugin(tmp_path / "b", name="samename")
    loader = PluginLoader([tmp_path])
    report = loader.load_all()
    assert report.loaded_count == 1
    assert report.failure_count == 1
    assert "duplicate plugin name" in report.failures[0].reason


def test_loader_does_not_add_to_sys_path(tmp_path: Path) -> None:
    before = list(sys.path)
    _write_plugin(tmp_path / "plug")
    PluginLoader([tmp_path]).load_all()
    assert sys.path == before


# ---------------------------------------------------------------------------
# PluginLoader — builtin sample
# ---------------------------------------------------------------------------
def test_uppercase_plugin_loads() -> None:
    roots = [Path(__file__).resolve().parents[1] / "app" / "plugins" / "builtin"]
    report = PluginLoader(roots).load_all()
    uppercase = next(
        (lp for lp in report.loaded if lp.metadata.name == "uppercase"), None
    )
    assert uppercase is not None
    assert uppercase.metadata.version == "1.0.0"
    assert uppercase.metadata.required_capabilities == (Capability.CHAT,)


def test_uppercase_plugin_tools_register_and_work(
    tool_manager: ToolManager, restricted: Principal
) -> None:
    roots = [Path(__file__).resolve().parents[1] / "app" / "plugins" / "builtin"]
    report = PluginLoader(roots).load_all()

    manager = PluginManager()
    manager.load_from_report(report)
    manager.register_tools(tool_manager)

    # The tool is registered under its declared name.
    assert "uppercase" in tool_manager.names()

    result = _run(
        tool_manager.invoke(
            "uppercase", {"text": "hello"}, principal=restricted
        )
    )
    assert result.success is True
    assert result.output == "HELLO"


# ---------------------------------------------------------------------------
# PluginManager — capability gating
# ---------------------------------------------------------------------------
def _make_plugin_with_tool(
    tool_capability: Capability,
    declared_capabilities: list[str],
) -> type:
    """Return a Plugin subclass whose tool uses ``tool_capability``."""

    class PluginTool(Tool):
        name: ClassVar[str] = "plugin-tool"
        description: ClassVar[str] = "x"
        parameters: ClassVar[dict[str, Any]] = {
            "type": "object", "properties": {}, "additionalProperties": False,
        }
        capability: ClassVar[Capability] = tool_capability
        risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
        requires_confirmation: ClassVar[bool] = False

        async def execute(self) -> str:
            return "ok"

    class SamplePlugin(Plugin):
        def register_tools(self, tool_manager: ToolManager) -> None:
            tool_manager.register(PluginTool())

    return SamplePlugin


def test_manager_accepts_declared_capability(
    tool_manager: ToolManager
) -> None:
    plugin_cls = _make_plugin_with_tool(Capability.CHAT, ["chat"])
    loaded = LoadedPlugin(
        plugin=plugin_cls(),
        metadata=PluginMetadata(
            name="ok", version="1.0.0", description="x",
            directory="/tmp/ok",
            required_capabilities=(Capability.CHAT,),
        ),
        directory=Path("/tmp/ok"),
    )
    manager = PluginManager()
    manager._plugins["ok"] = loaded
    manager.register_tools(tool_manager)

    assert "plugin-tool" in tool_manager.names()


def test_manager_rejects_undeclared_capability(
    tool_manager: ToolManager
) -> None:
    """A plugin whose tool declares a capability not in the manifest is rolled back."""
    plugin_cls = _make_plugin_with_tool(Capability.DELETE_FILE, ["chat"])
    loaded = LoadedPlugin(
        plugin=plugin_cls(),
        metadata=PluginMetadata(
            name="bad", version="1.0.0", description="x",
            directory="/tmp/bad",
            required_capabilities=(Capability.CHAT,),  # only chat declared
        ),
        directory=Path("/tmp/bad"),
    )
    manager = PluginManager()
    manager._plugins["bad"] = loaded
    manager.register_tools(tool_manager)

    assert "plugin-tool" not in tool_manager.names()


def test_manager_rolls_back_on_registration_exception(
    tool_manager: ToolManager
) -> None:
    class ExplodingPlugin(Plugin):
        def register_tools(self, tool_manager: ToolManager) -> None:
            raise RuntimeError("register_tools exploded")

    loaded = LoadedPlugin(
        plugin=ExplodingPlugin(),
        metadata=PluginMetadata(
            name="boom", version="1.0.0", description="x",
            directory="/tmp/boom",
            required_capabilities=(Capability.CHAT,),
        ),
        directory=Path("/tmp/boom"),
    )
    manager = PluginManager()
    manager._plugins["boom"] = loaded
    manager.register_tools(tool_manager)  # must not raise
    assert tool_manager.names() == ()


# ---------------------------------------------------------------------------
# PluginManager — lifecycle
# ---------------------------------------------------------------------------
def test_manager_startup_calls_all_plugins() -> None:
    calls: list[str] = []

    class P(Plugin):
        def __init__(self, name: str) -> None:
            self._name = name
        def register_tools(self, tool_manager: ToolManager) -> None:
            pass
        async def startup(self) -> None:
            calls.append(f"start:{self._name}")
        async def shutdown(self) -> None:
            calls.append(f"stop:{self._name}")

    manager = PluginManager()
    for name in ("a", "b"):
        manager._plugins[name] = LoadedPlugin(
            plugin=P(name),
            metadata=PluginMetadata(
                name=name, version="1", description="x", directory="/tmp",
            ),
            directory=Path("/tmp"),
        )

    _run(manager.startup())
    assert set(calls) == {"start:a", "start:b"}

    _run(manager.shutdown())
    assert "stop:a" in calls and "stop:b" in calls


def test_manager_startup_is_idempotent() -> None:
    calls: list[str] = []

    class P(Plugin):
        def register_tools(self, tool_manager: ToolManager) -> None:
            pass
        async def startup(self) -> None:
            calls.append("start")

    manager = PluginManager()
    manager._plugins["p"] = LoadedPlugin(
        plugin=P(),
        metadata=PluginMetadata(name="p", version="1", description="x", directory="/t"),
        directory=Path("/t"),
    )
    _run(manager.startup())
    _run(manager.startup())
    assert calls == ["start"]


def test_manager_survives_plugin_startup_failure() -> None:
    class Bad(Plugin):
        def register_tools(self, tool_manager: ToolManager) -> None:
            pass
        async def startup(self) -> None:
            raise RuntimeError("nope")

    class Good(Plugin):
        def register_tools(self, tool_manager: ToolManager) -> None:
            pass
        async def startup(self) -> None:
            pass  # would log if we tracked it

    manager = PluginManager()
    manager._plugins["bad"] = LoadedPlugin(
        plugin=Bad(),
        metadata=PluginMetadata(name="bad", version="1", description="x", directory="/t"),
        directory=Path("/t"),
    )
    manager._plugins["good"] = LoadedPlugin(
        plugin=Good(),
        metadata=PluginMetadata(name="good", version="1", description="x", directory="/t"),
        directory=Path("/t"),
    )
    _run(manager.startup())  # must not raise
    assert manager.is_started is True


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def test_build_plugin_manager_finds_builtins() -> None:
    manager, report = build_plugin_manager()
    assert "uppercase" in manager.names()
    assert report.loaded_count >= 1


# ---------------------------------------------------------------------------
# LoadedPlugin / metadata types
# ---------------------------------------------------------------------------
def test_plugin_metadata_as_dict_is_json_ready() -> None:
    import json
    md = PluginMetadata(
        name="x", version="1", description="d", directory="/tmp",
        required_capabilities=(Capability.CHAT,),
    )
    payload = json.loads(json.dumps(md.as_dict()))
    assert payload["required_capabilities"] == ["chat"]


def test_plugin_load_report_as_dict() -> None:
    report = PluginLoadReport()
    assert report.as_dict() == {"loaded": [], "failures": []}


# ---------------------------------------------------------------------------
# The path policy: no changes to core files are needed
# ---------------------------------------------------------------------------
def test_adding_a_plugin_does_not_touch_core(
    tmp_path: Path, tool_manager: ToolManager, restricted: Principal
) -> None:
    """A new plugin works by dropping files in a directory — no core edits."""
    _write_plugin(
        tmp_path / "greeting",
        name="greeting",
        capabilities=["chat"],
        module_body=(
            "from app.plugins.base import Plugin\n"
            "from app.security.capabilities import Capability\n"
            "from app.tools.base import RiskLevel, Tool\n"
            "from app.tools.manager import ToolManager\n"
            "from typing import Any, ClassVar\n"
            "\n"
            "class GreetTool(Tool):\n"
            "    name: ClassVar[str] = 'greet'\n"
            "    description: ClassVar[str] = 'greet'\n"
            "    parameters: ClassVar[dict] = {\n"
            "        'type': 'object',\n"
            "        'properties': {'name': {'type': 'string'}},\n"
            "        'required': ['name'],\n"
            "        'additionalProperties': False,\n"
            "    }\n"
            "    capability: ClassVar[Capability] = Capability.CHAT\n"
            "    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW\n"
            "    async def execute(self, *, name: str) -> str:\n"
            "        return f'Hello, {name}!'\n"
            "\n"
            "class TestPlugin(Plugin):\n"
            "    def register_tools(self, tool_manager: ToolManager) -> None:\n"
            "        tool_manager.register(GreetTool())\n"
        ),
    )

    manager, report = build_plugin_manager(
        builtin_roots=[tmp_path], user_roots=None
    )
    assert report.loaded_count == 1
    manager.register_tools(tool_manager)

    result = _run(
        tool_manager.invoke("greet", {"name": "Azmuth"}, principal=restricted)
    )
    assert result.output == "Hello, Azmuth!"