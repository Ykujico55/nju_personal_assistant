from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from personal_assistant.domain import ValidationError


class RuleHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_scoped_metadata_git_history_and_rollback_are_reviewed_candidates(self):
        from personal_assistant.core.governance import RuleService
        from personal_assistant.infrastructure.memory.records import InMemoryRecordStore
        from personal_assistant.infrastructure.operations.rules import GitRuleHistory

        with tempfile.TemporaryDirectory() as directory:
            history = GitRuleHistory(Path(directory) / "rules-history")
            rules = RuleService(InMemoryRecordStore(), history=history)
            candidate = await rules.propose(
                scope={"category": "general"}, preferences={"language": "zh"}, key="c"
            )
            first = await history.record(candidate)
            active = await rules.activate(
                candidate["id"],
                version=1,
                tests=[
                    {"metadata": {"category": "general"}, "matches": True},
                    {"metadata": {}, "matches": False},
                ],
                key="activate",
            )
            self.assertEqual("ACTIVE", active["value"]["state"])
            restored = await rules.rollback(
                candidate["id"], revision=first, version=2, key="rollback"
            )
            self.assertEqual("CANDIDATE", restored["value"]["state"])
            self.assertEqual((), await rules.active())
            self.assertGreaterEqual(len(await history.versions(candidate["id"])), 2)
            with self.assertRaises(ValueError):
                await history.read("--help", candidate["id"])
            with self.assertRaises(ValidationError):
                await history.record(
                    {
                        "id": "r",
                        "version": 1,
                        "value": {
                            "scope": {"category": "general"},
                            "preferences": {"api_key": "synthetic"},
                        },
                    }
                )
