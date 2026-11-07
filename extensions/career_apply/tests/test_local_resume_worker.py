"""Synthetic worker-level integration for local resume snapshots; never contacts BOSS/SMTP."""

from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import tomllib
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import AsyncMock

from personal_assistant_sdk import (
    ArtifactHandle,
    InvocationContext,
    MailAccountInfo,
    Outcome,
    RiskLevel,
    RuntimeContext,
)

from career_apply.worker import CareerApplyExtension


class FixtureArtifacts:
    """Byte-preserving fixture only; production persistence belongs to the host."""

    def __init__(self):
        self.data = {}
        self.put_calls = []

    async def put(self, data, *, media_type, sensitivity="PERSONAL"):
        digest = hashlib.sha256(data).hexdigest()
        self.data[digest] = data
        self.put_calls.append((data, media_type, sensitivity))
        return ArtifactHandle(digest, digest, media_type, len(data))

    async def read(self, identifier):
        return self.data[identifier]


class FixtureMail:
    async def account(self, account_id):
        if account_id != "fixture-mail":
            raise ValueError("unknown account")
        return MailAccountInfo(
            "fixture-mail", "sender@example.test", "Fixture", True, True, "a" * 64
        )


class LocalResumeWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.resume_root = self.root / "authorized"
        self.resume_root.mkdir()
        self.resume_path = self.resume_root / "public-resume.pdf"
        self.resume_bytes = b"%PDF-1.4\nPUBLIC_LOCAL_RESUME_FIXTURE\n%%EOF"
        self.resume_path.write_bytes(self.resume_bytes)
        self.resume_hash = hashlib.sha256(self.resume_bytes).hexdigest()
        self.artifacts = FixtureArtifacts()
        self.context = InvocationContext("fixture-task", "run", "2030-01-01T00:00:00Z", "key")
        self.worker = await self.make_worker()
        self.addAsyncCleanup(self.worker.shutdown)

    async def make_worker(self, *, config=None, artifacts=True, mail=False):
        worker = CareerApplyExtension()
        await worker.initialize(
            RuntimeContext(
                "1",
                "career.apply",
                "0.2.0",
                "ext_career",
                non_secret_config=(
                    {
                        "resume_roots": [{"key": "resumes", "path": str(self.resume_root)}],
                        **({"account_id": "fixture-mail"} if mail else {}),
                    }
                    if config is None
                    else config
                ),
                host_artifact=self.artifacts if artifacts else None,
                host_mail=FixtureMail() if mail else None,
            )
        )
        return worker

    def import_arguments(self):
        return {
            "root_key": "resumes",
            "relative_path": "public-resume.pdf",
            "expected_sha256": self.resume_hash,
        }

    def assert_no_material_leak(self, value):
        def strings(item):
            if isinstance(item, dict):
                for key, child in item.items():
                    yield str(key)
                    yield from strings(child)
            elif isinstance(item, (list, tuple)):
                for child in item:
                    yield from strings(child)
            elif isinstance(item, str):
                yield item

        for text in strings(value):
            normalized = text.replace(chr(92), "/").casefold()
            self.assertNotIn(str(self.root).replace(chr(92), "/").casefold(), normalized)
            self.assertNotIn("PUBLIC_LOCAL_RESUME_FIXTURE", text)
            self.assertNotIn(base64.b64encode(self.resume_bytes).decode(), text)
            self.assertNotIn("data_base64", text)

    async def test_lists_local_resume_without_email_or_artifact_configuration(self):
        worker = await self.make_worker(artifacts=False)
        self.addAsyncCleanup(worker.shutdown)
        response = await worker.invoke("career.resume.list", {}, self.context)
        self.assertEqual(Outcome.SUCCEEDED, response.outcome)
        self.assertFalse(response.output["truncated"])
        self.assertEqual(
            [
                {
                    "root_key": "resumes",
                    "relative_path": "public-resume.pdf",
                    "filename": "public-resume.pdf",
                    "media_type": "application/pdf",
                    "size_bytes": len(self.resume_bytes),
                    "sha256": self.resume_hash,
                }
            ],
            response.output["files"],
        )
        self.assert_no_material_leak(response.output)

    async def test_imports_exact_local_bytes_to_owned_artifact_without_mail(self):
        response = await self.worker.invoke(
            "career.resume.import_file", self.import_arguments(), self.context
        )
        self.assertEqual(Outcome.SUCCEEDED, response.outcome)
        ref = response.output
        self.assertEqual(self.resume_hash, ref["sha256"])
        self.assertEqual("public-resume.pdf", ref["filename"])
        self.assertEqual(len(self.resume_bytes), ref["size_bytes"])
        self.assertEqual(self.resume_bytes, await self.artifacts.read(ref["artifact_id"]))
        self.assertEqual(
            [(self.resume_bytes, "application/pdf", "PERSONAL")], self.artifacts.put_calls
        )
        self.assertEqual(self.resume_bytes, self.resume_path.read_bytes())
        self.assert_no_material_leak(ref)

    async def test_imported_reference_enters_boss_prepare_without_local_path_or_bytes(self):
        imported = await self.worker.invoke(
            "career.resume.import_file", self.import_arguments(), self.context
        )
        self.assertEqual(Outcome.SUCCEEDED, imported.outcome)
        browser = AsyncMock()
        browser.prepare.return_value = {"local_action_id": "fixture"}
        self.worker._boss = browser
        job = {
            "url": "https://www.zhipin.com/job_detail/fixture.html",
            "job_id": "fixture",
            "company": "Fixture",
            "recruiter": "Fixture HR",
            "title": "Developer",
        }
        prepared = await self.worker.invoke(
            "career.boss.prepare",
            {"job": job, "action_kind": "send_resume", "resume": dict(imported.output)},
            self.context,
        )
        self.assertEqual(Outcome.SUCCEEDED, prepared.outcome)
        self.assertTrue(prepared.output["approval_required"])
        browser.prepare.assert_awaited_once_with(
            job, "send_resume", message="", resume_label="", resume=dict(imported.output)
        )
        browser.execute.assert_not_awaited()
        self.assert_no_material_leak(prepared.output)

    async def test_imported_snapshot_mime_retains_reviewed_bytes_after_source_changes(self):
        worker = await self.make_worker(mail=True)
        self.addAsyncCleanup(worker.shutdown)
        imported = await worker.invoke(
            "career.resume.import_file", self.import_arguments(), self.context
        )
        self.assertEqual(Outcome.SUCCEEDED, imported.outcome)
        changed = b"%PDF-1.4\nLATER_PUBLIC_FIXTURE\n%%EOF"
        self.resume_path.write_bytes(changed)
        prepared = await worker.invoke(
            "career.mail.prepare",
            {
                "to": "recipient@example.test",
                "subject": "Public fixture attachment",
                "body": "Synthetic preparation only; do not send.",
                "resume": dict(imported.output),
                "prepared_at": "2026-10-30T10:00:00+08:00",
            },
            self.context,
        )
        self.assertEqual(Outcome.SUCCEEDED, prepared.outcome)
        self.assertTrue(prepared.output["approval_required"])
        args = prepared.output["send_arguments"]
        message = BytesParser(policy=policy.default).parsebytes(
            await self.artifacts.read(args["mime_artifact_id"])
        )
        attachments = list(message.iter_attachments())
        self.assertEqual(1, len(attachments))
        self.assertEqual(self.resume_bytes, attachments[0].get_payload(decode=True))
        self.assertEqual([self.resume_hash], args["attachment_hashes"])
        self.assertEqual(changed, self.resume_path.read_bytes())
        self.assert_no_material_leak(prepared.output)

    async def test_changed_file_is_rejected_before_artifact_creation(self):
        self.resume_path.write_bytes(b"%PDF-1.4\nDIFFERENT_PUBLIC_FIXTURE\n%%EOF")
        response = await self.worker.invoke(
            "career.resume.import_file", self.import_arguments(), self.context
        )
        self.assertNotEqual(Outcome.SUCCEEDED, response.outcome)
        self.assertNotEqual("UNKNOWN_TOOL", response.output.get("code"))
        self.assertEqual([], self.artifacts.put_calls)
        self.assert_no_material_leak(response.output)

    async def test_missing_root_configuration_is_explicit_user_action(self):
        worker = await self.make_worker(config={})
        self.addAsyncCleanup(worker.shutdown)
        for tool, args in (
            ("career.resume.list", {}),
            ("career.resume.import_file", self.import_arguments()),
        ):
            with self.subTest(tool=tool):
                response = await worker.invoke(tool, args, self.context)
                self.assertEqual(Outcome.NEEDS_USER_ACTION, response.outcome)
                self.assertEqual("RESUME_ROOTS_REQUIRED", response.output["code"])
        self.assertEqual([], self.artifacts.put_calls)

    async def test_import_requires_artifact_capability_but_never_mail_configuration(self):
        worker = await self.make_worker(artifacts=False)
        self.addAsyncCleanup(worker.shutdown)
        response = await worker.invoke(
            "career.resume.import_file", self.import_arguments(), self.context
        )
        self.assertEqual(Outcome.NEEDS_USER_ACTION, response.outcome)
        self.assertEqual("ARTIFACT_CAPABILITY_REQUIRED", response.output["code"])
        self.assert_no_material_leak(response.output)

    async def test_unknown_root_and_escape_paths_do_not_read_or_store_bytes(self):
        for args in (
            {**self.import_arguments(), "root_key": "unconfigured"},
            {**self.import_arguments(), "relative_path": "../outside.pdf"},
            {**self.import_arguments(), "relative_path": str(self.resume_path)},
        ):
            with self.subTest(arguments=args):
                response = await self.worker.invoke("career.resume.import_file", args, self.context)
                self.assertNotEqual(Outcome.SUCCEEDED, response.outcome)
                self.assertNotEqual("UNKNOWN_TOOL", response.output.get("code"))
                self.assert_no_material_leak(response.output)
        self.assertEqual([], self.artifacts.put_calls)


