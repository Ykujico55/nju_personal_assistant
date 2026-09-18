"""Generic, non-secret extension configuration.

Configuration is declared by the extension's ``config_schema`` and the host
persists only JSON-shaped values.  The host never stores credentials here: the
schema itself comes from the trusted extension bundle, and secrets must travel
through ``SecretHandle`` and host broker capabilities instead.

Only a small JSON Schema subset is implemented so the host stays
dependency-free and the accepted shape is explicit and testable.  Unsupported
keywords fail closed rather than being silently ignored.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .errors import ExtensionError

MAX_CONFIG_BYTES = 65_536
MAX_CONFIG_SCHEMA_BYTES = 262_144
MAX_DEPTH = 16

_SUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "$schema",
        "title",
        "description",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
        "pattern",
        "enum",
        "minimum",
        "maximum",
        "default",
        "examples",
        "x_ui",
        "order",
    }
)

_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})


class ExtensionConfigError(ExtensionError):
    pass


@runtime_checkable
class ExtensionConfigStore(Protocol):
    async def get(self, extension_id: str) -> Mapping[str, Any]: ...

    async def save(self, extension_id: str, config: Mapping[str, Any]) -> None: ...


@dataclass(frozen=True, slots=True)
class ConfigFieldError:
    path: str
    message: str

    def render(self) -> str:
        return f"{self.path or '$'}: {self.message}"


def validate_extension_config(
    schema: Mapping[str, Any] | None,
    config: Any,
    *,
    field_name: str = "config",
) -> dict[str, Any]:
    """Validate and normalize a non-secret extension config document."""

    if not isinstance(config, Mapping):
        raise ExtensionConfigError(f"{field_name} must be a JSON object")
    try:
        encoded = json.dumps(dict(config), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ExtensionConfigError(f"{field_name} must be JSON serializable") from exc
    if len(encoded.encode("utf-8")) > MAX_CONFIG_BYTES:
        raise ExtensionConfigError(f"{field_name} exceeds the size limit")
    normalized: dict[str, Any] = json.loads(encoded)
    if schema is None:
        return normalized
    normalized_schema = validate_extension_config_schema(schema)
    errors: list[ConfigFieldError] = []
    _validate(normalized_schema, normalized, path="", depth=0, errors=errors)
    if errors:
        rendered = "; ".join(error.render() for error in errors[:8])
        raise ExtensionConfigError(f"{field_name} does not match the schema: {rendered}")
    return normalized


def validate_extension_config_schema(schema: Any) -> dict[str, Any]:
    """Validate and normalize the manifest's supported JSON-Schema subset."""

    if not isinstance(schema, Mapping):
        raise ExtensionConfigError("config schema must be an object")
    try:
        encoded = json.dumps(dict(schema), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ExtensionConfigError("config schema must be JSON serializable") from exc
    if len(encoded.encode("utf-8")) > MAX_CONFIG_SCHEMA_BYTES:
        raise ExtensionConfigError("config schema exceeds the size limit")
    normalized: dict[str, Any] = json.loads(encoded)
    schema_errors: list[ConfigFieldError] = []
    _validate_schema(normalized, path="", depth=0, errors=schema_errors)
    if schema_errors:
        rendered = "; ".join(error.render() for error in schema_errors[:8])
        raise ExtensionConfigError(f"config schema is invalid: {rendered}")
    return normalized


def _validate_schema(
    schema: Any,
    *,
    path: str,
    depth: int,
    errors: list[ConfigFieldError],
) -> None:
    """Validate the supported schema subset independently of a config value.

    Optional properties must not hide unsupported keywords until a user happens
    to populate them.  The complete trusted manifest schema is therefore checked
    before it is ever used for validation.
    """

    if depth > MAX_DEPTH:
        errors.append(ConfigFieldError(path, "schema nesting is too deep"))
        return
    if not isinstance(schema, Mapping):
        errors.append(ConfigFieldError(path, "schema must be an object"))
        return
    unknown = set(schema) - _SUPPORTED_SCHEMA_KEYS
    if unknown:
        errors.append(
            ConfigFieldError(path, f"unsupported schema keywords: {sorted(unknown)}")
        )
        return
    expected = schema.get("type")
    if expected is not None and (not isinstance(expected, str) or expected not in _TYPES):
        errors.append(ConfigFieldError(path, f"unsupported schema type: {expected!r}"))
    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, Mapping):
            errors.append(ConfigFieldError(path, "properties must be an object"))
        else:
            for name, child in properties.items():
                if not isinstance(name, str):
                    errors.append(ConfigFieldError(path, "property names must be strings"))
                    continue
                _validate_schema(
                    child,
                    path=_child(path, name),
                    depth=depth + 1,
                    errors=errors,
                )
    items = schema.get("items")
    if items is not None:
        _validate_schema(items, path=f"{path}[]", depth=depth + 1, errors=errors)
    additional = schema.get("additionalProperties")
    if additional is not None and not isinstance(additional, bool):
        if isinstance(additional, Mapping):
            _validate_schema(
                additional,
                path=f"{path}.*" if path else "*",
                depth=depth + 1,
                errors=errors,
            )
        else:
            errors.append(
                ConfigFieldError(path, "additionalProperties must be a boolean or schema")
            )
    required = schema.get("required")
    if required is not None and (
        not isinstance(required, list)
        or any(not isinstance(item, str) for item in required)
        or len(set(required)) != len(required)
    ):
        errors.append(ConfigFieldError(path, "required must contain unique string names"))
    enum = schema.get("enum")
    if enum is not None and (not isinstance(enum, list) or not enum):
        errors.append(ConfigFieldError(path, "enum must be a non-empty array"))
    for keyword in ("minItems", "maxItems", "minLength", "maxLength"):
        value = schema.get(keyword)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            errors.append(ConfigFieldError(path, f"{keyword} must be a non-negative integer"))
    for keyword in ("minimum", "maximum"):
        value = schema.get(keyword)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            errors.append(ConfigFieldError(path, f"{keyword} must be a finite number"))
    pattern = schema.get("pattern")
    if pattern is not None:
        if not isinstance(pattern, str) or len(pattern) > 512:
            errors.append(ConfigFieldError(path, "pattern must be a string of at most 512 chars"))
        else:
            try:
                re.compile(pattern)
            except re.error:
                errors.append(ConfigFieldError(path, "schema pattern is invalid"))


