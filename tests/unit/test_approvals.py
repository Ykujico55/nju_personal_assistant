from __future__ import annotations

import unittest
from datetime import timedelta

from personal_assistant.core.approvals import (
    ApprovalBinding,
    ApprovalBindingMismatchError,
    ApprovalService,
    ApprovalStateError,
    InMemoryApprovalRepository,
    InvalidApprovalNonceError,
)
from personal_assistant.domain import ApprovalState, ValidationError


def binding(payload: dict[str, object] | None = None) -> ApprovalBinding:
    return ApprovalBinding(
        action_type="mail.send",
        task_id="task-1",
        tool_id="smail.send",
        tool_version="1",
        extension_id="nju.smail",
        extension_version="0.1.0",
        target={"to": "student@example.edu"},
        payload=payload or {"subject": "hello", "body": "body"},
    )


class ApprovalServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_approval_is_exact_and_single_use(self) -> None:
        service = ApprovalService(InMemoryApprovalRepository())
        prepared = await service.prepare(binding())
        approved = await service.approve(
            prepared.id, nonce=prepared.nonce, actor_id="owner"
        )
        self.assertEqual(ApprovalState.APPROVED, approved.state)
        executing = await service.consume_for_execution(prepared.id, binding())
        self.assertEqual(ApprovalState.EXECUTING, executing.state)
        with self.assertRaises(ApprovalStateError):
            await service.consume_for_execution(prepared.id, binding())

    async def test_payload_change_burns_approval(self) -> None:
        service = ApprovalService(InMemoryApprovalRepository())
        prepared = await service.prepare(binding())
        await service.approve(prepared.id, nonce=prepared.nonce, actor_id="owner")
        with self.assertRaises(ApprovalBindingMismatchError):
            await service.consume_for_execution(
                prepared.id, binding({"subject": "changed", "body": "body"})
            )
        cancelled = await service.get(prepared.id)
        self.assertEqual(ApprovalState.CANCELLED, cancelled.state)

    async def test_ttl_cannot_exceed_five_minutes(self) -> None:
        service = ApprovalService(InMemoryApprovalRepository())
        with self.assertRaises(ValidationError):
            await service.prepare(binding(), ttl=timedelta(minutes=6))

    async def test_non_ascii_nonce_is_rejected_as_invalid_nonce(self) -> None:
        service = ApprovalService(InMemoryApprovalRepository())
        prepared = await service.prepare(binding())
        with self.assertRaises(InvalidApprovalNonceError):
            await service.approve(prepared.id, nonce="é-not-the-nonce", actor_id="owner")


if __name__ == "__main__":
    unittest.main()
