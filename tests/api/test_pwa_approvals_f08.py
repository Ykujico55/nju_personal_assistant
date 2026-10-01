"""F08.4 public approval must fail closed for incomplete browser actions."""

from __future__ import annotations

import asyncio
import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from personal_assistant.app import create_app
from personal_assistant.bootstrap import Container, build_container
from personal_assistant.core.approvals import ApprovalBinding
from personal_assistant.domain import ApprovalState, RiskLevel, ToolDescriptor
from personal_assistant.domain.models import AttachmentDigest
from personal_assistant.settings import Settings


class PwaApprovalApiTests(unittest.TestCase):
    def test_mail_snapshot_must_match_actual_attachments_even_when_empty(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
        container = build_container(settings)
        digest = "a" * 64
        actual = [{"name": "important.pdf", "sha256": digest, "size_bytes": 18}]
        container.tool_registry.publish(
            (
                ToolDescriptor(
                    id="smail.send", version="0.2.0", extension_id="nju.smail",
                    extension_version="0.2.0", risk=RiskLevel.EXTERNAL_WRITE,
                    input_schema={}, output_schema={},
                    required_capabilities=frozenset({"mail.send"}),
                ),
                ToolDescriptor(
                    id="smail.send.preview", version="0.2.0", extension_id="nju.smail",
                    extension_version="0.2.0", risk=RiskLevel.READ,
                    input_schema={}, output_schema={},
                ),
            )
        )
        original = AttachmentDigest(name="important.pdf", sha256=digest, size_bytes=18)
        cases = (
            ("bound empty", (), actual, False),
            ("actual empty", (original,), [], False),
            ("wrong name", (AttachmentDigest("other.pdf", digest, 18),), actual, False),
            ("wrong size", (AttachmentDigest("important.pdf", digest, 19),), actual, False),
            ("wrong hash", (AttachmentDigest("important.pdf", "b" * 64, 18),), actual, False),
            ("exact", (original,), actual, True),
        )
        with (
            patch.object(Container, "refresh_tool_registry", new_callable=AsyncMock),
            patch.object(container.mail_send_executor, "review", new_callable=AsyncMock) as review,
            TestClient(create_app(settings=settings, container=container)) as client,
        ):
            for label, bound, actual_attachments, expected_ready in cases:
                with self.subTest(label=label):
                    review.return_value = {"attachments": actual_attachments}
                    record = asyncio.run(
                        container.approvals.prepare(
                            ApprovalBinding(
                                action_type="smail.send", task_id="task-1",
                                tool_id="smail.send", tool_version="0.2.0",
                                extension_id="nju.smail", extension_version="0.2.0",
                                target={"account_id": "nju"},
                                payload={"account_id": "nju", "attachment_hashes": [digest]},
                                attachments=bound,
                            )
                        )
                    )
                    response = client.get(f"/api/v1/approvals/{record.id}")
                    self.assertEqual(200, response.status_code, response.text)
                    self.assertEqual(expected_ready, response.json()["review"]["ready"])
                    self.assertEqual(
                        record.nonce if expected_ready else None,
                        response.json()["nonce"],
                    )
                    if not expected_ready:
                        approval = client.post(
                            f"/api/v1/approvals/{record.id}/approve",
                            json={"nonce": record.nonce},
                            headers={"Idempotency-Key": f"attachment-{label.replace(' ', '-')}"},
                        )
                        self.assertEqual(409, approval.status_code, approval.text)
                        self.assertEqual(
                            ApprovalState.WAITING_APPROVAL,
                            asyncio.run(container.approvals.get(record.id)).state,
                        )

    def test_expired_review_hides_nonce_and_keeps_expired_status(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
        container = build_container(settings)
        record = asyncio.run(
            container.approvals.prepare(
                ApprovalBinding(
                    action_type="smail.send",
                    task_id="task-1",
                    tool_id="smail.send",
                    tool_version="0.1.0",
                    extension_id="nju.smail",
                    extension_version="0.1.0",
                    target={"account_id": "nju"},
                    payload={"account_id": "nju"},
                ),
                now=datetime.now(UTC) - timedelta(minutes=10),
            )
        )
        with TestClient(create_app(settings=settings, container=container)) as client:
            response = client.get(f"/api/v1/approvals/{record.id}")
            self.assertEqual("EXPIRED", response.json()["state"])
            self.assertIsNone(response.json()["nonce"])
            approval = client.post(
                f"/api/v1/approvals/{record.id}/approve",
                json={"nonce": record.nonce},
                headers={"Idempotency-Key": "expired-approval-attempt"},
            )
            self.assertEqual(410, approval.status_code, approval.text)

    def test_f07_trial_preview_cannot_be_approved_through_public_api(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
        container = build_container(settings)
        record = asyncio.run(
            container.approvals.prepare(
                ApprovalBinding(
                    action_type="ehall.submit",
                    task_id="task-1",
                    tool_id="ehall.submit",
                    tool_version="0.1.0",
                    extension_id="nju.ehall",
                    extension_version="0.1.0",
                    target={"origin": "https://ehall.example.test", "transaction_id": "trial"},
                    payload={
                        "session_id": "trial-session",
                        "preview_hash": "a" * 64,
                        "preview_nonce": "trial-nonce",
                        "action_id": "trial-action",
                    },
                )
            )
        )
        with TestClient(create_app(settings=settings, container=container)) as client:
            response = client.get(f"/api/v1/approvals/{record.id}")
            self.assertEqual(200, response.status_code, response.text)
            self.assertFalse(response.json()["review"]["ready"])
            self.assertIsNone(response.json()["nonce"])
            self.assertEqual("no-store", response.headers["cache-control"])
            approval = client.post(
                f"/api/v1/approvals/{record.id}/approve",
                json={"nonce": record.nonce},
                headers={"Idempotency-Key": "trial-approval-attempt"},
            )
            self.assertEqual(409, approval.status_code, approval.text)
            self.assertEqual("APPROVAL_REVIEW_UNAVAILABLE", approval.json()["error"]["code"])
        self.assertEqual(
            ApprovalState.WAITING_APPROVAL,
            asyncio.run(container.approvals.get(record.id)).state,
        )
