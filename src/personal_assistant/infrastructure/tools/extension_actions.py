"""Exact, business-neutral review of a trusted extension's external action."""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Protocol

from personal_assistant.core.approvals.canonicalize import canonical_sha256
from personal_assistant.core.tools.gateway import DefinitiveToolFailure
from personal_assistant.core.tools.registry import ToolRegistry
from personal_assistant.domain import RiskLevel, ToolCall, ToolDescriptor
from personal_assistant.domain.models import AttachmentDigest
from personal_assistant.infrastructure.mail.executor import ExtensionToolInvoker


class ActionArtifacts(Protocol):
    async def metadata_for(self, artifact_id: str) -> Mapping[str, Any]: ...
    async def read_as_host(self, artifact_id: str) -> bytes: ...


class ExtensionActionService:
    def __init__(
        self, invoker: ExtensionToolInvoker, registry: ToolRegistry, artifacts: ActionArtifacts
    ) -> None:
        self.invoker = invoker
        self.registry = registry
        self.artifacts = artifacts

    async def prepare_call(self, call: ToolCall) -> ToolCall:
        descriptor = self.registry.snapshot().resolve(call.tool_id, call.tool_version)
        details = await self.review(descriptor, call.arguments, task_id=call.task_id)
        return replace(
            call,
            arguments=copy.deepcopy(dict(call.arguments)),
            target=copy.deepcopy(details["target"]),
            idempotency_key=call.arguments["local_action_id"],
            attachments=tuple(
                AttachmentDigest(
                    name=item["name"],
                    sha256=item["sha256"],
                    size_bytes=item["size_bytes"],
                )
                for item in details["attachments"]
            ),
        )

    async def review(
        self, descriptor: ToolDescriptor, arguments: Mapping[str, Any], *, task_id: str
    ) -> dict[str, Any]:
        try:
            return await self._review(descriptor, arguments, task_id=task_id)
        except DefinitiveToolFailure:
            raise
        except Exception:
            # This is a READ-only preparation, never an external dispatch.
            raise DefinitiveToolFailure(
                "exact extension action materials are unavailable",
            ) from None

    async def _review(
        self, descriptor: ToolDescriptor, arguments: Mapping[str, Any], *, task_id: str
    ) -> dict[str, Any]:
        preview = self.registry.snapshot().resolve(descriptor.id + ".preview", descriptor.version)
        if (
            descriptor.risk is not RiskLevel.EXTERNAL_WRITE
            or preview.risk is not RiskLevel.READ
            or preview.extension_id != descriptor.extension_id
            or preview.extension_version != descriptor.extension_version
        ):
            raise DefinitiveToolFailure("a matching READ preview is required")
        key, target = arguments.get("local_action_id"), arguments.get("target")
        consequences, attachments = arguments.get("consequences"), arguments.get("attachments")
        if (
            not isinstance(key, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key) is None
            or not isinstance(target, Mapping)
            or not target
            or not isinstance(consequences, str)
            or not consequences.strip()
            or len(consequences) > 4000
            or not isinstance(attachments, list)
            or len(attachments) > 16
        ):
            raise DefinitiveToolFailure(
                "the complete command, target and consequences are required",
            )
        result = await self.invoker.invoke_tool(
            preview.extension_id,
            preview.id,
            arguments,
            task_id=task_id,
            run_id="action-preview",
            idempotency_key="preview-" + canonical_sha256(arguments),
            deadline_seconds=min(30.0, preview.timeout_seconds),
        )
        output = result.get("output")
        if result.get("outcome") != "SUCCEEDED" or not isinstance(output, Mapping):
            raise DefinitiveToolFailure("the extension cannot confirm the current action")
        if (
            output.get("kind") != "EXTENSION_ACTION"
            or canonical_sha256(output.get("payload")) != canonical_sha256(arguments)
            or canonical_sha256(output.get("target")) != canonical_sha256(target)
            or output.get("consequences") != consequences
            or canonical_sha256(output.get("attachments")) != canonical_sha256(attachments)
        ):
            raise DefinitiveToolFailure("the preview differs from the exact command")
        verified = []
        names: set[str] = set()
        total = 0
        for item in attachments:
            if not isinstance(item, Mapping) or set(item) != {
                "artifact_id",
                "name",
                "sha256",
                "size_bytes",
            }:
                raise DefinitiveToolFailure("attachment material is incomplete")
            name, size = item["name"], item["size_bytes"]
            if (
                not isinstance(name, str)
                or not name
                or len(name) > 200
                or name in names
                or type(size) is not int
                or size < 0
            ):
                raise DefinitiveToolFailure("attachment identity is invalid")
            digest = AttachmentDigest(name=name, sha256=item["sha256"], size_bytes=size)
            total += size
            if total > 8 * 1024 * 1024:
                raise DefinitiveToolFailure("attachment material exceeds the limit")
            metadata = await self.artifacts.metadata_for(item["artifact_id"])
            if (
                metadata.get("owner_extension_id") != descriptor.extension_id
                or metadata.get("owner_extension_version") != descriptor.extension_version
            ):
                raise DefinitiveToolFailure("attachment is not owned by this extension version")
            data = await self.artifacts.read_as_host(item["artifact_id"])
            if len(data) != size or hashlib.sha256(data).hexdigest() != digest.sha256:
                raise DefinitiveToolFailure("attachment content changed")
            names.add(name)
            verified.append({"name": name, "sha256": digest.sha256, "size_bytes": size})
        return {
            "target": copy.deepcopy(dict(target)),
            "payload": copy.deepcopy(dict(arguments)),
            "consequences": consequences,
            "attachments": verified,
            "payload_sha256": canonical_sha256(arguments),
        }
