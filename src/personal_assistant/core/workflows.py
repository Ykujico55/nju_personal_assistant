"""Strict, data-only workflow interpreter; all tools still pass through the Gateway."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from personal_assistant.core.records import fingerprint
from personal_assistant.core.tools.schemas import validate_json_schema
from personal_assistant.domain import ConcurrentModificationError, ValidationError

_ID = re.compile(r"[A-Za-z0-9_.-]{1,128}")


class WorkflowBindingConflict(ConcurrentModificationError):
    """An incompatible definition is not an optimistic-lock supersession."""


def validate_workflow(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {
        "id",
        "version",
        "steps",
        "extension_id",
        "extension_version",
        "name",
        "description",
        "input_schema",
        "policy_snapshot",
    }:
        raise ValidationError("unsupported workflow fields; executable code is not accepted")
    if not all(
        isinstance(value.get(k), str) and _ID.fullmatch(value[k]) for k in ("id", "version")
    ):
        raise ValidationError("workflow id/version is invalid")
    steps = value.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 128:
        raise ValidationError("workflow requires one to 128 declarative steps")
    names: set[str] = set()
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get("id"), str):
            raise ValidationError("workflow step identity is required")
        if not _ID.fullmatch(step["id"]) or step["id"] in names:
            raise ValidationError("workflow step ids must be unique")
        names.add(step["id"])
        kind = step.get("type")
        allowed = {"id", "type"}
        if kind == "ask":
            allowed |= {"field", "question", "schema"}
            if (
                not isinstance(step.get("field"), str)
                or not _ID.fullmatch(step["field"])
                or not isinstance(step.get("question"), str)
                or not step["question"].strip()
                or not isinstance(step.get("schema"), dict)
            ):
                raise ValidationError("question step is incomplete")
        elif kind == "tool":
            allowed |= {"tool_id", "tool_version", "arguments"}
            if (
                not isinstance(step.get("tool_id"), str)
                or not isinstance(step.get("tool_version"), str)
                or not isinstance(step.get("arguments"), dict)
            ):
                raise ValidationError("tool step is incomplete")
        elif kind != "archive":
            raise ValidationError("unsupported workflow step type")
        if set(step) - allowed:
            raise ValidationError("workflow cannot supply grants, scripts or approval ids")
    fingerprint(value)
    return copy.deepcopy(value)


def resolve(value: Any, inputs: dict[str, Any], outputs: dict[str, Any], depth: int = 0) -> Any:
    if depth > 32:
        raise ValidationError("workflow references are too deep")
    if isinstance(value, dict):
        if "$value" in value:
            if set(value) != {"$value"} or not isinstance(value["$value"], str):
                raise ValidationError("invalid workflow reference")
            parts = value["$value"].split(".")
            if not parts or parts[0] not in {"inputs", "outputs"}:
                raise ValidationError("reference must address this run's inputs or outputs")
            current: Any = inputs if parts[0] == "inputs" else outputs
            for part in parts[1:]:
                if not isinstance(current, Mapping) or part not in current:
                    raise ValidationError("required workflow material is not available")
                current = current[part]
            return copy.deepcopy(current)
        return {k: resolve(v, inputs, outputs, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, inputs, outputs, depth + 1) for v in value]
    return value


def next_proposal(
    definition: dict[str, Any], snapshot: dict[str, Any], inputs: dict[str, Any]
) -> dict[str, Any]:
    workflow = validate_workflow(definition)
    digest = fingerprint(workflow)
    if "workflow_digest" in snapshot and snapshot["workflow_digest"] != digest:
        raise WorkflowBindingConflict(
            "workflow is already bound; create a new task to change its definition or mode"
        )
    snapshot["workflow_digest"], snapshot["workflow"] = digest, workflow
    if "input_schema" in workflow:
        validate_json_schema(inputs, workflow["input_schema"])
    cursor = snapshot.get("cursor", 0)
    if type(cursor) is not int or not 0 <= cursor <= len(workflow["steps"]):
        raise ValidationError("invalid workflow checkpoint")
    while cursor < len(workflow["steps"]):
        step = workflow["steps"][cursor]
        snapshot["cursor"], snapshot["step_id"] = cursor, step["id"]
        if step["type"] == "ask":
            field = step["field"]
            if field not in inputs:
                snapshot["pending_input"] = {
                    "field": field,
                    "question": step["question"],
                    "schema": step["schema"],
                }
                return {"kind": "NEEDS_USER", "message": step["question"]}
            validate_json_schema(inputs[field], step["schema"])
            snapshot.pop("pending_input", None)
            cursor += 1
            snapshot["cursor"] = cursor
            continue
        if step["type"] == "tool":
            return {
                "kind": "CALL_TOOL",
                "tool_id": step["tool_id"],
                "tool_version": step["tool_version"],
                "arguments": resolve(step["arguments"], inputs, snapshot.get("outputs", {})),
            }
        return {
            "kind": "COMPLETE",
            "message": "工作流已完成；来源、步骤结果和审批引用已保留。",
            "result": snapshot.get("outputs", {}),
        }
    return {"kind": "COMPLETE", "message": "工作流已完成。", "result": snapshot.get("outputs", {})}
