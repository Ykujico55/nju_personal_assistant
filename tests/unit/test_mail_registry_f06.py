"""F06 unit tests: host-owned file account registry strictness."""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path

from personal_assistant.core.mail import MailAccountNotFoundError, MailAccountRecord
from personal_assistant.infrastructure.mail.registry import (
    FileMailAccountRegistry,
    InMemoryMailAccountRegistry,
)


def _record(account_id: str = "nju", **overrides: object) -> MailAccountRecord:
    values: dict[str, object] = {
        "account_id": account_id,
        "address": f"{account_id}@smail.nju.edu.cn",
        "imap_host": "imap.example.test",
        "smtp_host": "smtp.example.test",
        "secret_handle_id": f"{account_id}-handle",
    }
    values.update(overrides)
    return MailAccountRecord(**values)  # type: ignore[arg-type]


class FileRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory(prefix="pa_f06_registry_")
        self.path = Path(self._dir.name) / "mail" / "accounts.json"
        self.registry = FileMailAccountRegistry(self.path)

    async def asyncTearDown(self) -> None:
        self._dir.cleanup()

    async def test_roundtrip_and_fingerprint_change_on_rebind(self) -> None:
        record = _record()
        await self.registry.upsert(record)
        resolved = await self.registry.resolve("nju")
        self.assertEqual(record, resolved)
        original = resolved.fingerprint()
        await self.registry.replace_all((_record(secret_handle_id="new-handle"),))
        rebound = await self.registry.resolve("nju")
        self.assertNotEqual(original, rebound.fingerprint())

    async def test_unknown_account_fails_closed(self) -> None:
        with self.assertRaises(MailAccountNotFoundError):
            await self.registry.resolve("missing")

    async def test_duplicate_ids_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            await self.registry.replace_all((_record(), _record()))

    async def test_non_boolean_flags_are_rejected(self) -> None:
        document = {
            "accounts": [
                {
                    "account_id": "nju",
                    "address": "nju@smail.nju.edu.cn",
                    "imap_host": "imap.example.test",
                    "smtp_host": "smtp.example.test",
                    "secret_handle_id": "nju-handle",
                    "read_enabled": 1,
                    "send_enabled": "false",
                }
            ]
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(ValueError):
            await self.registry.list()

    async def test_unknown_fields_are_rejected(self) -> None:
        document = {
            "accounts": [
                {
                    "account_id": "nju",
                    "address": "nju@smail.nju.edu.cn",
                    "imap_host": "imap.example.test",
                    "smtp_host": "smtp.example.test",
                    "secret_handle_id": "nju-handle",
                    "password": "hunter2",
                }
            ]
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(ValueError):
            await self.registry.list()

    async def test_invalid_json_and_shapes_fail_closed(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            await self.registry.list()
        self.path.write_text('{"accounts": "nope"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            await self.registry.list()

    async def test_delete_removes_only_the_target(self) -> None:
        await self.registry.replace_all((_record("a"), _record("b")))
        await self.registry.delete("a")
        remaining = await self.registry.list()
        self.assertEqual(("b",), tuple(item.account_id for item in remaining))

    async def test_memory_registry_matches_file_semantics(self) -> None:
        registry = InMemoryMailAccountRegistry((_record(),))
        self.assertEqual("nju", (await registry.resolve("nju")).account_id)
        with self.assertRaises(MailAccountNotFoundError):
            await registry.resolve("missing")


    async def test_admin_edit_bumps_generation_and_invalidates_lease(self) -> None:
        await self.registry.upsert(_record())
        lease = await self.registry.resolve("nju")
        self.assertEqual(0, lease.generation)
        self.assertTrue(await self.registry.verify(lease))
        # Any admin write bumps the generation, even for identical content.
        await self.registry.upsert(_record())
        self.assertFalse(await self.registry.verify(lease))
        current = await self.registry.resolve("nju")
        self.assertEqual(1, current.generation)
        self.assertTrue(await self.registry.verify(current))

    async def test_verify_fails_after_delete(self) -> None:
        await self.registry.upsert(_record())
        lease = await self.registry.resolve("nju")
        await self.registry.delete("nju")
        self.assertFalse(await self.registry.verify(lease))

    async def test_manual_file_with_duplicate_ids_is_rejected(self) -> None:
        document = {
            "accounts": [
                {
                    "account_id": "nju",
                    "address": "a@smail.nju.edu.cn",
                    "imap_host": "imap.example.test",
                    "smtp_host": "smtp.example.test",
                    "secret_handle_id": "h1",
                },
                {
                    "account_id": "nju",
                    "address": "b@smail.nju.edu.cn",
                    "imap_host": "attacker.example.test",
                    "smtp_host": "attacker.example.test",
                    "secret_handle_id": "h2",
                },
            ]
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(ValueError):
            await self.registry.list()


    async def test_delete_then_recreate_does_not_reuse_generation(self) -> None:
        await self.registry.upsert(_record())
        old_lease = await self.registry.resolve("nju")
        await self.registry.delete("nju")
        # ABA: recreating the same account id must not resurrect the old lease.
        await self.registry.upsert(_record())
        recreated = await self.registry.resolve("nju")
        self.assertGreater(recreated.generation, old_lease.generation)
        self.assertFalse(await self.registry.verify(old_lease))
        # The revision tombstone is durable across registry rebuilds.
        reopened = FileMailAccountRegistry(self.path)
        self.assertEqual(recreated.generation, (await reopened.resolve("nju")).generation)

    async def test_memory_registry_aba_is_monotonic(self) -> None:
        registry = InMemoryMailAccountRegistry((_record(),))
        old_lease = await registry.resolve("nju")
        await registry.delete("nju")
        await registry.upsert(_record())
        recreated = await registry.resolve("nju")
        self.assertGreater(recreated.generation, old_lease.generation)
        self.assertFalse(await registry.verify(old_lease))

    async def test_dispatch_guard_reads_live_and_fails_closed(self) -> None:
        await self.registry.upsert(_record())
        lease = await self.registry.resolve("nju")
        guard = self.registry.dispatch_guard(lease)
        self.assertTrue(guard.valid)
        self.assertIsNone(guard.reason)
        # A second registry instance on the same file (another process
        # incarnation) rebinds the account: the live guard sees it at once.
        other = FileMailAccountRegistry(self.path)
        await other.upsert(_record(secret_handle_id="other-handle"))
        self.assertFalse(guard.valid)
        self.assertEqual("ACCOUNT_CHANGED", guard.reason)
        # A missing or corrupt registry file also fails closed, never open.
        corrupt_path = Path(self._dir.name) / "other" / "accounts.json"
        corrupt_path.parent.mkdir(parents=True, exist_ok=True)
        corrupt_path.write_text("{", encoding="utf-8")
        corrupt = FileMailAccountRegistry(corrupt_path)
        corrupt_guard = corrupt.dispatch_guard(lease)
        self.assertFalse(corrupt_guard.valid)
        self.assertEqual("ACCOUNT_CHANGED", corrupt_guard.reason)

    async def test_memory_dispatch_guard_reads_live_state(self) -> None:
        registry = InMemoryMailAccountRegistry((_record(),))
        lease = await registry.resolve("nju")
        guard = registry.dispatch_guard(lease)
        self.assertTrue(guard.valid)
        await registry.delete("nju")
        self.assertFalse(guard.valid)
        self.assertEqual("ACCOUNT_CHANGED", guard.reason)

    async def test_file_registry_writes_run_off_the_event_loop(self) -> None:
        loop_thread = threading.get_ident()
        observed: list[int] = []
        original = self.registry._upsert_blocking

        def _spy(record: MailAccountRecord) -> None:
            observed.append(threading.get_ident())
            original(record)

        self.registry._upsert_blocking = _spy  # type: ignore[method-assign]
        await self.registry.upsert(_record())
        self.assertEqual(1, len(observed))
        self.assertNotEqual(loop_thread, observed[0])

    async def test_cancelled_write_waits_for_the_background_commit(self) -> None:
        from personal_assistant.infrastructure.mail.file_lock import ExclusiveFileLock

        await self.registry.upsert(_record(secret_handle_id="old"))
        lock_path = self.path.with_name(self.path.name + ".lock")
        holder = ExclusiveFileLock(lock_path)
        self.assertTrue(holder.acquire())
        observed: list[str] = []

        async def _write() -> None:
            try:
                await self.registry.upsert(_record(secret_handle_id="new"))
            finally:
                observed.append(
                    (await self.registry.resolve("nju")).secret_handle_id
                )

        task = asyncio.create_task(_write())
        await asyncio.sleep(0.2)
        task.cancel()
        await asyncio.sleep(0.2)
        # The caller must not observe cancellation while the background thread
        # still owns an in-flight (here: lock-blocked) commit.
        self.assertFalse(task.done())
        holder.release()
        with self.assertRaises(asyncio.CancelledError):
            await task
        # The commit finished before the cancellation surfaced: no write may
        # land after the caller has already seen the cancellation.
        self.assertEqual(["new"], observed)
        self.assertEqual("new", (await self.registry.resolve("nju")).secret_handle_id)


if __name__ == "__main__":

    unittest.main()
