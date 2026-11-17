"""MCP (Model Context Protocol) stdio client.

Launches an MCP server as a subprocess and communicates over stdio
using JSON-RPC 2.0.  Supports the ``initialize``, ``tools/list``,
and ``tools/call`` methods.

Usage::

    client = MCPClient(command=["node", "my-mcp-server.js"])
    await client.start()
    tools = await client.list_tools()
    result = await client.call_tool("search", {"query": "foo"})
    await client.stop()

Or as an async context manager::

    async with MCPClient(["node", "server.js"]) as client:
        tools = await client.list_tools()
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.mcp.protocol import (
    JsonRpcRequest,
    JsonRpcResponse,
    MCPToolDefinition,
    MCPToolResult,
)

logger = logging.getLogger(__name__)


class MCPError(RuntimeError):
    """Raised when the MCP server returns a JSON-RPC error."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"[MCP error {code}] {message}")
        self.code = code
        self.message = message
        self.data = data


class MCPClient:
    """Async MCP client over stdio transport.

    Parameters
    ----------
    command:
        Command line to launch the MCP server process.
    startup_timeout:
        Seconds to wait for the server to become ready.
    request_timeout:
        Seconds to wait for each individual request.
    client_name:
        Name sent in the MCP ``initialize`` handshake.
    """

    def __init__(
        self,
        command: list[str],
        startup_timeout: float = 10.0,
        request_timeout: float = 30.0,
        client_name: str = "Azmuth",
    ) -> None:
        self._command = command
        self._startup_timeout = startup_timeout
        self._request_timeout = request_timeout
        self._client_name = client_name
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._counter: int = 0
        self._pending: dict[int, asyncio.Future[JsonRpcResponse]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._initialized: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Launch the server process and perform the MCP handshake."""
        self._proc = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert self._proc.stdin and self._proc.stdout
        self._writer = self._proc.stdin  # type: ignore[assignment]
        self._reader = self._proc.stdout

        # Start background reader
        self._reader_task = asyncio.create_task(self._read_loop())

        # MCP initialize handshake
        try:
            async with asyncio.timeout(self._startup_timeout):
                resp = await self._request(
                    "initialize",
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "clientInfo": {"name": self._client_name, "version": "4.0"},
                    },
                )
        except TimeoutError as exc:
            raise MCPError(-1, "MCP server did not respond to initialize") from exc

        if resp.is_error:
            err = resp.error or {}
            raise MCPError(err.get("code", -1), err.get("message", "unknown"))

        # Send initialized notification (no id = notification)
        await self._notify("notifications/initialized", {})
        self._initialized = True
        logger.info("MCPClient connected: %s %s", self._command[0], self._command[1:])

    async def stop(self) -> None:
        """Terminate the server process cleanly."""
        self._initialized = False
        if self._reader_task:
            self._reader_task.cancel()
        if self._proc:
            with _suppress():
                self._proc.terminate()
            with _suppress():
                await asyncio.wait_for(self._proc.wait(), timeout=3)
        logger.info("MCPClient stopped")

    async def __aenter__(self) -> "MCPClient":
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # MCP API
    # ------------------------------------------------------------------

    async def list_tools(self) -> list[MCPToolDefinition]:
        """Return tools advertised by the connected MCP server."""
        resp = await self._request("tools/list", {})
        if resp.is_error:
            err = resp.error or {}
            raise MCPError(err.get("code", -1), err.get("message", "tools/list failed"))
        tools_raw = resp.result.get("tools", []) if resp.result else []
        return [MCPToolDefinition.from_dict(t) for t in tools_raw]

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> MCPToolResult:
        """Invoke a tool on the MCP server and return the result.

        Raises
        ------
        MCPError
            If the server returns a JSON-RPC error.
        """
        resp = await self._request(
            "tools/call",
            {"name": tool_name, "arguments": arguments or {}},
        )
        if resp.is_error:
            err = resp.error or {}
            raise MCPError(err.get("code", -1), err.get("message", "tools/call failed"))
        return MCPToolResult.from_dict(resp.result or {})

    # ------------------------------------------------------------------
    # Private — transport
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        params: dict[str, Any],
    ) -> JsonRpcResponse:
        """Send a request and await the matching response."""
        self._counter += 1
        req_id = self._counter
        req = JsonRpcRequest(method=method, params=params, id=req_id)

        fut: asyncio.Future[JsonRpcResponse] = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut

        try:
            await self._send(req.to_json())
            async with asyncio.timeout(self._request_timeout):
                return await fut
        except TimeoutError:
            self._pending.pop(req_id, None)
            raise

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        req = JsonRpcRequest(method=method, params=params)
        await self._send(req.to_json())

    async def _send(self, payload: str) -> None:
        """Write a newline-delimited JSON message to the server stdin."""
        assert self._proc and self._proc.stdin
        line = (payload + "\n").encode()
        self._proc.stdin.write(line)
        await self._proc.stdin.drain()

    async def _read_loop(self) -> None:
        """Read JSON-RPC responses from server stdout and resolve futures."""
        assert self._reader
        while True:
            try:
                line = await self._reader.readline()
                if not line:
                    break
                data = json.loads(line.decode().strip())
                resp = JsonRpcResponse.from_dict(data)
                req_id = resp.id
                if req_id is not None and req_id in self._pending:
                    self._pending.pop(req_id).set_result(resp)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("MCPClient read_loop error: %s", exc)


import contextlib as _contextlib


@_contextlib.contextmanager
def _suppress():  # type: ignore[return]
    with _contextlib.suppress(Exception):
        yield
