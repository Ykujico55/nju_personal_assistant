from __future__ import annotations

import asyncio
import copy
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from personal_assistant.app import create_app
from personal_assistant.bootstrap import Container, build_container
from personal_assistant.core.approvals import ApprovalBinding
from personal_assistant.domain import RiskLevel, ToolDescriptor
from personal_assistant.settings import Settings


class GenericApprovalTests(unittest.TestCase):
    def test_exact_review_approves_once_but_changed_materials_are_blocked(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        with patch.dict(os.environ, {}, clear=True):
            settings = replace(
                Settings.from_env(),
                extension_root=root / "extensions",
                artifact_root=root / "artifacts",
            )
        container = build_container(settings)
        descriptors = tuple(
            ToolDescriptor(
                id=tool,
                version="1",
                extension_id="fixture",
                extension_version="1",
                risk=risk,
                input_schema={},
                output_schema={},
            )
            for tool, risk in (
                ("fixture.action", RiskLevel.EXTERNAL_WRITE),
                ("fixture.action.preview", RiskLevel.READ),
            )
        )
        container.tool_registry.publish(descriptors)
        args = {
            "local_action_id": "action1",
            "target": {"url": "https://example.test/item"},
            "operation": {"text": "synthetic"},
            "consequences": "Send exact text",
            "attachments": [],
        }
        record = asyncio.run(
            container.approvals.prepare(
                ApprovalBinding(
                    action_type="fixture.action",
                    task_id="task1",
                    tool_id="fixture.action",
                    tool_version="1",
                    extension_id="fixture",
                    extension_version="1",
                    target=args["target"],
                    payload=args,
                )
            )
        )
        result = {
            "outcome": "SUCCEEDED",
            "output": {
                "kind": "EXTENSION_ACTION",
                "payload": copy.deepcopy(args),
                "target": args["target"],
                "consequences": args["consequences"],
                "attachments": [],
            },
        }
        with (
            patch.object(Container, "refresh_tool_registry", new_callable=AsyncMock),
            patch.object(
                container.extension_supervisor, "invoke_tool", new_callable=AsyncMock
            ) as call,
            TestClient(create_app(settings=settings, container=container)) as client,
        ):
            call.return_value = copy.deepcopy(result)
            shown = client.get(f"/api/v1/approvals/{record.id}")
            self.assertEqual("no-store", shown.headers["cache-control"])
            self.assertTrue(shown.json()["review"]["ready"])
            self.assertEqual("EXTENSION_ACTION", shown.json()["review"]["kind"])
            call.return_value["output"]["consequences"] = "changed"
            rejected = client.post(
                f"/api/v1/approvals/{record.id}/approve",
                json={"nonce": record.nonce},
                headers={"Idempotency-Key": "review-self-check"},
            )
            self.assertEqual(409, rejected.status_code)
            call.return_value = copy.deepcopy(result)
            first = client.post(
                f"/api/v1/approvals/{record.id}/approve",
                json={"nonce": record.nonce},
                headers={"Idempotency-Key": "review-self-check"},
            )
            self.assertEqual(200, first.status_code, first.text)
            self.assertEqual("APPROVED", first.json()["state"])
            self.assertIsNone(first.json()["nonce"])
            second = client.post(
                f"/api/v1/approvals/{record.id}/approve",
                json={"nonce": record.nonce},
                headers={"Idempotency-Key": "review-self-check"},
            )
            self.assertEqual(409, second.status_code)
