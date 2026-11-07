from __future__ import annotations

import base64
import hashlib
import unittest
from dataclasses import replace
from email import policy
from email.parser import BytesParser

from personal_assistant_sdk import ArtifactHandle, MailAccountInfo

from career_apply.mail import CareerMail, MailPlanError


class Artifacts:
    def __init__(self):
        self.data = {}

    async def put(self, data, *, media_type, sensitivity="PERSONAL"):
        digest = hashlib.sha256(data).hexdigest()
        self.data[digest] = data
        return ArtifactHandle(digest, digest, media_type, len(data))

    async def read(self, identifier):
        return self.data[identifier]


class Mail:
    def __init__(self):
        self.info = MailAccountInfo(
            "fixture", "sender@example.test", "Fixture", True, True, "a" * 64
        )

    async def account(self, account_id):
        if account_id != self.info.account_id:
            raise ValueError("unknown")
        return self.info


class CareerMailTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.artifacts = Artifacts()
        self.mail = Mail()
        self.service = CareerMail(self.mail, self.artifacts, "fixture")
        self.resume = await self.service.import_resume(
            {
                "filename": "resume.pdf",
                "media_type": "application/pdf",
                "data_base64": base64.b64encode(b"%PDF-1.4\nfixture\n%%EOF").decode(),
            }
        )
        self.request = {
            "to": "recipient@example.test",
            "subject": "Synthetic application",
            "body": "Public test body",
            "resume": self.resume,
            "prepared_at": "2026-10-30T01:00:00+00:00",
        }

    async def test_exact_resume_and_body_survive_materialization(self):
        args = await self.service.prepare(self.request, request_id="key", task_id="task")
        shown = await self.service.preview(args)
        self.assertEqual(args, shown)
        message = BytesParser(policy=policy.default).parsebytes(
            await self.artifacts.read(args["mime_artifact_id"])
        )
        self.assertEqual("recipient@example.test", message["To"])
        self.assertEqual("Public test body", message.get_body().get_content().strip())
        attachments = list(message.iter_attachments())
        self.assertEqual(1, len(attachments))
        self.assertEqual(
            self.resume["sha256"],
            hashlib.sha256(attachments[0].get_payload(decode=True)).hexdigest(),
        )

    async def test_same_key_and_materials_have_identical_action_and_mime(self):
        first = await self.service.prepare(self.request, request_id="key", task_id="task")
        again = await self.service.prepare(self.request, request_id="key", task_id="task")
        self.assertEqual(first, again)
        changed = await self.service.prepare(
            {**self.request, "body": "Changed"}, request_id="key", task_id="task"
        )
        self.assertNotEqual(first["local_action_id"], changed["local_action_id"])

    async def test_changed_account_and_changed_bytes_reject_preview(self):
        args = await self.service.prepare(self.request, request_id="key", task_id="task")
        self.mail.info = replace(self.mail.info, fingerprint="b" * 64)
        with self.assertRaises(MailPlanError):
            await self.service.preview(args)
        self.mail.info = replace(self.mail.info, fingerprint="a" * 64)
        self.artifacts.data[args["mime_artifact_id"]] = b"changed"
        with self.assertRaises(MailPlanError):
            await self.service.preview(args)

    async def test_changed_envelope_rejects_even_if_mime_hash_is_same(self):
        args = await self.service.prepare(self.request, request_id="key", task_id="task")
        for change in (
            {"to": ["other@example.test"]},
            {"subject": "changed"},
            {"attachment_hashes": []},
            {"local_action_id": "other"},
        ):
            with self.subTest(change=change), self.assertRaises(MailPlanError):
                await self.service.preview({**args, **change})

    async def test_invalid_header_or_false_attachment_metadata_is_rejected(self):
        for change in (
            {"subject": "bad\r\nBcc: other@example.test"},
            {"resume": {**self.resume, "sha256": "b" * 64}},
            {"prepared_at": "not a date"},
        ):
            with self.subTest(change=change), self.assertRaises(MailPlanError):
                await self.service.prepare(
                    {**self.request, **change}, request_id="key", task_id="task"
                )

    async def test_import_rejects_non_pdf_oversize_and_invalid_base64(self):
        for encoded in (
            "not base64",
            base64.b64encode(b"not a pdf").decode(),
            base64.b64encode(b"x" * (512 * 1024 + 1)).decode(),
        ):
            with self.subTest(length=len(encoded)), self.assertRaises(MailPlanError):
                await self.service.import_resume(
                    {
                        "filename": "resume.pdf",
                        "media_type": "application/pdf",
                        "data_base64": encoded,
                    }
                )
