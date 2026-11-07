from __future__ import annotations

import copy
import unittest
from unittest.mock import AsyncMock

from personal_assistant.core.tools.gateway import DefinitiveToolFailure
from personal_assistant.core.tools.registry import ToolRegistry
from personal_assistant.domain import RiskLevel, ToolCall, ToolDescriptor
from personal_assistant.infrastructure.tools.extension_actions import ExtensionActionService


class PreviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.descriptor = ToolDescriptor(
            id="fixture.action",
            version="1",
            extension_id="fixture",
            extension_version="1",
            risk=RiskLevel.EXTERNAL_WRITE,
            input_schema={},
            output_schema={},
        )
        preview = ToolDescriptor(
            id="fixture.action.preview",
            version="1",
            extension_id="fixture",
            extension_version="1",
            risk=RiskLevel.READ,
            input_schema={},
            output_schema={},
        )
        self.registry = ToolRegistry()
        self.registry.publish((self.descriptor, preview))
        self.args = {
            "local_action_id": "fixture-1",
            "target": {"url": "https://example.test/item"},
            "operation": {"text": "synthetic"},
            "consequences": "Sends the exact text",
            "attachments": [],
        }
        self.invoker = AsyncMock()
        self.artifacts = AsyncMock()
        self.service = ExtensionActionService(self.invoker, self.registry, self.artifacts)
        self.set_response()

    def set_response(self, **changes):
        output = {
            "kind": "EXTENSION_ACTION",
            "payload": copy.deepcopy(self.args),
            "target": self.args["target"],
            "consequences": self.args["consequences"],
            "attachments": [],
        }
        output.update(changes)
        self.invoker.invoke_tool.return_value = {"outcome": "SUCCEEDED", "output": output}

    async def test_prepared_call_binds_exact_arguments_target_and_key(self):
        call = await self.service.prepare_call(
            ToolCall(
                tool_id="fixture.action", tool_version="1", arguments=self.args, task_id="task1"
            )
        )
        self.assertEqual(self.args, call.arguments)
        self.assertEqual(self.args["target"], call.target)
        self.assertEqual("fixture-1", call.idempotency_key)
        self.assertEqual((), call.attachments)

    async def test_changed_payload_target_consequences_or_attachments_fail_closed(self):
        for change in (
            {"payload": {}},
            {"target": {}},
            {"consequences": "different"},
            {"attachments": [{"name": "undeclared"}]},
            {"kind": "MAIL"},
        ):
            self.set_response(**change)
            with self.subTest(change=change), self.assertRaises(DefinitiveToolFailure):
                await self.service.review(self.descriptor, self.args, task_id="task1")

    async def test_unavailable_preview_is_not_approvable(self):
        self.invoker.invoke_tool.return_value = {"outcome": "NEEDS_USER_ACTION", "output": {}}
        with self.assertRaises(DefinitiveToolFailure):
            await self.service.review(self.descriptor, self.args, task_id="task1")

    async def test_wrong_extension_preview_is_rejected(self):
        wrong = ToolDescriptor(
            id="fixture.action.preview",
            version="1",
            extension_id="other",
            extension_version="1",
            risk=RiskLevel.READ,
            input_schema={},
            output_schema={},
        )
        self.registry.publish((self.descriptor, wrong))
        with self.assertRaises(DefinitiveToolFailure):
            await self.service.review(self.descriptor, self.args, task_id="task1")

    async def test_attachment_bytes_and_ownership_are_verified(self):
        import hashlib

        data = b"synthetic attachment"
        item = {
            "artifact_id": "art_fixture",
            "name": "resume.pdf",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }
        self.args["attachments"] = [item]
        self.set_response(attachments=[item])
        self.artifacts.metadata_for.return_value = {
            "owner_extension_id": "fixture",
            "owner_extension_version": "1",
        }
        self.artifacts.read_as_host.return_value = data
        detail = await self.service.review(self.descriptor, self.args, task_id="task1")
        self.assertEqual(item["sha256"], detail["attachments"][0]["sha256"])
        self.artifacts.read_as_host.return_value = b"changed"
        with self.assertRaises(DefinitiveToolFailure):
            await self.service.review(self.descriptor, self.args, task_id="task1")
        self.artifacts.metadata_for.return_value = {
            "owner_extension_id": "other",
            "owner_extension_version": "1",
        }
        with self.assertRaises(DefinitiveToolFailure):
            await self.service.review(self.descriptor, self.args, task_id="task1")
