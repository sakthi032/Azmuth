from __future__ import annotations

from typing import Any, ClassVar

import pytest

from app.core.errors import (
    ConfirmationRequiredError,
    PermissionDeniedError,
    ProviderUnavailableError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolValidationError,
)
from app.core.events import Event, EventBus, EventName
from app.security.capabilities import Capability
from app.security.modes import AssuranceLevel, PermissionMode, Principal
from app.security.permissions import PermissionManager
from app.tools import (
    EchoTool,
    GetTimeTool,
    RiskLevel,
    Tool,
    ToolManager,
    ToolMetadata,
    ToolRegistry,
    ToolResult,
    build_builtin_tools,
    validate_arguments,
)


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
def permissions() -> PermissionManager:
    return PermissionManager()


@pytest.fixture()
def manager(permissions: PermissionManager) -> ToolManager:
    m = ToolManager(permissions)
    for tool in build_builtin_tools():
        m.register(tool)
    return m


# ---------------------------------------------------------------------------
# Schema validator
# ---------------------------------------------------------------------------
def test_validate_accepts_valid_object() -> None:
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer", "minimum": 0}},
        "required": ["n"],
        "additionalProperties": False,
    }
    assert validate_arguments({"n": 5}, schema) == []


def test_validate_reports_missing_required() -> None:
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    errors = validate_arguments({}, schema)
    assert any("missing required property 'n'" in e for e in errors)


def test_validate_reports_wrong_type() -> None:
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    errors = validate_arguments({"n": "five"}, schema)
    assert any("expected 'integer', got string" in e for e in errors)


def test_validate_rejects_unknown_property() -> None:
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "additionalProperties": False,
    }
    errors = validate_arguments({"a": "x", "b": 1}, schema)
    assert any("unexpected property 'b'" in e for e in errors)


def test_validate_boolean_is_not_integer() -> None:
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    errors = validate_arguments({"n": True}, schema)
    assert errors and "expected 'integer'" in errors[0]


def test_validate_number_accepts_int_and_float() -> None:
    schema = {"type": "object", "properties": {"n": {"type": "number"}}}
    assert validate_arguments({"n": 5}, schema) == []
    assert validate_arguments({"n": 5.5}, schema) == []


def test_validate_string_length_bounds() -> None:
    schema = {
        "type": "object",
        "properties": {"s": {"type": "string", "minLength": 2, "maxLength": 4}},
    }
    assert validate_arguments({"s": "ab"}, schema) == []
    assert any("shorter than" in e for e in validate_arguments({"s": "a"}, schema))
    assert any("longer than" in e for e in validate_arguments({"s": "abcde"}, schema))


def test_validate_string_pattern() -> None:
    schema = {"type": "object", "properties": {"s": {"type": "string", "pattern": r"^[a-z]+$"}}}
    assert validate_arguments({"s": "hello"}, schema) == []
    assert any("does not match" in e for e in validate_arguments({"s": "Hello"}, schema))


def test_validate_enum() -> None:
    schema = {"type": "object", "properties": {"c": {"enum": ["red", "green"]}}}
    assert validate_arguments({"c": "red"}, schema) == []
    assert any("must be one of" in e for e in validate_arguments({"c": "blue"}, schema))


def test_validate_numeric_bounds() -> None:
    schema = {"type": "object", "properties": {"n": {"type": "integer", "minimum": 1, "maximum": 3}}}
    assert validate_arguments({"n": 2}, schema) == []
    assert any("less than" in e for e in validate_arguments({"n": 0}, schema))
    assert any("greater than" in e for e in validate_arguments({"n": 4}, schema))


def test_validate_array_items_and_bounds() -> None:
    schema = {
        "type": "object",
        "properties": {
            "xs": {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 3}
        },
    }
    assert validate_arguments({"xs": [1, 2]}, schema) == []
    assert any("fewer than 1" in e for e in validate_arguments({"xs": []}, schema))
    assert any("more than 3" in e for e in validate_arguments({"xs": [1, 2, 3, 4]}, schema))
    errors = validate_arguments({"xs": [1, "two"]}, schema)
    assert any("$.xs[1]" in e and "expected 'integer'" in e for e in errors)


def test_validate_type_union() -> None:
    schema = {"type": "object", "properties": {"v": {"type": ["string", "null"]}}}
    assert validate_arguments({"v": "x"}, schema) == []
    assert validate_arguments({"v": None}, schema) == []
    assert validate_arguments({"v": 5}, schema) != []


