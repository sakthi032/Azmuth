
"""One-shot fixer for app/brain/tool_calls.py.

Run:  py -m scripts.fix_tool_calls
"""

from pathlib import Path

NEW_CONTENT = r'''"""Parser for the tool-call format taught in the system prompt.

Primary format: a fenced ```tool``` code block containing a JSON object
or an array of them.
Fallback: any other fenced block whose body parses as a tool call.
Last resort: the entire response is a bare tool-call JSON object.

Every `tool` fenced block is always removed from the cleaned text,
whether or not its body parsed successfully - the model intended it as a
tool call, and leaving raw JSON in the reply confuses the user.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

_TOOL_BLOCK = re.compile(r"```tool\s*\n(.*?)\n\s*```", re.DOTALL)
_ANY_FENCE = re.compile(r"```[a-zA-Z]*\s*\n(.*?)\n\s*```", re.DOTALL)


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "arguments": dict(self.arguments)}


def parse_tool_calls(text: str) -> tuple[tuple[ToolCall, ...], str]:
    if not text:
        return (), ""

    calls: list[ToolCall] = []
    consumed_spans: list[tuple[int, int]] = []

    # Pass 1: every explicit tool block is consumed - valid or not.
    for match in _TOOL_BLOCK.finditer(text):
        calls.extend(_parse_body(match.group(1).strip()))
        consumed_spans.append(match.span())

    # Pass 2: any other fenced block whose body parses as a tool call.
    if not consumed_spans:
        for match in _ANY_FENCE.finditer(text):
            parsed = _parse_body(match.group(1).strip())
            if parsed:
                calls.extend(parsed)
                consumed_spans.append(match.span())

    # Pass 3: whole response is one bare JSON tool call.
    if not consumed_spans:
        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            parsed = _parse_body(stripped)
            if parsed:
                calls.extend(parsed)
                consumed_spans.append((0, len(text)))

    if consumed_spans:
        consumed_spans.sort()
        pieces: list[str] = []
        cursor = 0
        for start, end in consumed_spans:
            pieces.append(text[cursor:start])
            cursor = end
        pieces.append(text[cursor:])
        cleaned = "".join(pieces).strip()
    else:
        cleaned = text.strip()

    return tuple(calls), cleaned


def _parse_body(body: str) -> list[ToolCall]:
    if not body:
        return []
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return []

    if isinstance(payload, list):
        result: list[ToolCall] = []
        for item in payload:
            call = _coerce(item)
            if call is not None:
                result.append(call)
        return result

    call = _coerce(payload)
    return [call] if call is not None else []


def _coerce(payload: Any) -> ToolCall | None:
    if not isinstance(payload, dict):
        return None
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    arguments = payload.get("arguments")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return None
    return ToolCall(name=name.strip(), arguments=dict(arguments))


__all__ = ["ToolCall", "parse_tool_calls"]
'''

path = Path("app/brain/tool_calls.py")
path.write_text(NEW_CONTENT, encoding="utf-8")
print(f"Wrote {path} ({path.stat().st_size} bytes)")

for cache in Path("app/brain").rglob("__pycache__"):
    if cache.is_dir():
        for f in cache.iterdir():
            f.unlink()
        cache.rmdir()
        print(f"Cleared {cache}")
