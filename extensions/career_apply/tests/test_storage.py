from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from career_apply.storage import LocalStore, PlanConflict


def plan(text="first", state="PREPARED"):
    return {"dedupe_key": "boss:job1:message", "command_digest": text,
            "state": state, "message": text, "execution_available": False}


class StorageTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = root / "private" / "plans.sqlite3"
        self.store = self.enterContext(LocalStore(self.path))

    def test_jobs_merge_keeps_existing_plan_and_persists_across_reopen(self):
        self.store.import_jobs([{"job_id": "job1", "title": "first"}])
        saved = self.store.save_plan(plan())
        self.store.import_jobs([{"job_id": "job1", "title": "updated"}])
        with LocalStore(self.path) as reopened:
            self.assertEqual("updated", reopened.get_job("job1")["title"])
            self.assertEqual(saved, reopened.get_plan(saved["id"]))

    def test_same_content_is_idempotent_and_changed_plan_requires_cas(self):
        saved = self.store.save_plan(plan())
        self.assertEqual(saved, self.store.save_plan(plan()))
        with self.assertRaises(PlanConflict):
            self.store.save_plan(plan("changed"))
        changed = self.store.save_plan(plan("changed"), expected_version=1)
        self.assertEqual(2, changed["version"])
        with self.assertRaises(PlanConflict):
            self.store.save_plan(plan("stale"), expected_version=1)
        self.assertEqual(changed, self.store.get_plan(saved["id"]))

    def test_unknown_cannot_be_overwritten_or_reset_by_import(self):
        saved = self.store.save_plan(plan(state="UNKNOWN"))
        self.store.import_jobs([{"job_id": "job1", "title": "updated"}])
        with self.assertRaises(PlanConflict):
            self.store.save_plan(plan("again"), expected_version=1)
        self.assertEqual("UNKNOWN", self.store.get_plan(saved["id"])["state"])

    def test_recipients_merge_without_duplicate_or_plan_reset(self):
        self.store.import_recipients([{"email": "hr@example.test", "name": "first"}])
        self.store.import_recipients([{"email": "hr@example.test", "name": "latest"}])
        self.assertEqual(1, len(self.store.recipients()))
        self.assertEqual("latest", self.store.recipients()[0]["name"])

    def test_payload_sql_text_is_stored_as_data(self):
        self.store.import_jobs([{"job_id": "job1", "title": "'; DROP TABLE jobs;--"}])
        self.assertEqual("'; DROP TABLE jobs;--", self.store.get_job("job1")["title"])
        self.assertEqual([], self.store.plans())

    def test_unknown_job_and_plan_have_explicit_error(self):
        with self.assertRaises(KeyError):
            self.store.get_job("missing")
        with self.assertRaises(KeyError):
            self.store.get_plan("missing")

    def test_expected_version_must_be_an_integer(self):
        self.store.save_plan(plan())
        for version in (True, 1.0, "1"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.store.save_plan(plan("changed"), expected_version=version)