def test_validate_ignores_unknown_type_name() -> None:
    # A typo in a schema should not reject valid input — it should simply
    # not enforce that constraint.
    schema = {"type": "object", "properties": {"v": {"type": "date-time"}}}
    assert validate_arguments({"v": "anything"}, schema) == []


def test_validate_no_type_declared_accepts_anything() -> None:
    schema = {"type": "object", "properties": {"v": {}}}
    assert validate_arguments({"v": 1}, schema) == []
    assert validate_arguments({"v": "x"}, schema) == []


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------
def test_registry_register_get_and_names() -> None:
    r = ToolRegistry()
    t = EchoTool()
    r.register(t)
    assert r.has("echo")
    assert r.get("echo") is t
    assert r.names() == ("echo",)
    assert len(r) == 1


def test_registry_rejects_duplicate_name() -> None:
    r = ToolRegistry()
    r.register(EchoTool())
    with pytest.raises(ValueError, match="already registered"):
        r.register(EchoTool())


def test_registry_replace_flag_allows_override() -> None:
    r = ToolRegistry()
    r.register(EchoTool())
    t2 = EchoTool()
    r.register(t2, replace=True)
    assert r.get("echo") is t2


def test_registry_unknown_tool_raises() -> None:
    with pytest.raises(ToolNotFoundError, match="not registered"):
        ToolRegistry().get("nope")


def test_registry_by_capability_filters() -> None:
    r = ToolRegistry()
    r.register(EchoTool())
    r.register(GetTimeTool())
    chat_tools = r.by_capability(Capability.CHAT)
    assert [t.name for t in chat_tools] == ["echo"]


def test_registry_unregister_and_clear() -> None:
    r = ToolRegistry()
    r.register(EchoTool())
    assert r.unregister("echo") is True
    assert r.unregister("echo") is False
    r.register(EchoTool())
    r.clear()
    assert len(r) == 0


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------
def test_tool_metadata_snapshot_is_live_free() -> None:
    md = EchoTool.metadata()
    assert isinstance(md, ToolMetadata)
    assert md.name == "echo"
    assert md.capability is Capability.CHAT
    assert md.risk_level is RiskLevel.LOW
    assert md.requires_confirmation is False
    assert md.dangerous is False


def test_tool_metadata_as_dict_is_json_ready() -> None:
    payload = GetTimeTool.metadata().as_dict()
    assert payload["name"] == "get_time"
    assert payload["capability"] == "use_basic_tools"
    assert payload["risk_level"] == "low"
    assert payload["dangerous"] is False


# ---------------------------------------------------------------------------
# ToolManager — registration & discovery
# ---------------------------------------------------------------------------
def test_manager_registers_and_lists_tools(manager: ToolManager) -> None:
    assert set(manager.names()) == {"echo", "get_time"}


def test_list_metadata_filters_by_principal(manager: ToolManager, restricted: Principal) -> None:
    names = {m.name for m in manager.list_metadata(principal=restricted)}
    assert names == {"echo", "get_time"}


def test_list_metadata_excludes_tools_the_principal_cannot_use(
    permissions: PermissionManager, restricted: Principal
) -> None:
    class DeleteTool(Tool):
        name = "delete"
        description = "delete a file"
        parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}
        capability = Capability.DELETE_FILE
        async def execute(self) -> str:
            return "deleted"

    m = ToolManager(permissions)
    m.register(EchoTool())
    m.register(DeleteTool())

    restricted_names = {md.name for md in m.list_metadata(principal=restricted)}
    assert restricted_names == {"echo"}


def test_list_metadata_includes_creator_only_for_creator(
    permissions: PermissionManager, creator: Principal
) -> None:
    class DeleteTool(Tool):
        name = "delete"
        description = "delete a file"
        parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}
        capability = Capability.DELETE_FILE
        async def execute(self) -> str:
            return "deleted"

    m = ToolManager(permissions)
    m.register(DeleteTool())

    names = {md.name for md in m.list_metadata(principal=creator)}
    assert names == {"delete"}


def test_list_metadata_skips_unavailable_tools(
    permissions: PermissionManager
) -> None:
    class Broken(Tool):
        name = "broken"
        description = "always unavailable"
        parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}
        capability = Capability.USE_BASIC_TOOLS
        def is_available(self) -> bool:
            return False
        async def execute(self) -> None:
            return None

    m = ToolManager(permissions)
    m.register(EchoTool())
    m.register(Broken())
    assert {md.name for md in m.list_metadata()} == {"echo"}