def _validate(
    schema: Any,
    value: Any,
    *,
    path: str,
    depth: int,
    errors: list[ConfigFieldError],
) -> None:
    if depth > MAX_DEPTH:
        errors.append(ConfigFieldError(path, "schema nesting is too deep"))
        return
    if not isinstance(schema, Mapping):
        errors.append(ConfigFieldError(path, "schema must be an object"))
        return
    unknown = set(schema) - _SUPPORTED_SCHEMA_KEYS
    if unknown:
        errors.append(
            ConfigFieldError(path, f"unsupported schema keywords: {sorted(unknown)}")
        )
        return
    expected = schema.get("type")
    if expected is not None:
        if not isinstance(expected, str) or expected not in _TYPES:
            errors.append(ConfigFieldError(path, f"unsupported schema type: {expected!r}"))
            return
        if not _matches_type(expected, value):
            errors.append(ConfigFieldError(path, f"expected {expected}"))
            return
    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list) or value not in enum:
            errors.append(ConfigFieldError(path, "value is not one of the allowed values"))
    if isinstance(value, str):
        _validate_string(schema, value, path=path, errors=errors)
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        _validate_number(schema, value, path=path, errors=errors)
    if isinstance(value, float):
        _validate_number(schema, value, path=path, errors=errors)
    if isinstance(value, Mapping):
        _validate_object(schema, value, path=path, depth=depth, errors=errors)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        _validate_array(schema, value, path=path, depth=depth, errors=errors)


def _validate_string(
    schema: Mapping[str, Any], value: str, *, path: str, errors: list[ConfigFieldError]
) -> None:
    minimum = schema.get("minLength")
    if isinstance(minimum, int) and len(value) < minimum:
        errors.append(ConfigFieldError(path, f"must have at least {minimum} characters"))
    maximum = schema.get("maxLength")
    if isinstance(maximum, int) and len(value) > maximum:
        errors.append(ConfigFieldError(path, f"must have at most {maximum} characters"))
    pattern = schema.get("pattern")
    if isinstance(pattern, str) and len(pattern) <= 512:
        try:
            matched = re.search(pattern, value) is not None
        except re.error:
            errors.append(ConfigFieldError(path, "schema pattern is invalid"))
            return
        if not matched:
            errors.append(ConfigFieldError(path, "does not match the required pattern"))


def _validate_number(
    schema: Mapping[str, Any], value: int | float, *, path: str, errors: list[ConfigFieldError]
) -> None:
    if not math.isfinite(float(value)):
        errors.append(ConfigFieldError(path, "must be a finite number"))
        return
    minimum = schema.get("minimum")
    if isinstance(minimum, (int, float)) and value < minimum:
        errors.append(ConfigFieldError(path, f"must be >= {minimum}"))
    maximum = schema.get("maximum")
    if isinstance(maximum, (int, float)) and value > maximum:
        errors.append(ConfigFieldError(path, f"must be <= {maximum}"))


def _validate_object(
    schema: Mapping[str, Any],
    value: Mapping[str, Any],
    *,
    path: str,
    depth: int,
    errors: list[ConfigFieldError],
) -> None:
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        errors.append(ConfigFieldError(path, "properties must be an object"))
        return
    required = schema.get("required", [])
    if isinstance(required, list):
        for name in required:
            if isinstance(name, str) and name not in value:
                errors.append(ConfigFieldError(_child(path, name), "is required"))
    additional = schema.get("additionalProperties", True)
    for key, item in value.items():
        child_path = _child(path, str(key))
        if key in properties:
            _validate(
                properties[key], item, path=child_path, depth=depth + 1, errors=errors
            )
        elif additional is False:
            errors.append(ConfigFieldError(child_path, "additional properties are not allowed"))
        elif isinstance(additional, Mapping):
            _validate(additional, item, path=child_path, depth=depth + 1, errors=errors)


def _validate_array(
    schema: Mapping[str, Any],
    value: Sequence[Any],
    *,
    path: str,
    depth: int,
    errors: list[ConfigFieldError],
) -> None:
    minimum = schema.get("minItems")
    if isinstance(minimum, int) and len(value) < minimum:
        errors.append(ConfigFieldError(path, f"must contain at least {minimum} items"))
    maximum = schema.get("maxItems")
    if isinstance(maximum, int) and len(value) > maximum:
        errors.append(ConfigFieldError(path, f"must contain at most {maximum} items"))
    items = schema.get("items")
    if isinstance(items, Mapping):
        for index, item in enumerate(value):
            _validate(
                items,
                item,
                path=f"{path}[{index}]",
                depth=depth + 1,
                errors=errors,
            )


def _matches_type(expected: str, value: Any) -> bool:
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, Sequence) and not isinstance(value, (str, bytes))
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _child(path: str, name: str) -> str:
    return f"{path}.{name}" if path else name


__all__ = [
    "MAX_CONFIG_BYTES",
    "MAX_CONFIG_SCHEMA_BYTES",
    "ExtensionConfigError",
    "ExtensionConfigStore",
    "validate_extension_config",
    "validate_extension_config_schema",
]
