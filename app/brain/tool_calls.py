"""Parser for the tool-call format taught in the system prompt.

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

    # Pass 3: the response begins with one or more bare JSON tool-call
    # objects. This happens when a small model ignores the fenced-block
    # instruction and emits JSON directly. We consume as many leading
    # JSON objects as parse cleanly, then leave the rest as text.
    if not consumed_spans:
        leading_calls, consumed_up_to = _consume_leading_json_objects(text)
        if leading_calls:
            calls.extend(leading_calls)
            consumed_spans.append((0, consumed_up_to))

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

def _consume_leading_json_objects(
    text: str,
) -> tuple[list[ToolCall], int]:
    """Parse as many leading JSON objects as possible.

    Returns ``(calls, chars_consumed)``. Stops at the first byte that
    does not continue a JSON object. Whitespace and newlines between
    objects are tolerated.
    """
    calls: list[ToolCall] = []
    cursor = 0
    length = len(text)

    while cursor < length:
        # Skip whitespace.
        while cursor < length and text[cursor] in " \t\r\n":
            cursor += 1
        if cursor >= length or text[cursor] != "{":
            break

        # Try to find the matching closing brace, respecting nesting and
        # strings (a brace inside a string must not count).
        depth = 0
        in_string = False
        escape = False
        start = cursor
        while cursor < length:
            ch = text[cursor]
            if escape:
                escape = False
            elif in_string:
                if ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        cursor += 1
                        break
            cursor += 1
        else:
            # Unterminated object; stop.
            break

        candidate = text[start:cursor].strip()
        parsed = _parse_body(candidate)
        if not parsed:
            # Leading object is not a valid tool call. Stop; do not
            # consume it — the response may just be JSON-shaped text.
            return [], 0
        calls.extend(parsed)

    if not calls:
        return [], 0
    return calls, cursor


__all__ = ["ToolCall", "parse_tool_calls"]