# ---------------------------------------------------------------------------
# ToolManager.invoke — happy path
# ---------------------------------------------------------------------------
async def _run(coro):  # noqa: ANN001 - test helper
    return await coro


def test_invoke_echo_with_valid_arguments(
    manager: ToolManager, restricted: Principal
) -> None:
    import asyncio
    result = asyncio.run(
        manager.invoke("echo", {"message": "hello"}, principal=restricted)
    )
    assert isinstance(result, ToolResult)
    assert result.success is True
    assert result.output == "hello"
    assert result.tool == "echo"
    assert result.duration_ms >= 0


def test_invoke_get_time_returns_iso(
    manager: ToolManager, restricted: Principal
) -> None:
    import asyncio
    result = asyncio.run(manager.invoke("get_time", {}, principal=restricted))
    assert result.success is True
    assert "iso" in result.output and "epoch" in result.output


# ---------------------------------------------------------------------------
# ToolManager.invoke — selection failures
# ---------------------------------------------------------------------------
def test_invoke_unknown_tool(manager: ToolManager, restricted: Principal) -> None:
    import asyncio
    with pytest.raises(ToolNotFoundError):
        asyncio.run(manager.invoke("nope", {}, principal=restricted))


def test_invoke_unavailable_tool(permissions: PermissionManager, restricted: Principal) -> None:
    class Unavailable(Tool):
        name = "unavailable"
        description = "x"
        parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}
        capability = Capability.USE_BASIC_TOOLS
        def is_available(self) -> bool:
            return False
        async def execute(self) -> None:
            return None

    m = ToolManager(permissions)
    m.register(Unavailable())
    import asyncio
    with pytest.raises(ProviderUnavailableError, match="not available"):
        asyncio.run(m.invoke("unavailable", {}, principal=restricted))


# ---------------------------------------------------------------------------
# ToolManager.invoke — permission gate
# ---------------------------------------------------------------------------
def test_invoke_denied_for_restricted_principal(
    permissions: PermissionManager, restricted: Principal
) -> None:
    class DeleteTool(Tool):
        name = "delete_file"
        description = "delete a file"
        parameters: ClassVar[dict[str, Any]] = {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }
        capability = Capability.DELETE_FILE
        async def execute(self, path: str) -> str:
            return f"deleted {path}"

    m = ToolManager(permissions)
    m.register(DeleteTool())
    import asyncio

    # Permission runs *before* validation — invalid args do not change
    # the fact that this principal is not allowed.
    with pytest.raises(PermissionDeniedError):
        asyncio.run(m.invoke("delete_file", {}, principal=restricted))
    with pytest.raises(PermissionDeniedError):
        asyncio.run(m.invoke("delete_file", {"path": "/x"}, principal=restricted))


def test_invoke_creator_allowed_but_needs_confirmation(
    permissions: PermissionManager, creator: Principal
) -> None:
    class DeleteTool(Tool):
        name = "delete_file"
        description = "delete a file"
        parameters: ClassVar[dict[str, Any]] = {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }
        capability = Capability.DELETE_FILE
        async def execute(self, path: str) -> str:
            return f"deleted {path}"

    m = ToolManager(permissions)
    m.register(DeleteTool())
    import asyncio

    with pytest.raises(ConfirmationRequiredError):
        asyncio.run(
            m.invoke("delete_file", {"path": "/x"}, principal=creator, confirmed=False)
        )

    result = asyncio.run(
        m.invoke("delete_file", {"path": "/x"}, principal=creator, confirmed=True)
    )
    assert result.output == "deleted /x"


def test_tool_can_demand_confirmation_it_would_not_otherwise_need(
    permissions: PermissionManager, creator: Principal
) -> None:
    class Sensitive(Tool):
        name = "sensitive"
        description = "x"
        parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}
        capability = Capability.CHAT           # policy does not require confirmation
        requires_confirmation = True           # but the tool does
        async def execute(self) -> str:
            return "done"

    m = ToolManager(permissions)
    m.register(Sensitive())
    import asyncio

    with pytest.raises(ConfirmationRequiredError):
        asyncio.run(m.invoke("sensitive", {}, principal=creator))
    result = asyncio.run(m.invoke("sensitive", {}, principal=creator, confirmed=True))
    assert result.success is True


