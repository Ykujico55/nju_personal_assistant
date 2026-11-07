"""Optional isolated PostgreSQL checks; no base database migrations or real models."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from personal_assistant.domain import TaskRun, TaskState
from personal_assistant.runtime import build_runtime
from tests.integration import test_postgres_delivery as postgres
from tests.unit import test_runtime_conversation_context as cases


@unittest.skipUnless(postgres.BASE, "set PA_TEST_DATABASE_URL for local temporary PostgreSQL")
class RuntimeConversationPostgresTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = postgres.PostgresDeliveryTests.asyncTearDown
    runtime = cases.RuntimeConversationTests.runtime
    add_message = cases.RuntimeConversationTests.add_message
    resume = cases.RuntimeConversationTests.resume
    test_question_context_survives_runtime_restart = (
        cases.RuntimeConversationTests.test_followup_keeps_model_question_across_runtime_restart_only
    )

    async def asyncSetUp(self):
        await postgres.PostgresDeliveryTests.asyncSetUp(self)
        self.container = self.containers[0]

    async def test_finish_failure_rolls_back_question_checkpoint_and_run_then_retry_once(self):
        runtime = build_runtime(self.container)
        task = await self.container.tasks.create(objective="atomic fixture", idempotency_key="c")
        run = TaskRun(id="run_" + task.id, task_id=task.id, objective=task.objective)
        await self.container.run_repository.add(run)
        with (
            patch.object(runtime.records, "put", side_effect=RuntimeError("fixture failure")),
            self.assertRaisesRegex(RuntimeError, "fixture failure"),
        ):
            await runtime.finish(
                run, TaskState.WAITING_USER, {}, "Choose A/B?", conversation=True,
            )
        self.assertEqual(task, await self.container.tasks.get(task.id))
        self.assertEqual(run, await self.container.run_repository.get(run.id))
        self.assertEqual((), await self.container.tasks.messages(task.id))
        self.assertEqual({}, await self.container.checkpoint_store.load(run.id))
        await runtime.finish(run, TaskState.WAITING_USER, {}, "Choose A/B?", conversation=True)
        messages = await self.container.tasks.messages(task.id)
        self.assertEqual(1, len(messages))
        snapshot = await self.container.checkpoint_store.load(run.id)
        self.assertEqual([messages[0].id], snapshot["conversation_message_ids"])
        self.assertEqual(TaskState.WAITING_USER, (await runtime.runs.get(run.id)).state)
