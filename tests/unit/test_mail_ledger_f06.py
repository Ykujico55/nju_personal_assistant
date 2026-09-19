"""F06 unit tests: in-memory transport ledger state machine and binding guards."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from personal_assistant.core.mail import (
    MailDeliveryRecord,
    MailDeliveryStatus,
    MailLedgerConflictError,
    MailLedgerStateError,
)
from personal_assistant.infrastructure.memory.mail import InMemoryMailDeliveryLedger


def _record(
    local_action_id: str = "act-1",
    *,
    account_id: str = "nju",
    message_id: str = "<smail.ledger@example.test>",
    digest: str = "a" * 64,
) -> MailDeliveryRecord:
    return MailDeliveryRecord(
        local_action_id=local_action_id,
        account_id=account_id,
        message_id=message_id,
        status=MailDeliveryStatus.PREPARED,
        envelope_digest=digest,
        mime_sha256="b" * 64,
    )


class MemoryLedgerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.ledger = InMemoryMailDeliveryLedger()

    async def _unknown(self, local_action_id: str = "act-1") -> None:
        await self.ledger.prepare(_record(local_action_id))
        await self.ledger.begin_execution(
            local_action_id, "a" * 64, owner_id="owner-1", lease_seconds=120
        )
        await self.ledger.finalize(
            local_action_id, "a" * 64, status=MailDeliveryStatus.UNKNOWN
        )

    async def test_reconciliation_requires_account_and_message_binding(self) -> None:
        await self._unknown()
        self.assertEqual(
            MailDeliveryStatus.UNKNOWN,
            await self.ledger.reconcile(
                "act-1",
                account_id="other",
                message_id="<smail.ledger@example.test>",
                status=MailDeliveryStatus.SUCCEEDED,
            ),
        )
        self.assertEqual(
            MailDeliveryStatus.UNKNOWN,
            await self.ledger.reconcile(
                "act-1",
                account_id="nju",
                message_id="<other@example.test>",
                status=MailDeliveryStatus.SUCCEEDED,
            ),
        )
        self.assertEqual(
            MailDeliveryStatus.SUCCEEDED,
            await self.ledger.reconcile(
                "act-1",
                account_id="nju",
                message_id="<smail.ledger@example.test>",
                status=MailDeliveryStatus.SUCCEEDED,
            ),
        )

    async def test_terminal_states_never_move_back(self) -> None:
        await self.ledger.prepare(_record())
        await self.ledger.begin_execution(
            "act-1", "a" * 64, owner_id="owner-1", lease_seconds=120
        )
        await self.ledger.finalize(
            "act-1", "a" * 64, status=MailDeliveryStatus.FAILED
        )
        # Duplicate finalize is idempotent and reconciliation cannot lift FAILED.
        self.assertEqual(
            MailDeliveryStatus.FAILED,
            await self.ledger.finalize(
                "act-1", "a" * 64, status=MailDeliveryStatus.SUCCEEDED
            ),
        )
        self.assertEqual(
            MailDeliveryStatus.FAILED,
            await self.ledger.reconcile(
                "act-1",
                account_id="nju",
                message_id="<smail.ledger@example.test>",
                status=MailDeliveryStatus.SUCCEEDED,
            ),
        )

    async def test_conflicting_envelope_is_rejected(self) -> None:
        await self.ledger.prepare(_record())
        with self.assertRaises(MailLedgerConflictError):
            await self.ledger.prepare(_record(digest="c" * 64))

    async def test_live_lease_is_never_burned_by_a_second_begin(self) -> None:
        await self.ledger.prepare(_record())
        await self.ledger.begin_execution(
            "act-1", "a" * 64, owner_id="owner-1", lease_seconds=600
        )
        second = await self.ledger.begin_execution(
            "act-1", "a" * 64, owner_id="owner-2", lease_seconds=600
        )
        self.assertEqual(MailDeliveryStatus.EXECUTING, second)
        record = await self.ledger.get("act-1")
        assert record is not None
        self.assertEqual("owner-1", record.owner_id)
        self.assertEqual((), await self.ledger.recover_stale_executions())

    async def test_recovery_sweeps_only_expired_executions(self) -> None:
        await self.ledger.prepare(_record("act-crashed"))
        await self.ledger.begin_execution(
            "act-crashed", "a" * 64, owner_id="crashed", lease_seconds=120
        )
        # Simulate a dead process by moving the lease into the past.
        record = await self.ledger.get("act-crashed")
        assert record is not None
        from dataclasses import replace

        async with self.ledger._lock:  # noqa: SLF001 - deterministic clock injection
            self.ledger._records["act-crashed"] = replace(  # noqa: SLF001
                record, lease_expires_at=datetime.now(UTC) - timedelta(seconds=1)
            )
        recovered = await self.ledger.recover_stale_executions()
        self.assertEqual(("act-crashed",), recovered)
        swept = await self.ledger.get("act-crashed")
        assert swept is not None
        self.assertEqual(MailDeliveryStatus.UNKNOWN, swept.status)
        self.assertEqual(
            MailDeliveryStatus.SUCCEEDED,
            await self.ledger.reconcile(
                "act-crashed",
                account_id="nju",
                message_id="<smail.ledger@example.test>",
                status=MailDeliveryStatus.SUCCEEDED,
            ),
        )

    async def test_unknown_action_and_invalid_lease_are_typed_errors(self) -> None:
        with self.assertRaises(MailLedgerStateError):
            await self.ledger.begin_execution(
                "missing", "a" * 64, owner_id="owner", lease_seconds=60
            )
        await self.ledger.prepare(_record())
        with self.assertRaises(MailLedgerStateError):
            await self.ledger.begin_execution(
                "act-1", "a" * 64, owner_id="", lease_seconds=60
            )
        with self.assertRaises(MailLedgerStateError):
            await self.ledger.begin_execution(
                "act-1", "a" * 64, owner_id="owner", lease_seconds=0
            )


    async def test_heartbeat_renews_only_the_owner(self) -> None:
        await self.ledger.prepare(_record())
        await self.ledger.begin_execution(
            "act-1", "a" * 64, owner_id="owner-1", lease_seconds=60
        )
        self.assertTrue(
            await self.ledger.heartbeat("act-1", "owner-1", lease_seconds=600)
        )
        self.assertFalse(
            await self.ledger.heartbeat("act-1", "owner-2", lease_seconds=600)
        )
        record = await self.ledger.get("act-1")
        assert record is not None
        self.assertEqual("owner-1", record.owner_id)

    async def test_active_owner_is_never_swept(self) -> None:
        from dataclasses import replace

        await self.ledger.prepare(_record("act-1"))
        await self.ledger.begin_execution(
            "act-1", "a" * 64, owner_id="owner-live", lease_seconds=120
        )
        record = await self.ledger.get("act-1")
        assert record is not None
        async with self.ledger._lock:  # noqa: SLF001
            self.ledger._records["act-1"] = replace(  # noqa: SLF001
                record, lease_expires_at=datetime.now(UTC) - timedelta(seconds=1)
            )
        # The owner is still active locally: the expired lease is not swept.
        self.assertEqual(
            (),
            await self.ledger.recover_stale_executions(active_owners={"owner-live"}),
        )
        self.assertEqual(
            ("act-1",),
            await self.ledger.recover_stale_executions(),
        )
        swept = await self.ledger.get("act-1")
        assert swept is not None
        self.assertEqual(MailDeliveryStatus.UNKNOWN, swept.status)
        self.assertEqual(
            MailDeliveryStatus.SUCCEEDED,
            await self.ledger.reconcile(
                "act-1",
                account_id="nju",
                message_id="<smail.ledger@example.test>",
                status=MailDeliveryStatus.SUCCEEDED,
            ),
        )


if __name__ == "__main__":

    unittest.main()
