"""SDK contracts and dialogue routing, using synthetic service responses only."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
import tomllib
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from personal_assistant_sdk import ContextQuery, InvocationContext, Outcome, RuntimeContext

from personal_files.errors import FilesError
from personal_files.worker import PersonalFilesExtension

PLUGIN = Path(__file__).resolve().parents[1]


def context(key="request-1", *, seconds=30):
    return InvocationContext(
        "task-1",
        "run-1",
        (
            datetime.now(UTC)
            + timedelta(
                seconds=seconds,
            )
        ).isoformat(),
        key,
    )


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.worker = PersonalFilesExtension()
        await self.worker.initialize(
            RuntimeContext(
                "1",
                "personal.files",
                "0.1.0",
                "ext_files",
                manifest_schema_hash="fixture",
                non_secret_config={"state_directory": "synthetic-state", "roots": []},
            )
        )
        self.addAsyncCleanup(self.worker.shutdown)
        self.service = Mock()
        self.factory = patch("personal_files.worker.FileService", return_value=self.service)
        self.factory.start()
        self.addCleanup(self.factory.stop)

    async def test_install_probe_does_not_initialize_filesystem(self):
        self.assertTrue((await self.worker.health()).healthy)
        self.assertEqual((), await self.worker.retrieve(ContextQuery("", limit=0)))
        with patch("personal_files.worker.FileService") as factory:
            self.assertEqual((), await self.worker.retrieve(ContextQuery("", limit=0)))
            factory.assert_not_called()

    async def test_instance_configuration_is_shared_by_different_tasks(self):
        self.service.roots.return_value = {"roots": [{"key": "work", "mode": "read_write"}]}
        first = await self.worker.invoke("files.roots", {}, context())
        second = await self.worker.invoke(
            "files.roots",
            {},
            InvocationContext(
                "different-task",
                "run-2",
                context().deadline,
                "other-request",
            ),
        )
        self.assertEqual(Outcome.SUCCEEDED, first.outcome)
        self.assertEqual(first.output, second.output)
        self.assertEqual(2, self.service.roots.call_count)

    async def test_manifest_matches_runtime_and_delete_cannot_be_self_approved(self):
        manifest = tomllib.loads((PLUGIN / "extension.toml").read_text(encoding="utf-8"))
        tools = {tool.id: tool for tool in self.worker.tools()}
        self.assertEqual(
            {
                "files.roots",
                "files.list",
                "files.read",
                "files.preview",
                "files.write",
                "files.status",
            },
            set(tools),
        )
        for entry in manifest["tools"]:
            tool = tools[entry["id"]]
            self.assertEqual(entry["risk"], tool.risk.value)
            self.assertEqual(
                json.loads((PLUGIN / entry["input_schema"]).read_text()), tool.input_schema
            )
            self.assertEqual(
                json.loads((PLUGIN / entry["output_schema"]).read_text()), tool.output_schema
            )
        self.assertEqual("INTERNAL_WRITE", tools["files.preview"].risk.value)
        for tool_id, args in (
            ("files.write", {"plan_id": "plan", "plan_hash": "0" * 64, "confirmed": True}),
            (
                "files.preview",
                {
                    "operation": "delete",
                    "root_key": "work",
                    "path": "a.txt",
                    "expected_sha256": "0" * 64,
                    "confirmed": True,
                },
            ),
        ):
            result = await self.worker.invoke(tool_id, args, context())
            self.assertNotEqual(Outcome.SUCCEEDED, result.outcome)
        self.service.write.assert_not_called()
        self.service.preview.assert_not_called()

    async def test_expired_or_invalid_deadline_does_not_write(self):
        for deadline in (context(seconds=-1).deadline, "bad", "2030-01-01T00:00:00"):
            with self.subTest(deadline=deadline):
                result = await self.worker.invoke(
                    "files.write",
                    {
                        "plan_id": "plan-1",
                        "plan_hash": "0" * 64,
                    },
                    InvocationContext("task", "run", deadline, "key"),
                )
                self.assertNotEqual(Outcome.SUCCEEDED, result.outcome)
        self.service.write.assert_not_called()

    async def test_preview_and_commit_forward_original_idempotency_key(self):
        self.service.preview.return_value = {"plan_id": "plan-1", "plan_hash": "1" * 64}
        preview = await self.worker.invoke(
            "files.preview",
            {
                "operation": "write",
                "root_key": "work",
                "path": "a.txt",
                "expected_sha256": None,
                "content": "公开测试",
            },
            context("original-preview"),
        )
        self.assertEqual(Outcome.SUCCEEDED, preview.outcome)
        self.service.preview.assert_called_once_with(
            "write",
            "work",
            "a.txt",
            None,
            "公开测试",
            idempotency_key="original-preview",
        )
        receipt = {
            "status": "SUCCEEDED",
            "historical_receipt": True,
            "current_matches_receipt": False,
            "sha256": "2" * 64,
        }
        self.service.write.return_value = receipt
        result = await self.worker.invoke(
            "files.write",
            {
                "plan_id": "plan-1",
                "plan_hash": "1" * 64,
            },
            context("original-write"),
        )
        self.service.write.assert_called_once_with(
            "plan-1",
            "1" * 64,
            idempotency_key="original-write",
        )
        self.assertEqual(receipt, result.output)
        self.assertFalse(result.output["current_matches_receipt"])

    async def test_errors_are_classified_without_exception_text(self):
        for error, expected in (
            (FilesError("VERSION_CONFLICT"), Outcome.NEEDS_USER_ACTION),
            (PermissionError("PRIVATE_PATH"), Outcome.PERMANENT),
            (OSError("PRIVATE_PATH"), Outcome.UNKNOWN),
            (RuntimeError("PRIVATE_PATH"), Outcome.UNKNOWN),
        ):
            self.service.write.side_effect = error
            result = await self.worker.invoke(
                "files.write",
                {
                    "plan_id": "plan-1",
                    "plan_hash": "0" * 64,
                },
                context(),
            )
            self.assertEqual(expected, result.outcome)
            self.assertNotIn("PRIVATE_PATH", json.dumps(dict(result.output)))
        self.service.write.side_effect = None
        self.service.write.return_value = {"status": "UNKNOWN", "code": "RESULT_UNCONFIRMED"}
        result = await self.worker.invoke(
            "files.write",
            {
                "plan_id": "plan-1",
                "plan_hash": "0" * 64,
            },
            context(),
        )
        self.assertEqual(Outcome.UNKNOWN, result.outcome)

    async def test_workspace_uses_roots_until_an_exact_directory_uri_is_given(self):
        self.service.roots.return_value = {"roots": [{"key": "work", "label": "工作目录"}]}
        evidence = await self.worker.retrieve(
            ContextQuery(
                "列出目录",
                filters={"provider_ids": ["files.workspace"]},
            )
        )
        self.assertEqual(1, len(evidence))
        self.service.list.assert_not_called()
        self.service.read.assert_not_called()
        self.service.list.return_value = {"entries": [{"path": "notes/a.md"}]}
        await self.worker.retrieve(
            ContextQuery(
                "files://work/notes/",
                filters={"provider_ids": ["files.workspace"]},
            )
        )
        self.service.list.assert_called_once_with("work", "notes", offset=0, limit=100)

    async def test_exact_uri_body_is_complete_and_has_host_accepted_provenance(self):
        body = "公开正文。" * 15000
        digest = hashlib.sha256(body.encode()).hexdigest()
        self.service.read.return_value = {
            "content": body,
            "sha256": digest,
            "offset": 0,
            "next_offset": None,
        }
        evidence = await self.worker.retrieve(
            ContextQuery(
                "files://work/%E7%AC%94%E8%AE%B0.md",
                filters={"provider_ids": ["files.document"]},
            )
        )
        self.service.read.assert_called_once_with("work", "笔记.md", offset=0, limit=262144)
        self.assertEqual(body, evidence[0].text)
        self.assertEqual(digest, evidence[0].content_hash)
        self.assertEqual("CURRENT", evidence[0].metadata["status"])
        self.assertEqual("SENSITIVE", evidence[0].metadata["sensitivity"])
        self.assertEqual("USER_SOURCE", evidence[0].metadata["trust"])
        self.assertEqual("personal.files", evidence[0].metadata["extension_id"])
        self.assertEqual("0.1.0", evidence[0].metadata["extension_version"])

    async def test_document_requires_explicit_provider_and_target(self):
        for query in (
            ContextQuery("files://work/a.txt"),
            ContextQuery("读取我的文件", filters={"provider_ids": ["files.document"]}),
            ContextQuery(
                "files://work/a.txt?other=x", filters={"provider_ids": ["files.document"]}
            ),
        ):
            evidence = await self.worker.retrieve(query)
            self.assertTrue(not evidence or "code" in evidence[0].text)
        self.service.read.assert_not_called()

    async def test_partial_document_is_explicit_status_not_full_body(self):
        self.service.read.return_value = {
            "content": "first part",
            "sha256": "0" * 64,
            "next_offset": 10,
        }
        evidence = await self.worker.retrieve(
            ContextQuery(
                "files://work/a.txt",
                filters={"provider_ids": ["files.document"]},
            )
        )
        self.assertIn("DOCUMENT_INCOMPLETE", evidence[0].text)
        self.assertNotEqual("first part", evidence[0].text)

    async def test_mkdir_plan_is_r1_and_rejects_content_or_existing_hash_before_io(self):
        self.service.preview.return_value = {
            "plan_id": "mkdir-plan",
            "plan_hash": "1" * 64,
            "operation": "mkdir",
            "after_sha256": None,
        }
        valid = {"operation": "mkdir", "root_key": "work", "path": "notes", "expected_sha256": None}
        result = await self.worker.invoke("files.preview", valid, context("mkdir-preview"))
        self.assertEqual(Outcome.SUCCEEDED, result.outcome, result)
        self.service.preview.assert_called_once_with(
            "mkdir",
            "work",
            "notes",
            None,
            None,
            idempotency_key="mkdir-preview",
        )
        self.service.preview.reset_mock()
        for extra in (
            {"content": ""},
            {"content": None},
            {"expected_sha256": "0" * 64},
            {"confirmed": True},
        ):
            with self.subTest(extra=extra):
                denied = await self.worker.invoke("files.preview", {**valid, **extra}, context())
                self.assertEqual(Outcome.PERMANENT, denied.outcome)
        self.service.preview.assert_not_called()

    async def test_unconfigured_and_drained_worker_fail_closed(self):
        self.service.roots.side_effect = FilesError("CONFIGURATION_REQUIRED")
        result = await self.worker.invoke("files.roots", {}, context())
        self.assertEqual(Outcome.NEEDS_USER_ACTION, result.outcome)
        self.assertTrue((await self.worker.drain(0)).drained)
        result = await self.worker.invoke("files.roots", {}, context())
        self.assertEqual(Outcome.RETRYABLE, result.outcome)


class StdioTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_unconfigured_stdio_handshake_and_context_probe(self):
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.upper().startswith(("PA_", "PG", "DATABASE_", "POSTGRES_"))
        }
        environment.update(
            PYTHONPATH=os.pathsep.join(
                (
                    str(PLUGIN / "src"),
                    str(
                        PLUGIN.parents[1] / "extension_sdk/src",
                    ),
                )
            ),
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONUTF8="1",
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-B",
            "-m",
            "personal_files.worker",
            cwd=PLUGIN,
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def call(identifier, method, params):
            process.stdin.write(
                (
                    json.dumps(
                        {"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}
                    )
                    + "\n"
                ).encode()
            )
            await process.stdin.drain()
            response = json.loads(await asyncio.wait_for(process.stdout.readline(), 15))
            self.assertNotIn("error", response, response)
            return response["result"]

        try:
            info = await call(
                "1",
                "system.handshake",
                {
                    "protocol_version": "1",
                    "runtime": {
                        "protocol_version": "1",
                        "extension_id": "personal.files",
                        "extension_version": "0.1.0",
                        "data_namespace": "ext_files",
                    },
                },
            )
            self.assertEqual(["ToolProvider", "ContextProvider"], info["slots"])
            self.assertEqual(
                [],
                await call(
                    "2",
                    "context.retrieve",
                    {
                        "query": {"text": "", "limit": 0},
                    },
                ),
            )
            self.assertEqual(6, len(await call("3", "tool.list", {})))
            self.assertTrue((await call("4", "system.health", {}))["healthy"])
        finally:
            process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 10)
            except TimeoutError:
                process.kill()
                await process.wait()
            error = (await process.stderr.read()).decode("utf-8", errors="replace")
            self.assertEqual(0, process.returncode, error)


class RealFileWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.root = base / "authorized"
        self.root.mkdir()
        self.state = base / "control"
        self.state.mkdir()
        self.worker = PersonalFilesExtension()
        await self.worker.initialize(
            RuntimeContext(
                "1",
                "personal.files",
                "0.1.0",
                "ext_files",
                non_secret_config={
                    "state_directory": str(self.state),
                    "roots": [
                        {
                            "key": "work",
                            "label": "Work",
                            "path": str(self.root),
                            "mode": "read_write",
                        }
                    ],
                },
            )
        )
        self.addAsyncCleanup(self.worker.shutdown)

    async def plan(self, content, *, expected=None, key="preview-1"):
        result = await self.worker.invoke(
            "files.preview",
            {
                "operation": "write",
                "root_key": "work",
                "path": "sample.txt",
                "expected_sha256": expected,
                "content": content,
            },
            context(key),
        )
        self.assertEqual(Outcome.SUCCEEDED, result.outcome, result)
        return {key: result.output[key] for key in ("plan_id", "plan_hash")}

    async def test_real_preview_write_and_replay_never_restore_historical_content(self):
        plan = await self.plan("published version")
        self.assertFalse((self.root / "sample.txt").exists())
        first = await self.worker.invoke("files.write", plan, context("write-1"))
        self.assertEqual(Outcome.SUCCEEDED, first.outcome, first)
        self.assertEqual("published version", (self.root / "sample.txt").read_text())
        (self.root / "sample.txt").write_text("later manual edit")
        replay = await self.worker.invoke("files.write", plan, context("write-1"))
        self.assertEqual(first.output, replay.output)
        self.assertEqual("later manual edit", (self.root / "sample.txt").read_text())
        self.assertIn("receipt_scope", replay.output)
        read = await self.worker.invoke(
            "files.read",
            {
                "root_key": "work",
                "path": "sample.txt",
            },
            context(),
        )
        self.assertEqual("later manual edit", read.output["content"])

    async def test_real_changed_file_blocks_commit_and_delete_is_owner_only(self):
        target = self.root / "sample.txt"
        target.write_text("original")
        old_hash = hashlib.sha256(b"original").hexdigest()
        plan = await self.plan("new", expected=old_hash)
        target.write_text("manual edit")
        refused = await self.worker.invoke("files.write", plan, context("write-1"))
        self.assertEqual(Outcome.NEEDS_USER_ACTION, refused.outcome, refused)
        self.assertEqual("VERSION_CONFLICT", refused.output["code"])
        self.assertEqual("manual edit", target.read_text())
        delete = await self.worker.invoke(
            "files.preview",
            {
                "operation": "delete",
                "root_key": "work",
                "path": "sample.txt",
                "expected_sha256": hashlib.sha256(b"manual edit").hexdigest(),
            },
            context("delete-preview"),
        )
        self.assertEqual(Outcome.SUCCEEDED, delete.outcome, delete)
        denied = await self.worker.invoke(
            "files.write",
            {key: delete.output[key] for key in ("plan_id", "plan_hash")},
            context("delete-write"),
        )
        self.assertEqual(Outcome.NEEDS_USER_ACTION, denied.outcome, denied)
        self.assertEqual("LOCAL_CONFIRMATION_REQUIRED", denied.output["code"])
        self.assertTrue(target.exists())

    async def test_real_context_directory_uri_and_exact_body_are_usable(self):
        notes = self.root / "notes"
        notes.mkdir()
        document = notes / "sample.md"
        document.write_bytes(b"# Public\nSynthetic body")
        listing = await self.worker.retrieve(
            ContextQuery(
                "files://work/notes/",
                filters={"provider_ids": ["files.workspace"]},
            )
        )
        self.assertEqual(1, len(listing))
        self.assertIn("sample.md", listing[0].text)
        self.assertNotIn("Synthetic body", listing[0].text)
        body = await self.worker.retrieve(
            ContextQuery(
                "files://work/notes/sample.md",
                filters={"provider_ids": ["files.document"]},
            )
        )
        self.assertEqual("# Public\nSynthetic body", body[0].text)
        self.assertEqual("files://work/notes/sample.md", body[0].source_uri)
