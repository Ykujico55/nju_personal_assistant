"""A deliberately small, dependency-free JSON Schema validator.

It supports the subset used by core and extension contracts.  Unsupported schema
keywords fail closed so adding a complex schema never silently weakens validation.
Production may replace this module with a standards-complete adapter behind the
same function contract.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from personal_assistant.domain.errors import ValidationError


class SchemaValidationError(ValidationError):
    code = "schema_validation_error"


_SUPPORTED_KEYWORDS = {
    "$schema",
    "$id",
    "title",
    "description",
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "const",
    "minLength",
    "maxLength",
    "pattern",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minProperties",
    "maxProperties",
    "anyOf",
    "oneOf",
    "allOf",
}


def validate_json_schema(value: Any, schema: Mapping[str, Any], *, path: str = "$") -> None:
    if not isinstance(schema, Mapping):
        raise SchemaValidationError(f"{path}: schema must be an object")
    unsupported = set(schema) - _SUPPORTED_KEYWORDS
    if unsupported:
        names = ", ".join(sorted(unsupported))
        raise SchemaValidationError(f"{path}: unsupported schema keyword(s): {names}")

    if "allOf" in schema:
        for sub_schema in _schema_list(schema["allOf"], path, "allOf"):
            validate_json_schema(value, sub_schema, path=path)
    if "anyOf" in schema:
        matches = _matching_count(value, schema["anyOf"], path)
        if matches < 1:
            raise SchemaValidationError(f"{path}: value does not match anyOf")
    if "oneOf" in schema:
        matches = _matching_count(value, schema["oneOf"], path)
        if matches != 1:
            raise SchemaValidationError(f"{path}: value must match exactly one oneOf branch")

    if "const" in schema and value != schema["const"]:
        raise SchemaValidationError(f"{path}: value does not match const")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaValidationError(f"{path}: value is not in enum")

    expected = schema.get("type")
    if expected is not None:
        expected_types = [expected] if isinstance(expected, str) else list(expected)
        if not any(_is_type(value, candidate) for candidate in expected_types):
            raise SchemaValidationError(
                f"{path}: expected {' or '.join(expected_types)}, got {type(value).__name__}"
            )

    if isinstance(value, Mapping):
        _validate_object(value, schema, path)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        _validate_array(value, schema, path)
    elif isinstance(value, str):
        _validate_string(value, schema, path)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        _validate_number(value, schema, path)


def _validate_object(value: Mapping[str, Any], schema: Mapping[str, Any], path: str) -> None:
    if not any(
        keyword in schema
        for keyword in (
            "properties",
            "required",
            "additionalProperties",
            "minProperties",
            "maxProperties",
        )
    ):
        # The empty schema is intentionally unconstrained.
        return
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise SchemaValidationError(f"{path}: properties must be an object")
    required = schema.get("required", [])
    missing = [name for name in required if name not in value]
    if missing:
        raise SchemaValidationError(f"{path}: missing required field(s): {', '.join(missing)}")
    if "minProperties" in schema and len(value) < schema["minProperties"]:
        raise SchemaValidationError(f"{path}: too few properties")
    if "maxProperties" in schema and len(value) > schema["maxProperties"]:
        raise SchemaValidationError(f"{path}: too many properties")

    additional = schema.get("additionalProperties", False)
    for key, item in value.items():
        if not isinstance(key, str):
            raise SchemaValidationError(f"{path}: object keys must be strings")
        child_path = f"{path}.{key}"
        if key in properties:
            validate_json_schema(item, properties[key], path=child_path)
        elif additional is True:
            continue
        elif isinstance(additional, Mapping):
            validate_json_schema(item, additional, path=child_path)
        else:
            raise SchemaValidationError(f"{child_path}: unknown field")


def _validate_array(value: Sequence[Any], schema: Mapping[str, Any], path: str) -> None:
    if "minItems" in schema and len(value) < schema["minItems"]:
        raise SchemaValidationError(f"{path}: too few items")
    if "maxItems" in schema and len(value) > schema["maxItems"]:
        raise SchemaValidationError(f"{path}: too many items")
    if schema.get("uniqueItems"):
        try:
            if len({repr(item) for item in value}) != len(value):
                raise SchemaValidationError(f"{path}: items must be unique")
        except TypeError as exc:
            raise SchemaValidationError(f"{path}: cannot evaluate uniqueItems") from exc
    item_schema = schema.get("items")
    if item_schema is not None:
        for index, item in enumerate(value):
            validate_json_schema(item, item_schema, path=f"{path}[{index}]")


def _validate_string(value: str, schema: Mapping[str, Any], path: str) -> None:
    if "minLength" in schema and len(value) < schema["minLength"]:
        raise SchemaValidationError(f"{path}: string is too short")
    if "maxLength" in schema and len(value) > schema["maxLength"]:
        raise SchemaValidationError(f"{path}: string is too long")
    if "pattern" in schema and re.search(schema["pattern"], value) is None:
        raise SchemaValidationError(f"{path}: string does not match pattern")


def _validate_number(value: int | float, schema: Mapping[str, Any], path: str) -> None:
    if "minimum" in schema and value < schema["minimum"]:
        raise SchemaValidationError(f"{path}: number is below minimum")
    if "maximum" in schema and value > schema["maximum"]:
        raise SchemaValidationError(f"{path}: number exceeds maximum")
    if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
        raise SchemaValidationError(f"{path}: number is below exclusiveMinimum")
    if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
        raise SchemaValidationError(f"{path}: number exceeds exclusiveMaximum")


def _is_type(value: Any, expected: str) -> bool:
    checks = {
        "null": lambda: value is None,
        "boolean": lambda: isinstance(value, bool),
        "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda: isinstance(value, (int, float)) and not isinstance(value, bool),
        "string": lambda: isinstance(value, str),
        "array": lambda: isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray)),
        "object": lambda: isinstance(value, Mapping),
    }
    try:
        return checks[expected]()
    except KeyError as exc:
        raise SchemaValidationError(f"unsupported JSON Schema type: {expected}") from exc


def _schema_list(value: Any, path: str, keyword: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or not value or not all(
        isinstance(item, Mapping) for item in value
    ):
        raise SchemaValidationError(f"{path}: {keyword} must be a non-empty schema list")
    return value


def _matching_count(value: Any, schemas: Any, path: str) -> int:
    count = 0
    for sub_schema in _schema_list(schemas, path, "branch"):
        try:
            validate_json_schema(value, sub_schema, path=path)
        except SchemaValidationError:
            continue
        count += 1
    return count
