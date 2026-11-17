"""MCP (Model Context Protocol) JSON-RPC 2.0 protocol types.

Minimal, dependency-free implementation of the MCP wire protocol
using only stdlib ``dataclasses`` and ``typing``.

Reference: https://spec.modelcontextprotocol.io/specification/
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 primitives
# ---------------------------------------------------------------------------

@dataclass
class JsonRpcRequest:
    method: str
    params: dict[str, Any] | None = None
    id: int | str | None = None
    jsonrpc: str = "2.0"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"jsonrpc": self.jsonrpc, "method": self.method}
        if self.params is not None:
            d["params"] = self.params
        if self.id is not None:
            d["id"] = self.id
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclass
class JsonRpcResponse:
    id: int | str | None
    result: Any = None
    error: dict[str, Any] | None = None
    jsonrpc: str = "2.0"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "JsonRpcResponse":
        return cls(
            id=d.get("id"),
            result=d.get("result"),
            error=d.get("error"),
        )

    @property
    def is_error(self) -> bool:
        return self.error is not None


# ---------------------------------------------------------------------------
# MCP-specific types
# ---------------------------------------------------------------------------

@dataclass
class MCPToolDefinition:
    """A tool advertised by an MCP server."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MCPToolDefinition":
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            input_schema=d.get("inputSchema", {}),
        )


@dataclass
class MCPToolResult:
    """Result of a tool call via MCP."""

    content: list[dict[str, Any]]
    is_error: bool = False

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MCPToolResult":
        return cls(
            content=d.get("content", []),
            is_error=d.get("isError", False),
        )

    def text(self) -> str:
        """Concatenate all text content blocks."""
        parts = []
        for block in self.content:
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
