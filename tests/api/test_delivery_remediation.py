from __future__ import annotations

import copy
import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from personal_assistant.app import create_app
from personal_assistant.bootstrap import build_container
from personal_assistant.core.jobs.queue import JobState
from personal_assistant.domain import TaskState
from personal_assistant.runtime import build_runtime
from personal_assistant.settings import Settings


def workflow(identifier="fixture.first", version="1", extension_version="1"):
    return {
        "id": identifier,
        "version": version,
        "extension_id": "fixture.catalog",
        "extension_version": extension_version,
        "steps": [
            {
                "id": "material",
                "type": "ask",
                "field": "answer",
                "question": "Provide synthetic material",
                "schema": {"type": "string"},
            },
            {"id": "done", "type": "archive"},
        ],
    }


class ExecutionBindingRemediationTests(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, {}, clear=True):
            self.settings = Settings.from_env()
        self.container = build_container(self.settings)
        self.client = TestClient(create_app(settings=self.settings, container=self.container))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.first = workflow()
        self.other = workflow("fixture.second")
        self.new_version = workflow(version="2")
        self.new_extension = workflow(extension_version="2")
        self.catalog = patch.object(
            self.container.extension_supervisor,
            "list_declarations",
            new=AsyncMock(
                return_value=[self.first, self.other, self.new_version, self.new_extension]
            ),
        )
        self.catalog.start()
        self.addCleanup(self.catalog.stop)
        self.runtime = build_runtime(self.container)
        created = self.client.post(
            "/api/v1/tasks",
            json={"objective": "Synthetic workflow binding fixture"},
            headers={"Idempotency-Key": "binding-create"},
        )
        self.assertEqual(202, created.status_code, created.text)
        self.task_id = created.json()["id"]
        self.route = f"/api/v1/tasks/{self.task_id}/execution"
        selected = self.post(self.first, key="binding-select")
        self.assertEqual(202, selected.status_code, selected.text)
        self.assertTrue(self.client.portal.call(self.runtime.work_once))
        self.assertEqual("WAITING_USER", self.current_task()["state"])
        self.assertFalse(self.client.portal.call(self.runtime.work_once))

    def current_task(self):
        return self.client.get(f"/api/v1/tasks/{self.task_id}").json()["task"]

    def post(self, definition, *, key, inputs=None):
        return self.client.post(
            self.route,
            json={
                "version": self.current_task()["version"],
                "mode": "workflow" if definition else "agent",
                "workflow": (
                    {
                        field: definition[field]
                        for field in ("id", "version", "extension_id", "extension_version")
                    }
                    if definition
                    else None
                ),
                "inputs": inputs or {},
            },
            headers={"Idempotency-Key": key},
        )

    def snapshot(self):
        async def read():
            return {
                "task": await self.container.tasks.get(self.task_id),
                "run": await self.container.run_repository.get("run_" + self.task_id),
                "checkpoint": await self.container.checkpoint_store.load("run_" + self.task_id),
                "request": await self.container.records.get("requests", self.task_id),
                "execution": await self.container.records.get("executions", self.task_id),
                "messages": await self.container.tasks.messages(self.task_id),
            }

        return self.client.portal.call(read)

    def assert_rejected_without_mutation(self, target):
        before = self.snapshot()
        with patch.object(
            self.container.jobs, "enqueue", new=AsyncMock(wraps=self.container.jobs.enqueue)
        ) as enqueue:
            changed = self.post(target, key="binding-change", inputs={"answer": "not adopted"})
        self.assertEqual(409, changed.status_code, changed.text)
        self.assertIn("new", changed.text.lower())
        enqueue.assert_not_awaited()
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.client.portal.call(self.runtime.work_once))
        continued = self.post(self.first, key="binding-continue", inputs={"answer": "reviewed"})
        self.assertEqual(202, continued.status_code, continued.text)
        self.assertTrue(self.client.portal.call(self.runtime.work_once))
        after = self.snapshot()
        self.assertEqual(TaskState.SUCCEEDED, after["task"].state)
        self.assertEqual(TaskState.SUCCEEDED, after["run"].state)
        self.assertEqual(self.first, after["checkpoint"]["workflow"])
        self.assertEqual("reviewed", after["request"]["value"]["inputs"]["answer"])
        job = self.client.portal.call(self.container.jobs.get, continued.json()["job_id"])
        self.assertEqual(JobState.SUCCEEDED, job.state)

    def test_another_legal_catalog_workflow_is_rejected_before_queueing(self):
        self.assert_rejected_without_mutation(self.other)

    def test_new_workflow_version_is_rejected_before_queueing(self):
        self.assert_rejected_without_mutation(self.new_version)

    def test_new_extension_version_is_rejected_before_queueing(self):
        self.assert_rejected_without_mutation(self.new_extension)

    def test_changed_definition_under_same_catalog_identity_is_rejected(self):
        changed = copy.deepcopy(self.first)
        changed["steps"][0]["question"] = "A materially different workflow question"
        self.container.extension_supervisor.list_declarations.return_value = [changed]
        before = self.snapshot()
        with patch.object(
            self.container.jobs, "enqueue", new=AsyncMock(wraps=self.container.jobs.enqueue)
        ) as enqueue:
            response = self.post(changed, key="binding-definition-change")
        self.assertEqual(409, response.status_code, response.text)
        enqueue.assert_not_awaited()
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.client.portal.call(self.runtime.work_once))

    def test_agent_mode_cannot_replace_a_bound_workflow(self):
        self.assert_rejected_without_mutation(None)

    def test_worker_binding_fault_fails_job_and_preserves_original_checkpoint(self):
        original = self.snapshot()
        continuation = self.post(
            self.first, key="binding-valid-continue", inputs={"answer": "reviewed"}
        )
        self.assertEqual(202, continuation.status_code, continuation.text)

        async def corrupt_internal_request():
            saved = await self.container.records.get("requests", self.task_id)
            value = copy.deepcopy(saved["value"])
            value["workflow"] = copy.deepcopy(self.other)
            await self.container.records.put(
                "requests", self.task_id, value, expected_version=saved["version"]
            )

        self.client.portal.call(corrupt_internal_request)
        self.assertTrue(self.client.portal.call(self.runtime.work_once))
        job = self.client.portal.call(self.container.jobs.get, continuation.json()["job_id"])
        self.assertEqual(JobState.FAILED, job.state)
        self.assertNotEqual({"superseded": True}, job.result)
        current = self.snapshot()
        self.assertEqual(TaskState.WAITING_USER, current["task"].state)
        self.assertEqual(TaskState.WAITING_USER, current["run"].state)
        for field in ("workflow", "workflow_digest", "cursor", "pending_input"):
            self.assertEqual(original["checkpoint"][field], current["checkpoint"][field])
        self.assertFalse(self.client.portal.call(self.runtime.work_once))

    def test_old_queued_agent_mode_cannot_bypass_existing_workflow_binding(self):
        original = self.snapshot()
        continuation = self.post(
            self.first, key="binding-old-queued-agent", inputs={"answer": "reviewed"}
        )
        self.assertEqual(202, continuation.status_code, continuation.text)

        async def mutate_old_queued_request():
            saved = await self.container.records.get("requests", self.task_id)
            value = copy.deepcopy(saved["value"])
            value["mode"] = "agent"
            value["workflow"] = None
            await self.container.records.put(
                "requests", self.task_id, value, expected_version=saved["version"]
            )

        self.client.portal.call(mutate_old_queued_request)
        self.runtime.planner.propose = AsyncMock(
            return_value={"kind": "COMPLETE", "message": "Must not be accepted"}
        )
        self.assertTrue(self.client.portal.call(self.runtime.work_once))
        job = self.client.portal.call(self.container.jobs.get, continuation.json()["job_id"])
        self.assertEqual(JobState.FAILED, job.state)
        self.runtime.planner.propose.assert_not_awaited()
        current = self.snapshot()
        self.assertEqual(TaskState.WAITING_USER, current["task"].state)
        self.assertEqual(TaskState.WAITING_USER, current["run"].state)
        for field in ("workflow", "workflow_digest", "cursor", "pending_input"):
            self.assertEqual(original["checkpoint"][field], current["checkpoint"][field])
