"""Read-only comparison of pinned Agent tools and the current catalog."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from personal_assistant.domain import TaskState, ToolDescriptor

_RECREATABLE = frozenset({
    TaskState.WAITING_USER, TaskState.PAUSED_SAFETY, TaskState.PAUSED_EXTENSION,
    TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED,
})


def project_toolset(
    checkpoint: Mapping[str, Any], descriptors: Sequence[ToolDescriptor],
    grants: frozenset[str] | Mapping[str, frozenset[str]], *,
    task_state: TaskState, requires_reconciliation: bool, pending_external: bool,
) -> dict[str, Any]:
    current = {
        item.id: {
            "id": item.id, "version": item.version, "extension_id": item.extension_id,
            "extension_version": item.extension_version,
            "grants": sorted(grants.get(item.id, frozenset())
                             if isinstance(grants, Mapping) else grants),
        }
        for item in descriptors
    }
    added: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []
    invalid = False
    if "tools" not in checkpoint:
        state = "UNBOUND"
        message = "本任务尚未绑定工具快照；首次执行时绑定当时的工具集合。"
    else:
        pinned = checkpoint["tools"]
        invalid = not isinstance(pinned, Mapping)
        if isinstance(pinned, Mapping):
            added = [current[key] for key in sorted(current) if key not in pinned]
            for key, identity in sorted(pinned.items(), key=lambda item: str(item[0])):
                if not isinstance(key, str) or not isinstance(identity, Mapping):
                    invalid = True
                    continue
                live = current.get(key)
                reason = None
                if live is None:
                    reason = "TOOL_UNAVAILABLE"
                elif identity.get("version") != live["version"]:
                    reason = "TOOL_VERSION_CHANGED"
                elif identity.get("extension_version") != live["extension_version"]:
                    reason = "EXTENSION_VERSION_CHANGED"
                elif ("extension_id" in identity
                      and identity["extension_id"] != live["extension_id"]):
                    reason = "EXTENSION_CHANGED"
                elif identity.get("grants") != live["grants"]:
                    reason = "GRANTS_CHANGED"
                if reason is not None:
                    # Historical checkpoints do not record owner IDs. Never infer
                    # the old owner from whichever extension currently owns an ID.
                    unavailable.append({
                        "id": key, "version": identity.get("version"),
                        "extension_id": identity.get("extension_id"),
                        "extension_version": identity.get("extension_version"),
                        "grants": identity.get("grants"), "reason": reason,
                    })
        state = "CHANGED" if added or unavailable or invalid else "BOUND"
        message = (
            "本任务固定使用开始时的工具快照，不会自动获得后来启用的工具。"
            "工具变化或缺失不代表知识目录未配置；如需使用当前工具，可显式携原目标新建任务。"
            if state == "CHANGED" else
            "本任务已绑定工具快照；当前工具版本、扩展版本和授权与已记录信息一致。"
        )
        if invalid:
            message = "本任务的工具快照无法核实，请先检查原任务；没有修改旧快照。"
    can_recreate = (task_state in _RECREATABLE and not invalid
                    and not requires_reconciliation and not pending_external)
    if requires_reconciliation or pending_external:
        message += " 原任务仍有待审批或未决外部动作，请先处理原任务，不能据此重复执行。"
    elif task_state not in _RECREATABLE:
        message += " 原任务尚未处于可新建替代任务的非活动状态。"
    return {"state": state, "added": added, "unavailable": unavailable,
            "can_recreate": can_recreate, "message": message}
