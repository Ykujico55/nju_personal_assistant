"""F02: host-side JSON-RPC framing, environment, timeout and crash containment."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

from personal_assistant.core.extensions.errors import (
    ExtensionOperationError,
    RpcCallError,
    RpcTimeoutError,
)
from personal_assistant.core.extensions.rpc import JsonRpcProcessClient, WorkerSpec

SILENT = """
import os, sys, time
probe = os.environ.get("PA_RPC_PROBE")
while True:
    line = sys.stdin.buffer.readline()
    if not line:
        break
    if probe:
        with open(probe, "ab") as stream:
            stream.write(b"call\\n")
    time.sleep(60)
"""

MALFORMED = """
import sys
sys.stdin.buffer.readline()
sys.stdout.write("this is not json\\n")
sys.stdout.flush()
import time; time.sleep(60)
"""

OVERSIZE = """
import sys
sys.stdin.buffer.readline()
sys.stdout.write("x" * 8192 + "\\n")
sys.stdout.flush()
import time; time.sleep(60)
"""

CRASH = """
import sys
sys.stdin.buffer.readline()
raise SystemExit(3)
"""

ENV_DUMP = """
import json, os, sys
with open(os.environ["PA_ENV_DUMP"], "w", encoding="utf-8") as stream:
    json.dump(dict(os.environ), stream)
line = sys.stdin.buffer.readline()
if line:
    request = json.loads(line)
    sys.stdout.write(
        json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {}}) + "\\n"
    )
    sys.stdout.flush()
"""

EXIT_WHILE_WRITING = """
import json, sys, time
line = sys.stdin.buffer.readline()
request = json.loads(line)
sys.stdout.write(
    json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {}}) + "\\n"
)
sys.stdout.flush()
time.sleep(0.3)
sys.exit(0)
"""

SLOW_WORKER = """
import json, sys, time
while True:
    line = sys.stdin.buffer.readline()
    if not line:
        break
    request = json.loads(line)
    if request["method"] == "system.drain":
        time.sleep(60)
        continue
    time.sleep(60)
