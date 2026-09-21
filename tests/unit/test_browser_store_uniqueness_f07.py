"""F07 audit: driver write-allowance matching and store-level session uniqueness."""

from __future__ import annotations

import asyncio
import itertools
import unittest
from datetime import UTC

from personal_assistant.core.browser import BrowserSessionBroker, BrowserSessionState
from personal_assistant.domain.errors import AlreadyExistsError
from personal_assistant.infrastructure.browser.driver import _allowance_matches, _origin_of
from personal_assistant.infrastructure.memory.browser import (
    InMemoryBrowserAdapterStore,
    InMemoryBrowserSessionStore,
)
from tests.support.browser import TEST_ORIGIN, standard_adapter, standard_companion


class AllowanceMatchingTests(unittest.TestCase):
    def test_exact_target_matches(self) -> None:
        allowance = ("POST", TEST_ORIGIN, "/apps/proof/submit", 1)
        self.assertTrue(
            _allowance_matches(allowance, "POST", TEST_ORIGIN + "/apps/proof/submit")
        )

    def test_query_string_on_the_same_path_never_matches(self) -> None:
        allowance = ("POST", TEST_ORIGIN, "/apps/proof/submit", 1)
        self.assertFalse(
            _allowance_matches(
                allowance, "POST", TEST_ORIGIN + "/apps/proof/submit?trace=1"
            )
        )
        self.assertFalse(
            _allowance_matches(
                allowance,
                "POST",
                TEST_ORIGIN + "/apps/proof/submit?operation=withdraw",
            )
        )

    def test_other_method_never_matches(self) -> None:
        allowance = ("POST", TEST_ORIGIN, "/apps/proof/submit", 1)
        self.assertFalse(
            _allowance_matches(allowance, "GET", TEST_ORIGIN + "/apps/proof/submit")
        )

    def test_other_path_never_matches(self) -> None:
        allowance = ("POST", TEST_ORIGIN, "/apps/proof/submit", 1)
        self.assertFalse(
            _allowance_matches(allowance, "POST", TEST_ORIGIN + "/apps/proof/other")
        )
        self.assertFalse(
            _allowance_matches(allowance, "POST", TEST_ORIGIN + "/apps/proof/submit/extra")
        )

    def test_other_origin_never_matches(self) -> None:
        allowance = ("POST", TEST_ORIGIN, "/apps/proof/submit", 1)
        self.assertFalse(
            _allowance_matches(
                allowance, "POST", "https://evil.example.com/apps/proof/submit"
            )
        )

    def test_consumed_allowance_never_matches_again(self) -> None:
        allowance = ("POST", TEST_ORIGIN, "/apps/proof/submit", 0)
        self.assertFalse(
            _allowance_matches(allowance, "POST", TEST_ORIGIN + "/apps/proof/submit")
        )

    def test_origin_of_normalizes_ports(self) -> None:
        self.assertEqual(_origin_of("https://ehall.test.example/a/b"), TEST_ORIGIN)
        self.assertEqual(
            _origin_of("https://ehall.test.example:443/a"), TEST_ORIGIN
        )
        self.assertEqual(
            _origin_of("https://ehall.test.example:8443/a"),
            "https://ehall.test.example:8443",
        )


class LoginTargetMatchingTests(unittest.TestCase):
    """Only the frozen authentication target may be released once."""

    def test_exact_frozen_target_matches(self) -> None:
        from personal_assistant.infrastructure.browser.driver import (
            _navigation_target_matches,
        )

        allowance = ("POST", TEST_ORIGIN, "/sso/login", 1)
        self.assertTrue(
            _navigation_target_matches(allowance, "POST", TEST_ORIGIN + "/sso/login")
        )

    def test_other_paths_and_methods_never_match(self) -> None:
        from personal_assistant.infrastructure.browser.driver import (
            _navigation_target_matches,
        )

        allowance = ("POST", TEST_ORIGIN, "/sso/login", 1)
        self.assertFalse(
            _navigation_target_matches(allowance, "POST", TEST_ORIGIN + "/apps/withdraw")
        )
        self.assertFalse(
            _navigation_target_matches(allowance, "GET", TEST_ORIGIN + "/sso/login")
        )
        self.assertFalse(
            _navigation_target_matches(
                allowance, "POST", "https://evil.example.com/sso/login"
            )
        )

    def test_consumed_login_allowance_never_matches_again(self) -> None:
        from personal_assistant.infrastructure.browser.driver import (
            _navigation_target_matches,
        )

        allowance = ("POST", TEST_ORIGIN, "/sso/login", 0)
        self.assertFalse(
            _navigation_target_matches(allowance, "POST", TEST_ORIGIN + "/sso/login")
        )


class MemoryStoreUniquenessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._ids = itertools.count(1)
        self.store = InMemoryBrowserSessionStore()
        self.adapters = InMemoryBrowserAdapterStore()
        self.broker = BrowserSessionBroker(
            companion=standard_companion(),
            sessions=self.store,
            adapters=self.adapters,
            allowed_origins=frozenset({TEST_ORIGIN}),
            submit_enabled=False,
            id_factory=lambda: next(self._ids),
            nonce_factory=lambda: "nonce-fixed",
        )
        await self.broker.register_adapter(standard_adapter())

    async def test_concurrent_session_creation_yields_one_session(self) -> None:
        results = await asyncio.gather(
            *[
                self.broker.create_session(
                    task_id="task-race",
                    extension_id="nju.ehall",
                    extension_version="0.1.0",
                    purpose="办理在读证明",
                )
                for _ in range(5)
            ]
        )
        self.assertEqual(len({record.session_id for record in results}), 1)
        records = await self.store.find_active_for_task("task-race")
        self.assertEqual(len(records), 1)

    async def test_store_rejects_a_second_open_session_for_the_same_task(self) -> None:
        first = await self.broker.create_session(
            task_id="task-open",
            extension_id="nju.ehall",
            extension_version="0.1.0",
            purpose="办理在读证明",
        )
        from dataclasses import replace

        duplicate = replace(first, session_id="brs_other")
        with self.assertRaises(AlreadyExistsError):
            await self.store.create(duplicate)

    async def test_unknown_pending_sessions_keep_the_task_slot(self) -> None:
        first = await self.broker.create_session(
            task_id="task-pending",
            extension_id="nju.ehall",
            extension_version="0.1.0",
            purpose="办理在读证明",
        )
        record = await self.store.get(first.session_id)
        assert record is not None
        from dataclasses import replace
        from datetime import timedelta

        stale = replace(
            record,
            state=BrowserSessionState.UNKNOWN,
            expires_at=record.expires_at - timedelta(hours=2),
        )
        await self.store.save(stale, expected_version=record.version)
        reloaded = await self.broker.get_session(first.session_id, extension_id="nju.ehall")
        self.assertEqual(reloaded.state, BrowserSessionState.UNKNOWN)
        again = await self.broker.create_session(
            task_id="task-pending",
            extension_id="nju.ehall",
            extension_version="0.1.0",
            purpose="办理在读证明",
        )
        self.assertEqual(again.session_id, first.session_id)

    async def test_terminal_sessions_release_the_task_slot(self) -> None:
        first = await self.broker.create_session(
            task_id="task-release",
            extension_id="nju.ehall",
            extension_version="0.1.0",
            purpose="办理在读证明",
        )
        await self.broker.cancel_session(first.session_id, extension_id="nju.ehall")
        second = await self.broker.create_session(
            task_id="task-release",
            extension_id="nju.ehall",
            extension_version="0.1.0",
            purpose="办理在读证明",
        )
        self.assertNotEqual(first.session_id, second.session_id)
        record = await self.store.get(second.session_id)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.state, BrowserSessionState.REQUESTED)
        self.assertEqual(record.created_at.tzinfo, UTC)


if __name__ == "__main__":
    unittest.main()
