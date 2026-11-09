"""Real lease commit and gated response delivery through production shutdown.

Only synthetic input and a dedicated *_test database are used. The delivery
adapter adds gates to the existing broker; SQL, migrations and Worker lifecycle
remain real. Imported setup helpers do not collect the metadata suite again.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from personal_assistant_sdk import RuntimeContext
from personal_knowledge import EXTENSION_VERSION
from personal_knowledge.worker import PersonalKnowledgeExtension

from tests.integration import test_knowledge_meta_postgres as metadata


class DelayedDelivery:
    def __init__(self, client, *, before_commit: bool) -> None:
        self.client = client
        self.entered = asyncio.Event()
        self.allow_commit = asyncio.Event()
        self.committed = asyncio.Event()
        self.release_reply = asyncio.Event()
        self.host_tasks = []
        if not before_commit:
            self.allow_commit.set()

    async def execute(self, *args, **kwargs):
        return await self.client.execute(*args, **kwargs)

    async def migrate(self, *args, **kwargs):
        return await self.client.migrate(*args, **kwargs)

    async def transaction(self, statements, **kwargs):
        is_claim = any(
            "'expires_at'" in item["statement"]
            and "jsonb_build_object" in item["statement"]
            for item in statements
        )
        if not is_claim:
            return await self.client.transaction(statements, **kwargs)

        async def host_transaction():
            await self.allow_commit.wait()
            result = await self.client.transaction(statements, **kwargs)
            self.committed.set()
            return result

        # A request already delivered to the host is not cancelled by losing
        # its caller. Otherwise this fixture would hide a committed-claim leak.
        host = asyncio.create_task(host_transaction())
        self.host_tasks.append(host)
        self.entered.set()
        result = await asyncio.shield(host)
        await self.release_reply.wait()
        return result

    async def aclose(self):
        return None

    async def drain(self):
        self.allow_commit.set()
        self.release_reply.set()
        await asyncio.gather(*self.host_tasks, return_exceptions=True)


@unittest.skipUnless(metadata.BASE_URL, "set PA_TEST_DATABASE_URL to an isolated *_test endpoint")
class KnowledgeClaimCancellationPostgresTests(unittest.IsolatedAsyncioTestCase):
    # Reuse only fixture methods, without inheriting/collecting its test nodes.
    asyncSetUp = metadata.KnowledgeMetaPostgresTests.asyncSetUp
    _drop_database = metadata.KnowledgeMetaPostgresTests._drop_database

    async def _check_shutdown_during_claim(self, *, before_commit: bool) -> None:
        delivery = DelayedDelivery(self.client, before_commit=before_commit)
        extension = PersonalKnowledgeExtension()
        shutdown = None
        with tempfile.TemporaryDirectory(prefix="pa_claim_cancel_test_") as directory:
            root = Path(directory).resolve()
            (root / "note.md").write_text("Synthetic cancelled-before-scan input", encoding="utf-8")
            try:
                await extension.initialize(RuntimeContext(
                    "1", "personal.knowledge", EXTENSION_VERSION, metadata.NAMESPACE,
                    non_secret_config={
                        "roots": [{"key": "notes", "path": str(root)}],
                        "embedding": {"provider": "none"},
                    },
                    host_data=delivery,
                ))
                await asyncio.wait_for(delivery.entered.wait(), 10)
                if before_commit:
                    self.assertIsNone(await self.store.get_build_lease())
                else:
                    await asyncio.wait_for(delivery.committed.wait(), 10)
                    self.assertTrue((await self.store.get_build_lease())["active"])

                indexing = extension._indexing_task
                self.assertIsNotNone(indexing)
                shutdown = asyncio.create_task(extension.shutdown())
                async with asyncio.timeout(5):
                    while not indexing.cancelling():
                        await asyncio.sleep(0)
                # Repeated cancellation must neither cancel the pending host
                # transaction nor skip the eventual owner-CAS cleanup.
                for _ in range(2):
                    indexing.cancel()
                    await asyncio.sleep(0)
                await self.client.execute("SELECT 1 AS scheduled")
                ended_before_commit_or_reply = shutdown.done()

                delivery.allow_commit.set()
                await asyncio.wait_for(delivery.committed.wait(), 10)
                # A matching owner read-back may already prove commitment and
                # permit cleanup before the original response is delivered.
                # Only pre-commit shutdown completion is necessarily unsafe.
                delivery.release_reply.set()
                await asyncio.wait_for(shutdown, 10)

                lease = await self.store.get_build_lease()
                sources = await self.client.execute(
                    "SELECT count(*) AS total FROM knowledge_sources"
                )
                successor_claimed = await self.store.claim_build("successor", "next-binding", 60)
                if before_commit:
                    self.assertFalse(
                        ended_before_commit_or_reply, "shutdown skipped the pending claim"
                    )
                self.assertEqual("CANCELLED", lease["state"])
                self.assertFalse(lease["active"])
                self.assertTrue(
                    successor_claimed, "shutdown left a committed claim without cleanup"
                )
                self.assertEqual(0, sources["rows"][0]["total"])
                self.assertTrue(indexing.cancelled())
                self.assertIsNone(extension._indexing_task)
            finally:
                await delivery.drain()
                if shutdown is not None:
                    await asyncio.gather(shutdown, return_exceptions=True)
                await extension.shutdown()

    async def test_shutdown_waits_for_claim_commit_and_reply_before_cleanup(self) -> None:
        await self._check_shutdown_during_claim(before_commit=True)

    async def test_shutdown_cleans_committed_claim_after_delayed_reply(self) -> None:
        await self._check_shutdown_during_claim(before_commit=False)