# ---------------------------------------------------------------------------
# ToolManager.invoke — validation gate
# ---------------------------------------------------------------------------
def test_invoke_rejects_invalid_arguments(
    manager: ToolManager, restricted: Principal
) -> None:
    import asyncio
    with pytest.raises(ToolValidationError, match="message"):
        asyncio.run(manager.invoke("echo", {}, principal=restricted))
    with pytest.raises(ToolValidationError):
        asyncio.run(manager.invoke("echo", {"message": ""}, principal=restricted))
    with pytest.raises(ToolValidationError):
        asyncio.run(manager.invoke("echo", {"message": "hi", "extra": 1}, principal=restricted))


# ---------------------------------------------------------------------------
# ToolManager.invoke — execution failures
# ---------------------------------------------------------------------------
def test_invoke_wraps_tool_exception(
    permissions: PermissionManager, restricted: Principal
) -> None:
    class Boom(Tool):
        name = "boom"
        description = "raises"
        parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}
        capability = Capability.USE_BASIC_TOOLS
        async def execute(self) -> None:
            raise RuntimeError("inner failure")

    m = ToolManager(permissions)
    m.register(Boom())
    import asyncio

    with pytest.raises(ToolExecutionError, match="inner failure"):
        asyncio.run(m.invoke("boom", {}, principal=restricted))


def test_sync_tool_execute_is_awaited(
    permissions: PermissionManager, restricted: Principal
) -> None:
    class SyncTool(Tool):
        name = "sync"
        description = "sync"
        parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}
        capability = Capability.USE_BASIC_TOOLS
        async def execute(self) -> str:
            return "ok"

    m = ToolManager(permissions)
    m.register(SyncTool())
    import asyncio
    assert asyncio.run(m.invoke("sync", {}, principal=restricted)).output == "ok"


def test_tool_returning_tool_result_preserves_metadata(
    permissions: PermissionManager, restricted: Principal
) -> None:
    class Custom(Tool):
        name = "custom"
        description = "x"
        parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}
        capability = Capability.USE_BASIC_TOOLS
        async def execute(self) -> ToolResult:
            return ToolResult(
                success=True, tool="custom", output="ok", metadata={"note": "hi"}
            )

    m = ToolManager(permissions)
    m.register(Custom())
    import asyncio
    result = asyncio.run(m.invoke("custom", {}, principal=restricted))
    assert result.output == "ok"
    assert result.metadata == {"note": "hi"}
    assert result.tool == "custom"
    assert result.duration_ms >= 0


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------
def test_invoke_emits_started_and_completed_events(
    permissions: PermissionManager, restricted: Principal
) -> None:
    import asyncio
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe(EventName.TOOL_STARTED, seen.append)
    bus.subscribe(EventName.TOOL_COMPLETED, seen.append)

    m = ToolManager(permissions, event_bus=bus)
    m.register(EchoTool())

    asyncio.run(m.invoke("echo", {"message": "hi"}, principal=restricted))
    asyncio.run(asyncio.sleep(0))

    names = [e.name for e in seen]
    assert EventName.TOOL_STARTED in names
    assert EventName.TOOL_COMPLETED in names
    started = next(e for e in seen if e.name is EventName.TOOL_STARTED)
    assert started.payload["tool"] == "echo"


def test_failed_tool_still_emits_completed_event(
    permissions: PermissionManager, restricted: Principal
) -> None:
    import asyncio

    class Boom(Tool):
        name = "boom"
        description = "x"
        parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}
        capability = Capability.USE_BASIC_TOOLS
        async def execute(self) -> None:
            raise RuntimeError("bad")

    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe(EventName.TOOL_COMPLETED, seen.append)

    m = ToolManager(permissions, event_bus=bus)
    m.register(Boom())

    with pytest.raises(ToolExecutionError):
        asyncio.run(m.invoke("boom", {}, principal=restricted))
    asyncio.run(asyncio.sleep(0))

    assert any(e.payload.get("success") is False for e in seen)


def test_event_bus_failure_does_not_break_invocation(
    permissions: PermissionManager, restricted: Principal
) -> None:
    import asyncio

    class BrokenBus(EventBus):
        async def publish(self, event: Event) -> None:  # type: ignore[override]
            raise RuntimeError("bus down")

    m = ToolManager(permissions, event_bus=BrokenBus())
    m.register(EchoTool())

    result = asyncio.run(m.invoke("echo", {"message": "hi"}, principal=restricted))
    assert result.success is True