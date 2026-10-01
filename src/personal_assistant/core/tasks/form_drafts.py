"""Generic, declarative task forms and versioned draft commands (F08.3)."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from personal_assistant.core.approvals.canonicalize import canonical_sha256
from personal_assistant.domain import ConcurrentModificationError, ValidationError

from .service import TaskService

_ROOT_KEYS = frozenset(
    {"type", "title", "description", "properties", "required", "additionalProperties"}
)
_FIELD_KEYS = frozenset(
    {"type", "title", "description", "enum", "minLength", "maxLength", "minimum", "maximum"}
)
_WIDGETS = frozenset({"text", "textarea", "select", "number"})
_RESERVED_NAMES = frozenset({"__proto__", "constructor", "prototype"})


def validate_form_schema(json_schema: object, ui_schema: object) -> None:
    """Accept only the flat form subset that both host and PWA implement."""

    if not isinstance(json_schema, dict) or not isinstance(ui_schema, dict):
        raise ValidationError("form schemas must be JSON objects")
    if len(json.dumps([json_schema, ui_schema], ensure_ascii=False)) > 65536:
        raise ValidationError("form schema exceeds 64 KiB")
    if set(json_schema) - _ROOT_KEYS or json_schema.get("type") != "object":
        raise ValidationError("unsupported form JSON Schema root keyword or type")
    if json_schema.get("additionalProperties") is not False:
        raise ValidationError("form must set additionalProperties to false")
    properties = json_schema.get("properties")
    if not isinstance(properties, dict) or not 1 <= len(properties) <= 100:
        raise ValidationError("form needs 1 to 100 declared fields")
    for name in ("title", "description"):
        if name in json_schema and not isinstance(json_schema[name], str):
            raise ValidationError("form labels must be strings")
    required = json_schema.get("required", [])
    if not isinstance(required, list) or len(set(map(str, required))) != len(required) or not all(
        isinstance(name, str) and name in properties for name in required
    ):
        raise ValidationError("form required fields are invalid")
    for name, field in properties.items():
        if not isinstance(name, str) or not name or len(name) > 128 or name in _RESERVED_NAMES:
            raise ValidationError("form field name is unsupported")
        if not isinstance(field, dict) or set(field) - _FIELD_KEYS:
            raise ValidationError(f"unsupported schema keyword for field {name}")
        kind = field.get("type")
        if kind not in {"string", "boolean", "integer", "number"}:
            raise ValidationError(f"unsupported schema type for field {name}")
        if any(
            key in field and not isinstance(field[key], str)
            for key in ("title", "description")
        ):
            raise ValidationError(f"invalid label for field {name}")
        for bound in ("minLength", "maxLength"):
            if bound in field and (
                kind != "string" or type(field[bound]) is not int or field[bound] < 0
            ):
                raise ValidationError(f"invalid {bound} for field {name}")
        for bound in ("minimum", "maximum"):
            if bound in field and (
                kind not in {"integer", "number"}
                or type(field[bound]) not in {int, float}
                or not math.isfinite(field[bound])
            ):
                raise ValidationError(f"invalid {bound} for field {name}")
        if "enum" in field:
            values = field["enum"]
            if not isinstance(values, list) or not 1 <= len(values) <= 100:
                raise ValidationError(f"invalid enum for field {name}")
            for value in values:
                _validate_value(name, {"type": kind}, value)
    if set(ui_schema) - set(properties) - {"sensitivity", "ui:order"}:
        raise ValidationError("unsupported form UI Schema field")
    if "sensitivity" in ui_schema and ui_schema["sensitivity"] not in {
        "PUBLIC", "PERSONAL", "SENSITIVE"
    }:
        raise ValidationError("unsupported form sensitivity")
    if "ui:order" in ui_schema and (
        not isinstance(ui_schema["ui:order"], list)
        or sorted(ui_schema["ui:order"]) != sorted(properties)
    ):
        raise ValidationError("unsupported form field order")
    for name in properties:
        ui = ui_schema.get(name, {})
        if not isinstance(ui, dict) or set(ui) - {"ui:placeholder", "ui:widget"}:
            raise ValidationError(f"unsupported UI Schema for field {name}")
        if "ui:placeholder" in ui and not isinstance(ui["ui:placeholder"], str):
            raise ValidationError(f"invalid placeholder for field {name}")
        if "ui:widget" in ui and ui["ui:widget"] not in _WIDGETS:
            raise ValidationError(f"unsupported widget for field {name}")
        widget = ui.get("ui:widget")
        allowed_widgets = (
            {"select"} if "enum" in properties[name]
            else {"text", "textarea"} if properties[name]["type"] == "string"
            else {"select"} if properties[name]["type"] == "boolean"
            else {"number"}
        )
        if widget is not None and widget not in allowed_widgets:
            raise ValidationError(f"widget does not match field {name} type")


def _validate_value(name: str, field: Mapping[str, Any], value: Any) -> None:
    kind = field["type"]
    if kind == "string" and not isinstance(value, str):
        raise ValidationError(f"field {name} requires a string")
    if kind == "boolean" and not isinstance(value, bool):
        raise ValidationError(f"field {name} requires a boolean")
    if kind == "integer" and type(value) is not int:
        raise ValidationError(f"field {name} requires an integer")
    if kind == "number" and (type(value) not in {int, float} or not math.isfinite(value)):
        raise ValidationError(f"field {name} requires a finite number")
    if kind == "string" and (
        len(value) < field.get("minLength", 0)
        or len(value) > field.get("maxLength", 100000)
    ):
        raise ValidationError(f"field {name} has invalid length")
    if kind in {"integer", "number"} and (
        value < field.get("minimum", -math.inf)
        or value > field.get("maximum", math.inf)
    ):
        raise ValidationError(f"field {name} is outside its bounds")
    if "enum" in field and value not in field["enum"]:
        raise ValidationError(f"field {name} is not an allowed choice")


def validate_form_values(schema: Mapping[str, Any], values: object) -> dict[str, Any]:
    if not isinstance(values, dict) or len(json.dumps(values, ensure_ascii=False)) > 200000:
        raise ValidationError("draft values must be a JSON object of at most 200 KiB")
    properties = schema["properties"]
    for name, value in values.items():
        if name not in properties:
            raise ValidationError(f"unknown form field {name}")
        _validate_value(name, properties[name], value)
    return dict(values)


@dataclass(frozen=True, slots=True)
class FormDraft:
    task_id: str
    extension_id: str
    extension_version: str
    form_id: str
    json_schema: dict[str, Any]
    ui_schema: dict[str, Any]
    values: dict[str, Any]
    sources: dict[str, str]
    version: int
    updated_at: datetime


class FormDraftStorePort(Protocol):
    async def get(self, task_id: str) -> FormDraft: ...

    async def command_receipt(self, key: str) -> tuple[str, FormDraft] | None: ...

    async def create(self, draft: FormDraft, *, key: str, fingerprint: str) -> FormDraft: ...

    async def replace(
        self, task_id: str, values: dict[str, Any], *, version: int, key: str, fingerprint: str
    ) -> FormDraft: ...


class FormDraftService:
    def __init__(self, tasks: TaskService, store: FormDraftStorePort) -> None:
        self._tasks = tasks
        self._store = store

    async def get(self, task_id: str) -> FormDraft:
        await self._tasks.get(task_id)
        return await self._store.get(task_id)

    @staticmethod
    def _create_fingerprint(
        task_id: str,
        extension_id: str,
        form_id: str,
        initial_values: dict[str, Any] | None,
        initial_sources: dict[str, str] | None,
    ) -> str:
        return canonical_sha256(
            {"command": "form-draft.create", "task_id": task_id,
             "extension_id": extension_id, "form_id": form_id,
             "initial_values": initial_values or {}, "initial_sources": initial_sources or {}}
        )

    async def replay_create(
        self,
        task_id: str,
        extension_id: str,
        form_id: str,
        *,
        key: str,
        initial_values: dict[str, Any] | None = None,
        initial_sources: dict[str, str] | None = None,
    ) -> FormDraft | None:
        """Read the original command receipt without consulting the live form catalog."""

        await self._tasks.get(task_id)
        prior = await self._store.command_receipt(key)
        if prior is None:
            return None
        original_fingerprint, receipt = prior
        if (
            original_fingerprint != self._create_fingerprint(
                task_id, extension_id, form_id, initial_values, initial_sources
            )
            or receipt.version != 1
            or receipt.task_id != task_id
            or receipt.extension_id != extension_id
            or receipt.form_id != form_id
        ):
            raise ConcurrentModificationError("draft idempotency key was reused with new content")
        return receipt

    async def create(
        self,
        task_id: str,
        form: Mapping[str, Any],
        *,
        key: str,
        initial_values: dict[str, Any] | None = None,
        initial_sources: dict[str, str] | None = None,
    ) -> FormDraft:
        extension_id = str(form["extension_id"])
        form_id = str(form["id"])
        replay = await self.replay_create(
            task_id, extension_id, form_id, key=key,
            initial_values=initial_values, initial_sources=initial_sources,
        )
        if replay is not None:
            return replay
        schema = form["json_schema"]
        ui = form["ui_schema"]
        validate_form_schema(schema, ui)
        values = validate_form_values(schema, initial_values or {})
        sources = dict.fromkeys(schema["properties"], "UNKNOWN")
        for name, source in (initial_sources or {}).items():
            if name not in values or source not in {"EVIDENCE", "USER_INPUT"}:
                raise ValidationError("initial form source must match a supplied value")
            sources[name] = source
        draft = FormDraft(
            task_id=task_id,
            extension_id=extension_id,
            extension_version=str(form["extension_version"]),
            form_id=form_id,
            json_schema=dict(schema),
            ui_schema=dict(ui),
            values=values,
            sources=sources,
            version=1,
            updated_at=datetime.now(UTC),
        )
        fingerprint = self._create_fingerprint(
            task_id, extension_id, form_id, initial_values, initial_sources
        )
        try:
            return await self._store.create(draft, key=key, fingerprint=fingerprint)
        except ConcurrentModificationError:
            replay = await self.replay_create(
                task_id, extension_id, form_id, key=key,
                initial_values=initial_values, initial_sources=initial_sources,
            )
            if replay is not None:
                return replay
            raise

    async def replace(
        self, task_id: str, values: object, *, version: int, key: str
    ) -> FormDraft:
        await self._tasks.get(task_id)
        current = await self._store.get(task_id)
        checked = validate_form_values(current.json_schema, values)
        fingerprint = canonical_sha256({"task_id": task_id, "version": version, "values": checked})
        return await self._store.replace(
            task_id, checked, version=version, key=key, fingerprint=fingerprint
        )
