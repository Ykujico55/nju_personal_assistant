"""Actual SDK stdio transport; only creates a synthetic temporary workspace."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from contextlib import suppress
from pathlib import Path


class StdioWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_handshake_create_review_and_local_authorization_boundary(self):
        plugin = Path(__file__).resolve().parents[1]
        project = plugin.parents[1]
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = Path(temporary.name)
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.upper().startswith(("PA_", "PG", "DATABASE_", "POSTGRES_"))
        }
        environment.update(
            PYTHONPATH=os.pathsep.join((str(plugin / "src"), str(project / "extension_sdk/src"))),
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONUTF8="1",
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-Xutf8",
            "-B",
            "-m",
            "plugin_developer.worker",
            cwd=plugin,
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        counter = 0

        async def call(method, params):
            nonlocal counter
            counter += 1
            identifier = str(counter)
            request = {"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}
            process.stdin.write((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
            await process.stdin.drain()
            frame = await asyncio.wait_for(process.stdout.readline(), timeout=20)
            self.assertTrue(frame, "worker ended before its RPC response")
            response = json.loads(frame)
            self.assertEqual(identifier, response.get("id"))
            self.assertNotIn("error", response, response)
            return response["result"]

        async def invoke(name, arguments):
            return await call(
                "tool.invoke",
                {
                    "tool_id": name,
                    "arguments": arguments,
                    "context": {
                        "task_id": "stdio-fixture",
                        "run_id": "run",
                        "deadline": "2030-01-01T00:00:00Z",
                        "idempotency_key": f"stdio-{counter + 1}",
                    },
                },
            )

        try:
            identity = await call(
                "system.handshake",
                {
                    "protocol_version": "1",
                    "runtime": {
                        "protocol_version": "1",
                        "extension_id": "assistant.plugin_developer",
                        "extension_version": "0.1.0",
                        "data_namespace": "ext_stdio",
                        "non_secret_config": {"workspace_root": str(workspace)},
                    },
                },
            )
            self.assertEqual("assistant.plugin_developer", identity["id"])
            descriptors = await call("tool.list", {})
            names = {item["id"] for item in descriptors}
            self.assertTrue(
                {
                    "developer.workspace.create",
                    "developer.files.write",
                    "developer.review",
                    "developer.validate",
                    "developer.release.request",
                }
                <= names
            )
            self.assertFalse(names & {"developer.grant", "developer.install", "developer.enable"})
            validate = next(item for item in descriptors if item["id"] == "developer.validate")
            self.assertNotIn("approved_digest", validate["input_schema"]["properties"])
            self.assertFalse(validate["input_schema"]["additionalProperties"])

            created = await invoke(
                "developer.workspace.create",
                {
                    "extension_id": "fixture.stdio",
                    "version": "0.1.0",
                },
            )
            self.assertEqual("SUCCEEDED", created["outcome"])
            candidate = created["output"]["candidate_id"]
            self.assertRegex(candidate, r"^cand_[a-f0-9]{32}$")
            self.assertTrue((workspace / "candidates" / candidate / "extension.toml").is_file())
            review = await invoke("developer.review", {"candidate_id": candidate})
            self.assertEqual("SUCCEEDED", review["outcome"])
            digest = review["output"]["candidate_hash"]
            self.assertRegex(digest, r"^[a-f0-9]{64}$")
            self.assertTrue(review["output"]["static_review"]["ok"], review)
            blocked = await invoke(
                "developer.validate",
                {
                    "candidate_id": candidate,
                    "candidate_hash": digest,
                },
            )
            self.assertEqual("NEEDS_USER_ACTION", blocked["outcome"])
            self.assertIn("APPROVAL", blocked["output"]["code"])
            public = json.dumps([review, blocked], ensure_ascii=False)
            self.assertNotIn(str(workspace), public)
            for private in ("approved_digest", "run_token", "confirmation_nonce"):
                self.assertNotIn(private, public)
        finally:
            if process.stdin is not None and not process.stdin.is_closing():
                process.stdin.close()
                with suppress(BrokenPipeError, ConnectionResetError):
                    await process.stdin.wait_closed()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except TimeoutError:
                process.kill()
                await process.wait()
            stderr = (await process.stderr.read()).decode("utf-8", errors="replace")
            self.assertEqual(0, process.returncode, stderr)