"""

UNCLEAN_DRAIN = """
import json, sys, time
line = sys.stdin.buffer.readline()
request = json.loads(line)
report = {"drained": False, "active_calls": 2}
sys.stdout.write(
    json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": report}) + "\\n"
)
sys.stdout.flush()
time.sleep(30)
"""

STALL_AFTER_ONE = """
import json, sys, time
line = sys.stdin.buffer.readline()
request = json.loads(line)
sys.stdout.write(
    json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {}}) + "\\n"
)
sys.stdout.flush()
time.sleep(60)
"""


class RpcRobustnessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa_f02_rpc_"))
        self.clients: list[JsonRpcProcessClient] = []
        self.processes: list[asyncio.subprocess.Process] = []
        self.io_tasks: list[asyncio.Task[None]] = []
        original_start = JsonRpcProcessClient.start

        async def tracked_start(client: JsonRpcProcessClient) -> None:
            if client not in self.clients:
                self.clients.append(client)
            try:
                await original_start(client)
            finally:
                # Keep every generation, including restart after a timeout.
                if client._process is not None and client._process not in self.processes:
                    self.processes.append(client._process)
                for task in (client._reader_task, client._stderr_task):
                    if task is not None and task not in self.io_tasks:
                        self.io_tasks.append(task)

        self.enterContext(patch.object(JsonRpcProcessClient, "start", tracked_start))

    async def asyncTearDown(self) -> None:
        # close() may already have cleared _process while bounded reaping is
        # still pending. Retained handles must finish before this test's loop
        # closes, even when an assertion earlier in the test failed.
        failures: list[Exception] = []
        try:
            for client in self.clients:
                try:
                    await client.close()
                except Exception as exc:
                    failures.append(exc)
        finally:
            for process in self.processes:
                try:
                    if process.returncode is None:
                        with contextlib.suppress(ProcessLookupError):
                            process.kill()
                    # returncode can precede pipe EOF (notably after an
                    # oversized frame pauses reading). Close all pipe
                    # transports while this test's event loop is still alive.
                    transport = cast(
                        asyncio.SubprocessTransport,
                        process._transport,  # type: ignore[attr-defined]
                    )
                    transport.close()
                    await asyncio.wait_for(process.wait(), 5)
                except Exception as exc:
                    failures.append(exc)
            for task in self.io_tasks:
                if not task.done():
                    task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass  # RPC teardown deliberately cancels its pipe readers.
                except Exception as exc:
                    failures.append(exc)
            self.clients.clear()
            self.processes.clear()
            self.io_tasks.clear()
        if failures:
            raise ExceptionGroup("RPC test cleanup failed", failures)

    def tearDown(self) -> None:
        resolved = self.tmp.resolve()
        if not resolved.is_relative_to(Path(tempfile.gettempdir()).resolve()):
            raise AssertionError(f"refusing to delete {resolved}")
        shutil.rmtree(resolved, ignore_errors=True)

    def _client(
        self,
        name: str,
        script: str,
        *,
        environment: dict[str, str] | None = None,
        max_frame_bytes: int = 4096,
        max_outbound_frame_bytes: int | None = None,
    ) -> JsonRpcProcessClient:
        module = f"worker_{name}"
        (self.tmp / f"{module}.py").write_text(script, encoding="utf-8")
        outbound = (
            max_outbound_frame_bytes
            if max_outbound_frame_bytes is not None
            else 1024 * 1024
        )
        return JsonRpcProcessClient(
            WorkerSpec(
                module=module,
                python_executable=sys.executable,
                cwd=str(self.tmp),
                environment=environment or {},
            ),
            max_frame_bytes=max_frame_bytes,
            max_outbound_frame_bytes=outbound,
        )

    async def test_worker_environment_never_inherits_host_secrets(self) -> None:
        secrets = {
            "PA_DATABASE_URL": "postgresql://user:pass@host/db",
            "GITHUB_TOKEN": "ghp_example_token",
            "SESSION_COOKIE": "session=secret",
            "PA_CF_ACCESS_AUD": "audience-secret",
            "PA_CF_ACCESS_TEAM_DOMAIN": "team.cloudflareaccess.com",
        }
        previous = {key: os.environ.get(key) for key in secrets}
        os.environ.update(secrets)
        dump = self.tmp / "env.json"
        client = self._client(
            "envdump",
            ENV_DUMP,
            environment={"PA_ENV_DUMP": str(dump), "PA_DECLARED_VALUE": "explicit"},
        )
        try:
            await client.start()
            await client.call("system.ping", {}, timeout_seconds=5)
            await client.close()
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        environment = json.loads(dump.read_text("utf-8"))
        for key in secrets:
            self.assertNotIn(key, environment)
        self.assertNotIn("change-me", json.dumps(environment))
        self.assertEqual("explicit", environment.get("PA_DECLARED_VALUE"))
        if sys.platform == "win32":
            self.assertIn("SYSTEMROOT", environment)

    async def test_write_to_an_exiting_worker_is_a_typed_error(self) -> None:
        client = self._client(
            "exitwrite",
            EXIT_WHILE_WRITING,
            max_frame_bytes=8 * 1024 * 1024,
            max_outbound_frame_bytes=8 * 1024 * 1024,
        )
        await client.start()
        await client.call("system.ping", {}, timeout_seconds=5)
        # The worker stops reading and exits while this large frame is being
        # written; the broken write must surface as a typed RPC error.
        with self.assertRaises(RpcCallError) as captured:
            await client.call(
                "tool.invoke", {"blob": "x" * 1_500_000}, timeout_seconds=10
            )
        self.assertEqual(-32091, captured.exception.code)
        self.assertFalse(client.running)
        await client.close()

    async def test_drain_deadline_caps_waiting_for_an_in_flight_call(self) -> None:
        client = self._client("slowworker", SLOW_WORKER)
        await client.start()
        in_flight = asyncio.create_task(
            client.call("tool.invoke", {}, timeout_seconds=30)
        )
        await asyncio.sleep(0.3)
        deadline = datetime.now(UTC).timestamp() + 0.5
        started = time.monotonic()
        with self.assertRaises(RpcTimeoutError):
            await client.drain(deadline, timeout_seconds=10)
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertFalse(client.running)
        with self.assertRaises(RpcCallError):
            await in_flight
        await client.close()

    async def test_rpc_call_timeout_includes_lock_wait(self) -> None:
        probe = self.tmp / "lock-wait-calls.txt"
        client = self._client("lockwait", SILENT, environment={"PA_RPC_PROBE": str(probe)})
        await client.start()
        process = client._process
        assert process is not None
        first = asyncio.create_task(client.call("system.health", {}, timeout_seconds=30))

        async def wait_for_first_request() -> None:
            while not probe.exists():
                if first.done():
                    first.result()
                    self.fail("the first call completed instead of holding the RPC lock")
                await asyncio.sleep(0.01)

        try:
            # Readiness is a worker receipt, not a sleep that races process startup.
            await asyncio.wait_for(wait_for_first_request(), 5)
            self.assertTrue(client._lock.locked())
            with self.assertRaises(RpcTimeoutError):
                # This outer guard detects an unbounded wait. Exact deadline and
                # remaining-budget arithmetic are checked separately below.
                async with asyncio.timeout(5):
                    await client.call("system.health", {}, timeout_seconds=0.05)
            self.assertFalse(client.running)
            with self.assertRaises(RpcCallError):
                await first
            # Process exit is independently awaited, not included in an RPC
            # latency assertion. Retain this handle after client._process clears.
            await asyncio.wait_for(process.wait(), 5)
            self.assertIsNotNone(process.returncode)
            with self.assertRaises(RpcCallError):
                await client.call("system.health", {}, timeout_seconds=0.05)
            self.assertEqual(["call"], probe.read_text("utf-8").splitlines())
        finally:
            if not first.done():
                first.cancel()
            await asyncio.gather(first, return_exceptions=True)
            await client.close()
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
            await asyncio.wait_for(process.wait(), 5)

    async def test_drain_budget_covers_lock_wait_and_response(self) -> None:
        # Simulate 0.3 seconds spent acquiring the lock. Real OS process cleanup
        # must not be confused with the single 0.5-second request deadline.
        for operation in ("call", "drain"):
            with self.subTest(operation=operation):
                client = self._client("budget", SILENT)
                clock = Mock()
                clock.time.side_effect = [100.0, 100.3]
                timeout_guard = AsyncMock()
                result = {"drained": True, "active_calls": 0}
                exchange = AsyncMock(return_value=result)
                with (
                    patch(
                        "personal_assistant.core.extensions.rpc.asyncio.get_running_loop",
                        return_value=clock,
                    ),
                    patch(
                        "personal_assistant.core.extensions.rpc.asyncio.timeout_at",
                        return_value=timeout_guard,
                    ) as deadline,
                    patch.object(client, "_exchange", exchange),
                ):
                    if operation == "call":
                        actual = await client.call("system.health", {}, timeout_seconds=0.5)
                    else:
                        actual = await client.drain(time.time() + 100, timeout_seconds=0.5)
                self.assertEqual(result, actual)
                deadline.assert_called_once_with(100.5)
                self.assertEqual(2, clock.time.call_count)
                exchange.assert_awaited_once()
                self.assertAlmostEqual(0.2, exchange.call_args.args[2])
                self.assertFalse(client._lock.locked())
                self.assertFalse(client.in_flight)

    async def test_cancellation_during_write_stops_the_worker(self) -> None:
        client = self._client(
            "stallwrite",
            STALL_AFTER_ONE,
            max_frame_bytes=8 * 1024 * 1024,
            max_outbound_frame_bytes=8 * 1024 * 1024,
        )
        await client.start()
        await client.call("system.ping", {}, timeout_seconds=5)
        task = asyncio.create_task(
            client.call("tool.invoke", {"blob": "x" * 4_000_000}, timeout_seconds=60)
        )
        await asyncio.sleep(0.3)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(client._broken)  # noqa: SLF001 - stream state is the contract
        self.assertFalse(client.running)
        await client.close()

    async def test_unclean_drain_report_fails_closed(self) -> None:
        client = self._client("uncleandrain", UNCLEAN_DRAIN)
        await client.start()
        with self.assertRaises(ExtensionOperationError) as captured:
            await client.drain(time.time() + 5, timeout_seconds=5)
        self.assertEqual("DRAIN_TIMEOUT", captured.exception.code)
        await client.close()

    async def test_timeout_terminates_the_worker_and_never_retries(self) -> None:
        probe = self.tmp / "calls.txt"
        client = self._client("silent", SILENT, environment={"PA_RPC_PROBE": str(probe)})
        await client.start()
        with self.assertRaises(RpcTimeoutError):
            await client.call("system.health", {}, timeout_seconds=0.5)
        self.assertFalse(client.running)
        with self.assertRaises(RpcCallError):
            await client.call("system.health", {}, timeout_seconds=0.5)
        self.assertEqual(1, len(probe.read_text("utf-8").splitlines()))
        await client.close()

    async def test_malformed_frame_breaks_the_stream_without_crashing_the_host(self) -> None:
        client = self._client("malformed", MALFORMED)
        await client.start()
        with self.assertRaises(RpcCallError) as captured:
            await client.call("system.health", {}, timeout_seconds=5)
        self.assertEqual(-32700, captured.exception.code)
        self.assertFalse(client.running)
        await client.close()

    async def test_oversized_response_frame_is_rejected(self) -> None:
        client = self._client("oversize", OVERSIZE)
        await client.start()
        with self.assertRaises(RpcCallError) as captured:
            await client.call("system.health", {}, timeout_seconds=5)
        self.assertEqual(-32092, captured.exception.code)
        self.assertFalse(client.running)
        await client.close()

    async def test_unexpected_exit_is_a_typed_error(self) -> None:
        client = self._client("crash", CRASH)
        await client.start()
        with self.assertRaises(RpcCallError) as captured:
            await client.call("system.health", {}, timeout_seconds=5)
        self.assertEqual(-32091, captured.exception.code)
        await client.close()

    async def test_client_can_restart_after_graceful_close(self) -> None:
        probe = self.tmp / "calls-two.txt"
        client = self._client(
            "silent_two", SILENT, environment={"PA_RPC_PROBE": str(probe)}
        )
        await client.start()
        with self.assertRaises(RpcTimeoutError):
            await client.call("system.health", {}, timeout_seconds=0.5)
        await client.close()
        await client.start()
        self.assertTrue(client.running)
        await client.close()

    async def test_oversized_host_result_never_breaks_the_worker_stream(self) -> None:
        # Regression: the host used to write frames up to its own 4 MiB read
        # limit while the worker only accepts 1 MiB; the worker answered an
        # id-less -32700 and the host misreported it as an id mismatch.
        (self.tmp / "worker_hostframe.py").write_text(HOSTCALL_OVERSIZE, encoding="utf-8")
        client = JsonRpcProcessClient(
            WorkerSpec(
                module="worker_hostframe",
                python_executable=sys.executable,
                cwd=str(self.tmp),
                host_handler=_huge_host_handler,
            ),
            max_frame_bytes=4 * 1024 * 1024,
        )
        await client.start()
        result = await client.call("tool.invoke", {}, timeout_seconds=10)
        self.assertEqual("DATA_RESULT_TOO_LARGE", result["code"])
        self.assertLess(result["bytes"], 4096)
        # The stream is still correlated and usable: the worker handled a
        # second request after refusing the oversized response.
        self.assertEqual({"ok": True}, await client.call("tool.invoke", {}, timeout_seconds=10))
        self.assertTrue(client.running)
        await client.close()

    async def test_idless_worker_protocol_error_is_not_an_id_mismatch(self) -> None:
        client = self._client("idless", IDLESS_PROTOCOL_ERROR)
        await client.start()
        with self.assertRaises(RpcCallError) as captured:
            await client.call("system.health", {}, timeout_seconds=5)
        self.assertEqual(-32700, captured.exception.code)
        self.assertIn("host frame", str(captured.exception))
        self.assertNotIn("id mismatch", str(captured.exception))
        await client.close()


def _huge_host_result() -> dict[str, object]:
    return {"blob": "x" * 1_500_000}


async def _huge_host_handler(method: str, params: object) -> dict[str, object]:
    del method, params
    return _huge_host_result()


HOSTCALL_OVERSIZE = """
import json, sys
request = json.loads(sys.stdin.buffer.readline())
sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": "hostcall_1",
    "method": "host.data.execute", "params": {}}) + "\\n")
sys.stdout.flush()
response = json.loads(sys.stdin.buffer.readline())
error = response.get("error") or {}
code = (error.get("data") or {}).get("code")
sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request["id"],
    "result": {"code": code, "bytes": len(response)}}) + "\\n")
sys.stdout.flush()
follow_up = json.loads(sys.stdin.buffer.readline())
sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": follow_up["id"],
    "result": {"ok": True}}) + "\\n")
sys.stdout.flush()
while sys.stdin.buffer.readline():
    pass
"""

IDLESS_PROTOCOL_ERROR = """
import json, sys, time
sys.stdin.buffer.readline()
sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None,
    "error": {"code": -32700, "message": "invalid JSON-RPC frame"}}) + "\\n")
sys.stdout.flush()
time.sleep(60)
"""


if __name__ == "__main__":
    unittest.main()