class LocalResumeManifestTests(unittest.TestCase):
    def test_local_file_tools_match_sdk_risks_and_published_schemas(self):
        plugin = Path(__file__).resolve().parents[1]
        manifest = tomllib.loads((plugin / "extension.toml").read_text(encoding="utf-8"))
        self.assertEqual("0.2.0", manifest["version"])
        tools = {item.id: item for item in CareerApplyExtension().tools()}
        self.assertEqual(13, len(tools))
        self.assertIs(RiskLevel.READ, tools["career.resume.list"].risk)
        self.assertIs(RiskLevel.INTERNAL_WRITE, tools["career.resume.import_file"].risk)
        declarations = {item["id"]: item for item in manifest["tools"]}
        for name in ("career.resume.list", "career.resume.import_file"):
            declared = declarations[name]
            self.assertEqual(tools[name].risk.value, declared["risk"])
            for field, schema in (
                ("input_schema", tools[name].input_schema),
                ("output_schema", tools[name].output_schema),
            ):
                self.assertEqual(
                    schema, json.loads((plugin / declared[field]).read_text(encoding="utf-8"))
                )
        self.assertEqual(
            {"root_key", "relative_path", "expected_sha256"},
            set(tools["career.resume.import_file"].input_schema["required"]),
        )
        config = json.loads((plugin / "schemas/config.json").read_text(encoding="utf-8"))
        self.assertIn("resume_roots", config["properties"])
