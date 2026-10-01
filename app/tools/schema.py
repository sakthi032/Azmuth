"""Minimal JSON Schema validator.

A deliberately small subset — enough for tool argument validation, no more.
Deliberately **not** a full implementation of JSON Schema; a real one would
require ``jsonschema`` and the spec says don't add dependencies that aren't
needed. The supported keywords are listed below. Anything else is ignored
(never silently *validated incorrectly* — just not checked).

Supported keywords:

* ``type``            — ``"object"``, ``"array"``, ``"string"``, ``"integer"``,
                        ``"number"``, ``"boolean"``, ``"null"``, or a list of
                        those.
* ``required``        — array of property names that must be present.
* ``properties``      — per-property subschemas.
* ``additionalProperties`` — ``False`` rejects unknown object keys.
* ``enum``            — allowed values.
* ``minimum`` / ``maximum`` — numeric bounds.
* ``minLength`` / ``maxLength`` — string length bounds.
* ``pattern``         — ``re.search`` pattern for strings.
* ``items``           — subschema applied to every array element.
* ``minItems`` / ``maxItems`` — array length bounds.

Not supported (silently ignored): ``$ref``, ``allOf``, ``anyOf``,
``oneOf``, ``not``, ``format``, ``patternProperties``, ``dependencies``.

The validator never raises; it returns a list of human-readable error
strings, which the caller turns into :class:`ToolValidationError`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


def validate(instance: object, schema: Mapping[str, Any]) -> list[str]:
    """Return a list of validation errors. Empty list means valid."""
    errors: list[str] = []
    _validate(instance, schema, path="$", errors=errors)
    return errors


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------
_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
    # ``integer`` and ``number`` are handled specially — see _check_type.
}


def _check_type(value: object, expected: str) -> bool:
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    python_type = _TYPE_MAP.get(expected)
    if python_type is None:
        # Unknown type name — treat as "no constraint" so a typo in a schema
        # never causes valid input to be rejected. This is defensive, not
        # permissive: the schema is code, and code errors should not become
        # silent runtime failures for the user.
        return True
    if python_type is bool:
        return isinstance(value, bool)
    return isinstance(value, python_type)


def _type_name(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _validate(
    value: object,
    schema: object,
    *,
    path: str,
    errors: list[str],
) -> None:
    if not isinstance(schema, Mapping):
        return

    if not _check_declared_type(value, schema, path=path, errors=errors):
        # On a type mismatch, further keyword checks would be nonsense.
        return

    _check_enum(value, schema, path=path, errors=errors)

    if isinstance(value, str):
        _check_string(value, schema, path=path, errors=errors)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        _check_number(value, schema, path=path, errors=errors)
    if isinstance(value, list):
        _check_array(value, schema, path=path, errors=errors)
    if isinstance(value, dict):
        _check_object(value, schema, path=path, errors=errors)


def _check_declared_type(
    value: object,
    schema: Mapping[str, Any],
    *,
    path: str,
    errors: list[str],
) -> bool:
    expected = schema.get("type")
    if expected is None:
        return True
    if isinstance(expected, list):
        if any(_check_type(value, str(t)) for t in expected):
            return True
        errors.append(
            f"{path}: expected one of {list(expected)!r}, "
            f"got {_type_name(value)}"
        )
        return False
    if isinstance(expected, str):
        if _check_type(value, expected):
            return True
        errors.append(f"{path}: expected {expected!r}, got {_type_name(value)}")
        return False
    return True


def _check_enum(
    value: object,
    schema: Mapping[str, Any],
    *,
    path: str,
    errors: list[str],
) -> None:
    allowed = schema.get("enum")
    if allowed is None:
        return
    if not isinstance(allowed, (list, tuple)):
        return
    if value not in allowed:
        errors.append(f"{path}: must be one of {list(allowed)!r}, got {value!r}")


def _check_string(
    value: str,
    schema: Mapping[str, Any],
    *,
    path: str,
    errors: list[str],
) -> None:
    min_length = schema.get("minLength")
    if isinstance(min_length, int) and len(value) < min_length:
        errors.append(f"{path}: string is shorter than {min_length}")
    max_length = schema.get("maxLength")
    if isinstance(max_length, int) and len(value) > max_length:
        errors.append(f"{path}: string is longer than {max_length}")
    pattern = schema.get("pattern")
    if isinstance(pattern, str):
        try:
            matched = re.search(pattern, value) is not None
        except re.error:
            # A malformed pattern is a schema bug, not a value bug — treat
            # as "no constraint" rather than rejecting valid input.
            return
        if not matched:
            errors.append(f"{path}: string does not match pattern {pattern!r}")


def _check_number(
    value: float,
    schema: Mapping[str, Any],
    *,
    path: str,
    errors: list[str],
) -> None:
    minimum = schema.get("minimum")
    if isinstance(minimum, (int, float)) and value < minimum:
        errors.append(f"{path}: value {value} is less than minimum {minimum}")
    maximum = schema.get("maximum")
    if isinstance(maximum, (int, float)) and value > maximum:
        errors.append(f"{path}: value {value} is greater than maximum {maximum}")


def _check_array(
    value: list,
    schema: Mapping[str, Any],
    *,
    path: str,
    errors: list[str],
) -> None:
    min_items = schema.get("minItems")
    if isinstance(min_items, int) and len(value) < min_items:
        errors.append(f"{path}: array has fewer than {min_items} items")
    max_items = schema.get("maxItems")
    if isinstance(max_items, int) and len(value) > max_items:
        errors.append(f"{path}: array has more than {max_items} items")
    items = schema.get("items")
    if isinstance(items, Mapping):
        for index, element in enumerate(value):
            _validate(element, items, path=f"{path}[{index}]", errors=errors)


def _check_object(
    value: dict,
    schema: Mapping[str, Any],
    *,
    path: str,
    errors: list[str],
) -> None:
    required = schema.get("required")
    if isinstance(required, (list, tuple)):
        for key in required:
            if isinstance(key, str) and key not in value:
                errors.append(f"{path}: missing required property {key!r}")

    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        for key, subschema in properties.items():
            if key in value:
                _validate(value[key], subschema, path=f"{path}.{key}", errors=errors)

    if schema.get("additionalProperties") is False:
        allowed = set(properties) if isinstance(properties, Mapping) else set()
        for key in value:
            if key not in allowed:
                errors.append(f"{path}: unexpected property {key!r}")