"""Real temporary PostgreSQL: unresolved execution never replans or replays."""

from __future__ import annotations

import unittest

from personal_assistant.domain import RiskLevel
from tests.integration import test_postgres_delivery as postgres
from tests.unit import test_runtime_remediation_delivery as cases


@unittest.skipUnless(postgres.BASE, "set PA_TEST_DATABASE_URL for local temporary PostgreSQL")
class RuntimeUnknownPostgresTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = postgres.PostgresDeliveryTests.asyncSetUp
    asyncTearDown = postgres.PostgresDeliveryTests.asyncTearDown
    generate_unknown_checkpoint = cases.RuntimeUnknownRemediationTests.generate_unknown_checkpoint
    assert_resume_blocked = cases.RuntimeUnknownRemediationTests.assert_resume_blocked

    async def test_unknown_receipt_loss_remains_blocked_in_persisted_records(self):
        self.container = self.containers[0]
        runtime, task, executor = await self.generate_unknown_checkpoint()
        await self.assert_resume_blocked(runtime, task, executor)

    async def test_crashed_dispatch_reads_actual_risk_from_postgres_and_never_replays(self):
        self.container = self.containers[0]
        runtime, task, executor = await self.generate_unknown_checkpoint(crash=True)
        snapshot = await self.container.checkpoint_store.load("run_" + task.id)
        self.assertEqual(RiskLevel.EXTERNAL_WRITE.value, snapshot["risk"])
        self.assertEqual("dispatching", snapshot["phase"])
        await runtime.advance(task.id, resume=True)
        await self.assert_resume_blocked(runtime, task, executor)
