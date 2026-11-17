"""Tests for Model Context Protocol (MCP) JSON-RPC 2.0 protocol and client adapters."""

import pytest
from app.mcp.protocol import (
    JsonRpcRequest,
    JsonRpcResponse,
    MCPToolDefinition,
    MCPToolResult,
)
from app.mcp.adapter import MCPToolProxy
from app.mcp.client import MCPError


def test_mcp_json_rpc_request_formatting():
    """Verify JSON-RPC 2.0 request formatting."""
    req = JsonRpcRequest(method="tools/list", params={"cursor": "abc"}, id=1)
    d = req.to_dict()
    assert d["jsonrpc"] == "2.0"
    assert d["method"] == "tools/list"
    assert d["params"] == {"cursor": "abc"}
    assert d["id"] == 1


def test_mcp_json_rpc_response_parsing():
    """Verify JSON-RPC 2.0 response parsing."""
    raw = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"tools": [{"name": "calculator", "description": "Calc tool"}]},
    }
    resp = JsonRpcResponse.from_dict(raw)
    assert resp.is_error is False
    assert resp.result["tools"][0]["name"] == "calculator"


def test_mcp_json_rpc_error_detection():
    """Detect error responses from JSON-RPC."""
    raw = {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32601, "message": "Method not found"},
    }
    resp = JsonRpcResponse.from_dict(raw)
    assert resp.is_error is True
    assert resp.error["code"] == -32601


def test_mcp_tool_result_text_extraction():
    """MCPToolResult correctly concatenates multiple text blocks."""
    raw = {
        "content": [
            {"type": "text", "text": "First line."},
            {"type": "image", "data": "base64..."},
            {"type": "text", "text": "Second line."},
        ],
        "isError": False,
    }
    result = MCPToolResult.from_dict(raw)
    assert result.is_error is False
    text = result.text()
    assert "First line." in text
    assert "Second line." in text


class MockMCPClient:
    async def call_tool(self, tool_name: str, arguments: dict):
        if tool_name == "echo":
            return MCPToolResult(content=[{"type": "text", "text": f"Echo: {arguments.get('msg')}"}])
        elif tool_name == "fail":
            raise MCPError(-32000, "Internal tool failure")
        return MCPToolResult(content=[])


@pytest.mark.asyncio
async def test_mcp_tool_proxy_invocation():
    """MCPToolProxy exposes standard Azmuth callable interface."""
    defn = MCPToolDefinition(
        name="echo",
        description="Echo message",
        input_schema={"type": "object", "properties": {"msg": {"type": "string"}}},
    )
    client = MockMCPClient()
    proxy = MCPToolProxy(client=client, definition=defn)  # type: ignore

    output = await proxy({"msg": "hello MCP"})
    assert output == "Echo: hello MCP"
    
    schema = proxy.to_tool_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "echo"


@pytest.mark.asyncio
async def test_mcp_tool_proxy_error_handling():
    """MCPToolProxy handles MCPError gracefully without crashing."""
    defn = MCPToolDefinition(name="fail", description="Failing tool")
    client = MockMCPClient()
    proxy = MCPToolProxy(client=client, definition=defn)  # type: ignore

    output = await proxy({})
    assert "[MCP error" in output
