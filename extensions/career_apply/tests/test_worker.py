from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from personal_assistant_sdk import InvocationContext, Outcome, RiskLevel, RuntimeContext

from career_apply.worker import CareerApplyExtension


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.worker = CareerApplyExtension()
        self.runtime = RuntimeContext("1", "career.apply", "0.2.0", "ext_career")
        self.info = await self.worker.initialize(self.runtime)
        self.context = InvocationContext("fixture-task", "run", "2030-01-01T00:00:00Z", "key")

    async def test_public_sdk_identity_and_read_tools_are_registered(self):
        self.assertEqual("career.apply", self.info.id)
        tools = {item.id: item for item in self.worker.tools()}
        self.assertIs(RiskLevel.READ, tools["career.jobs.filter"].risk)
        self.assertIs(RiskLevel.EXTERNAL_WRITE, tools["career.boss.execute"].risk)
        self.assertIs(RiskLevel.READ, tools["career.boss.execute.preview"].risk)
        self.assertTrue((await self.worker.health()).healthy)

    async def test_plan_generation_is_not_delivery_success(self):
        result = await self.worker.invoke(
            "career.boss.plan",
            {
                "job": {
                    "url": "https://www.zhipin.com/job_detail/fixture.html",
                    "title": "Python",
                    "company": "Fixture",
                    "recruiter": "HR",
                },
                "action_kind": "message",
                "message": "Public fixture",
            },
            self.context,
        )
        self.assertEqual(Outcome.SUCCEEDED, result.outcome)
        self.assertFalse(result.output["execution_available"])
        self.assertEqual("PREPARED", result.output["state"])

    async def test_disabled_browser_preview_does_not_claim_ready(self):
        result = await self.worker.invoke("career.boss.execute.preview", {}, self.context)
        self.assertNotEqual(Outcome.SUCCEEDED, result.outcome)

    async def test_missing_mail_configuration_is_user_action_not_plugin_failure(self):
        response = await self.worker.invoke("career.mail.prepare", {}, self.context)
        self.assertEqual(Outcome.NEEDS_USER_ACTION, response.outcome)
        self.assertEqual("MAIL_ACCOUNT_REQUIRED", response.output["code"])

    async def test_bad_inputs_do_not_echo_private_material(self):
        result = await self.worker.invoke(
            "career.mail.plan",
            {
                "recipients": [{"email": "PRIVATE\r\nBcc: x@example.test"}],
                "subject": "s",
                "body": "b",
            },
            self.context,
        )
        self.assertEqual(Outcome.PERMANENT, result.outcome)
        self.assertNotIn("PRIVATE", str(result.output))

    async def test_browser_preview_is_live_and_execute_needs_verified_receipt(self):
        args = {
            "local_action_id": "boss_" + "a" * 64,
            "target": {
                "url": "https://www.zhipin.com/job_detail/fixture.html",
                "job_id": "fixture",
                "company": "Fixture",
                "recruiter": "HR",
            },
            "operation": {"kind": "message", "text": "fixture", "snapshot_fingerprint": "a" * 64},
            "consequences": "Send fixture",
            "attachments": [],
        }
        browser = AsyncMock()
        browser.review.return_value = args
        browser.execute.return_value = {
            "status": "SUCCEEDED",
            "state": "MESSAGE_SENT",
            "receipt": {"verified": True, "reference_id": "proof"},
        }
        self.worker._boss = browser
        preview = await self.worker.invoke("career.boss.execute.preview", args, self.context)
        self.assertEqual(Outcome.SUCCEEDED, preview.outcome)
        self.assertEqual(args, preview.output["payload"])
        browser.review.assert_awaited_once_with(args)
        sent = await self.worker.invoke("career.boss.execute", args, self.context)
        self.assertEqual(Outcome.SUCCEEDED, sent.outcome)
        self.assertEqual("proof", sent.external_receipt["reference_id"])
        browser.execute.return_value = {"status": "SUCCEEDED", "state": "MESSAGE_SENT"}
        unknown = await self.worker.invoke("career.boss.execute", args, self.context)
        self.assertEqual(Outcome.UNKNOWN, unknown.outcome)

    async def test_prepare_passes_owned_resume_reference_to_browser_adapter(self):
        browser = AsyncMock()
        browser.prepare.return_value = {"local_action_id": "fixture"}
        self.worker._boss = browser
        resume = {
            "artifact_id": "owned-resume",
            "filename": "resume.pdf",
            "sha256": "a" * 64,
            "size_bytes": 12,
            "media_type": "application/pdf",
        }
        job = {
            "url": "https://www.zhipin.com/job_detail/fixture.html",
            "company": "Fixture",
            "recruiter": "HR",
            "title": "Developer",
        }
        response = await self.worker.invoke(
            "career.boss.prepare",
            {
                "job": job,
                "action_kind": "send_resume",
                "resume": resume,
            },
            self.context,
        )
        self.assertEqual(Outcome.SUCCEEDED, response.outcome)
        browser.prepare.assert_awaited_once_with(
            job,
            "send_resume",
            message="",
            resume_label="",
            resume=resume,
        )

    async def test_unexpected_error_after_browser_dispatch_is_unknown(self):
        browser = AsyncMock()
        browser.execute.side_effect = ValueError("private transport detail")
        self.worker._boss = browser
        response = await self.worker.invoke("career.boss.execute", {}, self.context)
        self.assertEqual(Outcome.UNKNOWN, response.outcome)
        self.assertNotIn("private", str(response.output))

    async def test_draining_stops_new_invocations_and_shutdown_is_real(self):
        drained = await self.worker.drain(0)
        self.assertTrue(drained.drained)
        result = await self.worker.invoke("career.jobs.filter", {"jobs": []}, self.context)
        self.assertEqual(Outcome.RETRYABLE, result.outcome)
        await self.worker.shutdown()
        self.assertFalse((await self.worker.health()).healthy)


class WorkerProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdio_worker_handshake_lists_and_plans_without_host_imports(self):
        import asyncio
        import json
        import os
        import sys
        import tempfile
        from pathlib import Path

        local_directory = tempfile.TemporaryDirectory()
        self.addCleanup(local_directory.cleanup)
        local_resume = Path(local_directory.name) / "stdio-resume.txt"
        local_resume.write_text("Public local stdio fixture", encoding="utf-8")
        plugin = Path(__file__).resolve().parents[1]
        root = plugin.parents[1]
        env = {name: value for name, value in os.environ.items() if not name.startswith("PA_")}
        env["PYTHONPATH"] = os.pathsep.join((str(plugin / "src"), str(root / "extension_sdk/src")))
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-Xutf8",
            "-B",
            "-m",
            "career_apply.worker",
            cwd=plugin,
            env=env,
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
            if method == "system.shutdown":
                process.stdin.close()
                await process.stdin.wait_closed()
            line = await asyncio.wait_for(process.stdout.readline(), timeout=15)
            self.assertTrue(line)
            response = json.loads(line)
            self.assertNotIn("error", response, response)
            return response["result"]

        try:
            identity = await call(
                "1",
                "system.handshake",
                {
                    "protocol_version": "1",
                    "runtime": {
                        "protocol_version": "1",
                        "extension_id": "career.apply",
                        "extension_version": "0.2.0",
                        "data_namespace": "ext_career",
                        "non_secret_config": {
                            "resume_roots": [
                                {"key": "resumes", "path": local_directory.name}
                            ]
                        },
                    },
                },
            )
            self.assertEqual("career.apply", identity["id"])
            tools = await call("2", "tool.list", {})
            self.assertEqual(13, len(tools))
            plan = await call(
                "3",
                "tool.invoke",
                {
                    "tool_id": "career.jobs.filter",
                    "arguments": {"jobs": []},
                    "context": {
                        "task_id": "fixture",
                        "run_id": "run",
                        "deadline": "2030-01-01T00:00:00Z",
                        "idempotency_key": "key",
                    },
                },
            )
            self.assertEqual("SUCCEEDED", plan["outcome"])
            self.assertEqual([], plan["output"]["jobs"])
            listed = await call(
                "4", "tool.invoke",
                {
                    "tool_id": "career.resume.list", "arguments": {},
                    "context": {
                        "task_id": "fixture", "run_id": "run",
                        "deadline": "2030-01-01T00:00:00Z", "idempotency_key": "list-key",
                    },
                },
            )
            self.assertEqual("SUCCEEDED", listed["outcome"])
            self.assertEqual("stdio-resume.txt", listed["output"]["files"][0]["relative_path"])
            self.assertNotIn(local_directory.name, json.dumps(listed))
            self.assertNotIn("Public local stdio fixture", json.dumps(listed))
            # EOF is the stdio smoke-test boundary; shutdown is exercised by DispatcherTests.
        finally:
            process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except TimeoutError:
                process.kill()
                await process.wait()
            self.assertEqual(0, process.returncode, (await process.stderr.read()).decode())


class ManifestTests(unittest.TestCase):
    def test_manifest_matches_all_sdk_descriptors_and_schema_files(self):
        import json
        import tomllib
        from pathlib import Path

        plugin = Path(__file__).resolve().parents[1]
        manifest = tomllib.loads((plugin / "extension.toml").read_text())
        tools = {tool.id: tool for tool in CareerApplyExtension().tools()}
        self.assertEqual(set(tools), {tool["id"] for tool in manifest["tools"]})
        for declared in manifest["tools"]:
            tool = tools[declared["id"]]
            self.assertEqual(tool.risk.value, declared["risk"])
            self.assertEqual(
                tool.input_schema, json.loads((plugin / declared["input_schema"]).read_text())
            )
            self.assertEqual(
                tool.output_schema, json.loads((plugin / declared["output_schema"]).read_text())
            )
        mail = next(tool for tool in manifest["tools"] if tool["id"] == "career.mail.send")
        self.assertEqual(["mail.send"], mail["capabilities"])


class DispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_dispatcher_handshake_and_json_shaped_filter_call(self):
        from personal_assistant_sdk.rpc import RpcRequest
        from personal_assistant_sdk.worker import ExtensionDispatcher

        dispatcher = ExtensionDispatcher(CareerApplyExtension)
        response = await dispatcher.dispatch(
            RpcRequest(
                id="1",
                method="system.handshake",
                params={
                    "protocol_version": "1",
                    "runtime": {
                        "protocol_version": "1",
                        "extension_id": "career.apply",
                        "extension_version": "0.2.0",
                        "data_namespace": "ext_career",
                    },
                },
            )
        )
        self.assertIsNone(response.error)
        tools = await dispatcher.dispatch(RpcRequest(id="2", method="tool.list", params={}))
        self.assertIsNone(tools.error)
        self.assertEqual(13, len(tools.result))
        filtered = await dispatcher.dispatch(
            RpcRequest(
                id="3",
                method="tool.invoke",
                params={
                    "tool_id": "career.jobs.filter",
                    "arguments": {"jobs": []},
                    "context": {
                        "task_id": "fixture",
                        "run_id": "run",
                        "deadline": "2030-01-01T00:00:00Z",
                        "idempotency_key": "key",
                    },
                },
            )
        )
        self.assertIsNone(filtered.error)
        self.assertEqual("SUCCEEDED", filtered.result["outcome"])
        self.assertEqual([], filtered.result["output"]["jobs"])
        await dispatcher.dispatch(RpcRequest(id="4", method="system.shutdown", params={}))
