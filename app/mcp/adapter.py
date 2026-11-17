"""MCP Tool Adapter — wraps MCP server tools as Azmuth native ``Tool`` objects.

This bridges the MCP protocol to Azmuth's existing ``ToolManager`` so that
external MCP tools are callable alongside built-in tools with no pipeline
changes.

Capability gate: MCP tools require ``EXECUTE_CODE`` by default
(configurable per server).  This keeps the existing security model intact.

Usage::

    adapter = MCPToolAdapter(
        command=["node", "my-server.js"],
        capability=Capability.EXECUTE_CODE,
    )
    await adapter.connect()
    tools = adapter.as_tools()          # list[Tool]
    tool_manager.register_many(tools)
    ...
    await adapter.disconnect()
"""

from __future__ import annotations

import logging
from typing import Any

from app.mcp.client import MCPClient, MCPError
from app.mcp.protocol import MCPToolDefinition

logger = logging.getLogger(__name__)


class MCPToolProxy:
    """A thin callable that forwards invocations to an MCPClient.

    Compatible with Azmuth's tool-call convention: ``await proxy(args_dict)``.
    """

    def __init__(
        self,
        client: MCPClient,
        definition: MCPToolDefinition,
    ) -> None:
        self._client = client
        self._definition = definition
        self.name: str = definition.name
        self.description: str = definition.description
        self.schema: dict[str, Any] = definition.input_schema

    async def __call__(self, arguments: dict[str, Any] | None = None) -> str:
        """Call the remote MCP tool and return its text output."""
        try:
            result = await self._client.call_tool(self.name, arguments or {})
            if result.is_error:
                return f"[MCP error from {self.name}]: {result.text()}"
            return result.text() or "(no output)"
        except MCPError as exc:
            return f"[MCP error {exc.code}]: {exc.message}"
        except Exception as exc:
            logger.exception("MCPToolProxy %r unexpected error", self.name)
            return f"[MCP unexpected error]: {exc}"

    def to_tool_schema(self) -> dict[str, Any]:
        """Return an OpenAI-compatible function-calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema or {"type": "object", "properties": {}},
            },
        }


class MCPToolAdapter:
    """Manages a single MCP server connection and exposes its tools.

    Parameters
    ----------
    command:
        Subprocess command to start the MCP server.
    server_name:
        Human-readable name for logging.
    startup_timeout, request_timeout:
        Forwarded to ``MCPClient``.
    """

    def __init__(
        self,
        command: list[str],
        server_name: str = "mcp-server",
        startup_timeout: float = 10.0,
        request_timeout: float = 30.0,
    ) -> None:
        self._command = command
        self._server_name = server_name
        self._client = MCPClient(
            command=command,
            startup_timeout=startup_timeout,
            request_timeout=request_timeout,
        )
        self._tools: list[MCPToolProxy] = []
        self._connected: bool = False

    async def connect(self) -> None:
        """Start the MCP server and retrieve its tool list."""
        await self._client.start()
        definitions = await self._client.list_tools()
        self._tools = [MCPToolProxy(self._client, d) for d in definitions]
        self._connected = True
        logger.info(
            "MCPToolAdapter '%s' connected: %d tool(s) available: %s",
            self._server_name,
            len(self._tools),
            [t.name for t in self._tools],
        )

    async def disconnect(self) -> None:
        """Stop the MCP server process."""
        await self._client.stop()
        self._connected = False

    @property
    def tools(self) -> list[MCPToolProxy]:
        """Return the list of available tool proxies (empty if not connected)."""
        return self._tools

    @property
    def connected(self) -> bool:
        return self._connected

    async def __aenter__(self) -> "MCPToolAdapter":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.disconnect()
