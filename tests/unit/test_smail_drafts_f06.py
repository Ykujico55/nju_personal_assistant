"""F06 unit tests: versioned drafts, approval snapshot binding and reconciliation."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any

from nju_smail.drafts import DraftService
from nju_smail.models import MailConfigError, parse_settings
from nju_smail.send import SendService
from personal_assistant_sdk import ArtifactHandle, MailAccountInfo

CONFIG = {
    "accounts": [{"account_id": "nju", "display_name": "NJU"}],
    "folders": ["INBOX"],
}
FINGERPRINT = "f" * 64


class FakeArtifacts:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.counter = 0
        self.deleted: list[str] = []
        self.delete_started = asyncio.Event()
        self.delete_gate: asyncio.Event | None = None

    async def put(
        self, data: bytes, *, media_type: str, sensitivity: str = "PERSONAL"
    ) -> ArtifactHandle:
        self.counter += 1
        identifier = f"art_{self.counter:04d}"
        self.blobs[identifier] = data
        import hashlib

        return ArtifactHandle(
            id=identifier,
            content_hash=hashlib.sha256(data).hexdigest(),
            media_type=media_type,
            size_bytes=len(data),
        )

    async def read(self, artifact_id: str) -> bytes:
        return self.blobs[artifact_id]

    async def delete(self, artifact_id: str) -> None:
        self.delete_started.set()
        if self.delete_gate is not None:
            await self.delete_gate.wait()
        self.deleted.append(artifact_id)
        self.blobs.pop(artifact_id, None)

    async def aclose(self) -> None:
        return None


class FakeStore:
    def __init__(self, *, fail_after_insert: bool = False) -> None:
        self.drafts: dict[str, dict[str, Any]] = {}
        self.versions: dict[str, dict[int, dict[str, Any]]] = {}
        self.actions: dict[str, dict[str, Any]] = {}
        self.fail_after_insert = fail_after_insert
        self.prepared_artifacts: list[str] = []
        self.insert_gate: asyncio.Event | None = None
        self.insert_started = asyncio.Event()

    async def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        return self.drafts.get(draft_id)

    async def get_draft_version(
        self, draft_id: str, version: int
    ) -> dict[str, Any] | None:
        return self.versions.get(draft_id, {}).get(version)

    async def get_draft_version_by_request(
        self, draft_id: str, revision_request_id: str
    ) -> dict[str, Any] | None:
        for row in self.versions.get(draft_id, {}).values():
            if row["revision_request_id"] == revision_request_id:
                return row
        return None

    async def insert_draft_version(self, **kwargs: Any) -> int:
        if self.insert_gate is not None:
            self.insert_started.set()
            await self.insert_gate.wait()
        draft_id = kwargs["draft_id"]
        current = self.drafts.get(draft_id, {}).get("current_version", 0)
        if kwargs.get("expected_current", current) != current:
            raise RuntimeError("SMAL_DRAFT_CONFLICT")
        versions = self.versions.setdefault(draft_id, {})
        version = max(versions, default=0) + 1
        versions[version] = {
            "draft_id": draft_id,
            "version": version,
            "account_id": kwargs["account_id"],
            "account_fingerprint": kwargs["account_fingerprint"],
            "from_address": kwargs["from_address"],
            "to_json": list(kwargs["to"]),
            "cc_json": list(kwargs["cc"]),
            "bcc_json": list(kwargs["bcc"]),
            "subject": kwargs["subject"],
            "body_text": kwargs["body_text"],
            "body_html": kwargs["body_html"],
            "attachment_manifest": list(kwargs["attachment_manifest"]),
            "canonical_digest": kwargs["canonical_digest"],
            "mime_sha256": kwargs["mime_sha256"],
            "mime_artifact_id": kwargs["mime_artifact_id"],
            "local_action_id": kwargs["local_action_id"],
            "revision_request_id": kwargs["revision_request_id"],
            "message_id": kwargs["message_id"],
            "in_reply_to": kwargs["in_reply_to"],
            "refs_json": list(kwargs["references"]),
            "thread_id": kwargs["thread_id"],
        }
        self.drafts[draft_id] = {
            "draft_id": draft_id,
            "account_id": kwargs["account_id"],
            "current_version": version,
            "thread_id": kwargs["thread_id"],
        }
        if self.fail_after_insert:
            raise RuntimeError("simulated database failure after insert")
        return version

    async def get_action(self, local_action_id: str) -> dict[str, Any] | None:
        return self.actions.get(local_action_id)

    async def prepare_action(
        self,
        *,
        local_action_id: str,
        account_id: str,
        draft_id: str,
        draft_version: int,
        message_id: str,
        envelope_digest: str,
    ) -> dict[str, Any]:
        existing = self.actions.get(local_action_id)
        if existing is None:
            existing = {
                "local_action_id": local_action_id,
                "account_id": account_id,
                "draft_id": draft_id,
                "draft_version": draft_version,
                "message_id": message_id,
                "envelope_digest": envelope_digest,
                "state": "PREPARED",
                "receipt": None,
                "recipient_results": [],
            }
            self.actions[local_action_id] = existing
        if existing["envelope_digest"] != envelope_digest:
            raise ValueError("SMAL_IDEMPOTENCY_CONFLICT")
        return existing

    async def project_status(
        self,
        *,
        local_action_id: str,
        state: str,
        receipt: Any,
        recipient_results: list[Any],
    ) -> dict[str, Any] | None:
        action = self.actions[local_action_id]
        if action["state"] in {"PREPARED", "UNKNOWN"} and action["state"] != state:
            action["state"] = state
            action["receipt"] = receipt
            action["recipient_results"] = recipient_results
        return action


class FakeHostMail:
    def __init__(self, status: str = "NOT_FOUND", fingerprint: str = FINGERPRINT) -> None:
        self.status = status
        self.fingerprint = fingerprint
        self.calls = 0
        self.account_calls = 0
        self.ledger_status = "PREPARED"
        self.ledger_calls = 0

    async def account(self, account_id: str) -> MailAccountInfo:
        self.account_calls += 1
        if account_id != "nju":
            raise ValueError("unknown account")
        return MailAccountInfo(
            account_id="nju",
            address="student@smail.nju.edu.cn",
            display_name="NJU",
            read_enabled=True,
            send_enabled=True,
            fingerprint=self.fingerprint,
        )

    async def delivery_status(self, account_id: str, **kwargs: Any) -> dict[str, Any]:
        del account_id
        self.ledger_calls += 1
        return {
            "local_action_id": kwargs.get("local_action_id", ""),
            "message_id": "",
            "status": self.ledger_status,
            "recipient_results": [],
            "server_code": None,
            "diagnostic_code": None,
        }

    async def reconcile_sent(self, account_id: str, **kwargs: Any) -> dict[str, Any]:
        del account_id, kwargs
        self.calls += 1
        return {"status": self.status, "matches": [], "diagnostic_code": None}


def send_arguments(prepared: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_id": prepared["account_id"],
        "account_fingerprint": prepared["account_fingerprint"],
        "draft_id": prepared["draft_id"],
        "draft_version": prepared["version"],
        "canonical_digest": prepared["canonical_digest"],
        "local_action_id": prepared["local_action_id"],
        "message_id": prepared["message_id"],
        "from_address": prepared["from_address"],
        "to": list(prepared["to"]),
        "cc": list(prepared["cc"]),
        "bcc": list(prepared["bcc"]),
        "subject": prepared["subject"],
        "mime_sha256": prepared["mime_sha256"],
        "attachment_hashes": list(prepared["attachment_hashes"]),
    }


class DraftVersionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.settings = parse_settings(CONFIG)
        self.store = FakeStore()
        self.artifacts = FakeArtifacts()
        self.host_mail = FakeHostMail()
        self.drafts = DraftService(
            self.store, self.settings, self.artifacts, self.host_mail  # type: ignore[arg-type]
        )
        self.send = SendService(self.store, self.settings, self.host_mail)  # type: ignore[arg-type]

    async def _prepare(
        self,
        *,
        body: str = "Body",
        draft_id: str | None = None,
        request_id: str | None = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "account_id": "nju",
            "to": ["friend@example.test"],
            "subject": "Hello",
            "body_text": body,
        }
        if draft_id is not None:
            arguments["draft_id"] = draft_id
        if thread_id is not None:
            arguments["thread_id"] = thread_id
        return await self.drafts.prepare(arguments, request_id)

    async def test_every_field_change_produces_a_new_version_and_digest(self) -> None:
        first = await self._prepare(body="one")
        second = await self._prepare(body="two", draft_id=first["draft_id"])
        self.assertEqual(1, first["version"])
        self.assertEqual(2, second["version"])
        self.assertNotEqual(first["canonical_digest"], second["canonical_digest"])
        self.assertNotEqual(first["local_action_id"], second["local_action_id"])
        self.assertNotEqual(first["message_id"], second["message_id"])
        self.assertNotEqual(first["mime_sha256"], second["mime_sha256"])
        self.assertEqual(FINGERPRINT, first["account_fingerprint"])
        self.assertEqual("student@smail.nju.edu.cn", second["from_address"])

    async def test_same_request_replay_returns_the_original_version(self) -> None:
        first = await self._prepare(body="same", request_id="edit-request-1")
        artifacts_before = len(self.artifacts.blobs)
        replay = await self._prepare(
            body="same", draft_id=first["draft_id"], request_id="edit-request-1"
        )
        self.assertEqual(first["version"], replay["version"])
        self.assertEqual(first["local_action_id"], replay["local_action_id"])
        self.assertEqual(first["mime_artifact_id"], replay["mime_artifact_id"])
        self.assertEqual(artifacts_before, len(self.artifacts.blobs))

    async def test_same_request_with_different_content_conflicts(self) -> None:
        first = await self._prepare(body="one", request_id="edit-request-2")
        with self.assertRaises(MailConfigError) as captured:
            await self._prepare(
                body="two", draft_id=first["draft_id"], request_id="edit-request-2"
            )
        self.assertEqual("SMAL_IDEMPOTENCY_CONFLICT", captured.exception.code)
        self.assertEqual(1, len(self.store.versions[first["draft_id"]]))

    async def test_identical_content_re_edit_is_still_a_new_version_and_action(self) -> None:
        first = await self._prepare(body="same")
        second = await self._prepare(body="same", draft_id=first["draft_id"])
        self.assertEqual(1, first["version"])
        self.assertEqual(2, second["version"])
        self.assertEqual(first["canonical_digest"], second["canonical_digest"])
        self.assertNotEqual(first["local_action_id"], second["local_action_id"])
        self.assertNotEqual(first["message_id"], second["message_id"])

    async def test_revision_a_b_a_still_allocates_fresh_actions(self) -> None:
        first = await self._prepare(body="A")
        second = await self._prepare(body="B", draft_id=first["draft_id"])
        third = await self._prepare(body="A", draft_id=first["draft_id"])
        self.assertEqual(3, third["version"])
        action_ids = {
            first["local_action_id"],
            second["local_action_id"],
            third["local_action_id"],
        }
        self.assertEqual(3, len(action_ids))
        message_ids = {first["message_id"], second["message_id"], third["message_id"]}
        self.assertEqual(3, len(message_ids))

    async def test_failed_insert_removes_the_new_artifact(self) -> None:
        self.store.fail_after_insert = True
        before = len(self.artifacts.blobs)
        with self.assertRaises(RuntimeError):
            await self._prepare()
        self.assertEqual(before, len(self.artifacts.blobs))

    async def test_materialize_returns_exact_current_version(self) -> None:
        prepared = await self._prepare()
        output = await self.send.materialize(send_arguments(prepared))
        self.assertEqual(prepared["mime_artifact_id"], output["mime_artifact_id"])
        self.assertEqual(prepared["mime_sha256"], output["mime_sha256"])
        self.assertEqual(["friend@example.test"], output["to"])
        self.assertEqual(prepared["account_fingerprint"], output["account_fingerprint"])

    async def test_edited_draft_makes_the_old_version_unusable(self) -> None:
        prepared = await self._prepare(body="one")
        await self._prepare(body="two", draft_id=prepared["draft_id"])
        with self.assertRaises(MailConfigError) as captured:
            await self.send.materialize(send_arguments(prepared))
        self.assertEqual("SMAL_DRAFT_CHANGED", captured.exception.code)

    async def test_changed_account_fingerprint_is_rejected(self) -> None:
        prepared = await self._prepare()
        arguments = send_arguments(prepared)
        arguments["account_fingerprint"] = "0" * 64
        with self.assertRaises(MailConfigError) as captured:
            await self.send.materialize(arguments)
        self.assertEqual("SMAL_ACCOUNT_CHANGED", captured.exception.code)
        self.host_mail.fingerprint = "e" * 64
        with self.assertRaises(MailConfigError):
            await self.send.materialize(send_arguments(prepared))

    async def test_tampered_snapshot_values_are_rejected(self) -> None:
        prepared = await self._prepare()
        for field, value in (
            ("canonical_digest", "0" * 64),
            ("mime_sha256", "1" * 64),
            ("subject", "Changed"),
            ("to", ["attacker@example.test"]),
        ):
            arguments = send_arguments(prepared)
            arguments[field] = value
            with self.assertRaises(MailConfigError, msg=field):
                await self.send.materialize(arguments)

    async def test_attachment_hash_mismatch_is_rejected(self) -> None:
        handle = await self.artifacts.put(b"payload", media_type="text/plain")
        with self.assertRaises(MailConfigError) as captured:
            await self.drafts.prepare(
                {
                    "account_id": "nju",
                    "to": ["friend@example.test"],
                    "subject": "With file",
                    "body_text": "see attached",
                    "attachments": [
                        {
                            "filename": "../../note.txt",
                            "media_type": "text/plain",
                            "sha256": "0" * 64,
                            "artifact_id": handle.id,
                        }
                    ],
                }
            )
        self.assertEqual("SMAL_ATTACHMENT_MISMATCH", captured.exception.code)

    async def test_projection_mirrors_host_status_and_first_terminal_wins(self) -> None:
        prepared = await self._prepare()
        await self.send.materialize(send_arguments(prepared))
        self.host_mail.ledger_status = "PARTIAL"
        first = await self.send.sync_status(
            {"account_id": "nju", "local_action_id": prepared["local_action_id"]}
        )
        self.assertEqual("PARTIAL", first["state"])
        self.assertEqual("PARTIAL", first["authoritative_status"])
        self.assertTrue(first["found"])
        # A later host read cannot overwrite the first terminal projection.
        self.host_mail.ledger_status = "SUCCEEDED"
        replay = await self.send.sync_status(
            {"account_id": "nju", "local_action_id": prepared["local_action_id"]}
        )
        self.assertEqual("PARTIAL", replay["state"])
        self.assertEqual(2, self.host_mail.ledger_calls)

    async def test_not_found_reconciliation_keeps_unknown_and_never_resends(self) -> None:
        prepared = await self._prepare()
        arguments = send_arguments(prepared)
        await self.send.materialize(arguments)
        self.host_mail.ledger_status = "UNKNOWN"
        await self.send.sync_status(
            {"account_id": "nju", "local_action_id": prepared["local_action_id"]}
        )
        fake_mail = FakeHostMail("NOT_FOUND")
        service = SendService(self.store, self.settings, fake_mail)  # type: ignore[arg-type]
        result = await service.reconcile(
            {"account_id": "nju", "local_action_id": prepared["local_action_id"]}
        )
        self.assertEqual("NOT_FOUND", result["reconciliation"])
        self.assertEqual("UNKNOWN", result["state"])
        self.assertEqual(1, fake_mail.calls)

    async def test_matched_reconciliation_converges_to_confirmed(self) -> None:
        prepared = await self._prepare()
        await self.send.materialize(send_arguments(prepared))
        fake_mail = FakeHostMail("MATCHED")
        service = SendService(self.store, self.settings, fake_mail)  # type: ignore[arg-type]
        result = await service.reconcile(
            {"account_id": "nju", "local_action_id": prepared["local_action_id"]}
        )
        self.assertEqual("SENT_CONFIRMED", result["state"])




    async def test_same_request_same_body_different_thread_conflicts(self) -> None:
        first = await self._prepare(
            body="same", request_id="edit-request-thread", thread_id="thread-a"
        )
        with self.assertRaises(MailConfigError) as captured:
            await self._prepare(
                body="same",
                draft_id=first["draft_id"],
                request_id="edit-request-thread",
                thread_id="thread-b",
            )
        self.assertEqual("SMAL_IDEMPOTENCY_CONFLICT", captured.exception.code)

    async def test_repeated_cancellation_still_deletes_the_artifact(self) -> None:
        self.store.insert_gate = asyncio.Event()
        self.artifacts.delete_gate = asyncio.Event()
        task = asyncio.create_task(self._prepare())
        await self.store.insert_started.wait()
        task.cancel()
        await self.artifacts.delete_started.wait()
        task.cancel()
        await asyncio.sleep(0.05)
        self.artifacts.delete_gate.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(1, len(self.artifacts.deleted))
        self.assertEqual(0, len(self.artifacts.blobs))


    async def test_host_non_terminal_status_is_reported_without_projection(self) -> None:
        prepared = await self._prepare()
        await self.send.materialize(send_arguments(prepared))
        for host_status in ("PREPARED", "EXECUTING"):
            self.host_mail.ledger_status = host_status
            result = await self.send.sync_status(
                {"account_id": "nju", "local_action_id": prepared["local_action_id"]}
            )
            self.assertEqual(host_status, result["authoritative_status"])
            self.assertEqual("PREPARED", result["state"])
            self.assertTrue(result["found"])


if __name__ == "__main__":

    unittest.main()
