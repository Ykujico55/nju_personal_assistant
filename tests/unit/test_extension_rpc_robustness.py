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

DELAYED_DRAIN = """
import json, sys, time
while True:
    line = sys.stdin.buffer.readline()
    if not line:
        break
    request = json.loads(line)
    delay = 0.4 if request["method"] == "system.drain" else 0.3
    time.sleep(delay)
    sys.stdout.write(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {"drained": True, "active_calls": 0},
            }
        )
        + "\\n"
    )
    sys.stdout.flush()
"""

SLOW_RESPONSES = """
import json, sys, time
while True:
    line = sys.stdin.buffer.readline()
    if not line:
        break
    request = json.loads(line)
    time.sleep(0.4)
    sys.stdout.write(
        json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {}}) + "\\n"
    )
    sys.stdout.flush()
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
    ) -> JsonRpcProcessClient:
        module = f"worker_{name}"
        (self.tmp / f"{module}.py").write_text(script, encoding="utf-8")
        return JsonRpcProcessClient(
            WorkerSpec(
                module=module,
                python_executable=sys.executable,
                cwd=str(self.tmp),
                environment=environment or {},
            ),
            max_frame_bytes=max_frame_bytes,
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
            "exitwrite", EXIT_WHILE_WRITING, max_frame_bytes=8 * 1024 * 1024
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
        client = self._client("slowresponses", SLOW_RESPONSES)
        await client.start()
        first = asyncio.create_task(
            client.call("system.health", {}, timeout_seconds=10)
        )
        await asyncio.sleep(0.05)
        started = time.monotonic()
        with self.assertRaises(RpcTimeoutError):
            await client.call("system.health", {}, timeout_seconds=0.5)
        elapsed = time.monotonic() - started
        # The lock wait plus the response must fit inside the 0.5s budget.
        self.assertLess(elapsed, 0.7, f"call took {elapsed:.3f}s")
        self.assertFalse(client.running)
        with contextlib.suppress(Exception):
            await first
        await client.close()

    async def test_drain_budget_covers_lock_wait_and_response(self) -> None:
        client = self._client("delayed", DELAYED_DRAIN)
        await client.start()
        in_flight = asyncio.create_task(
            client.call("tool.invoke", {}, timeout_seconds=10)
        )
        await asyncio.sleep(0.05)
        budget = 0.5
        deadline = time.time() + budget
        started = time.monotonic()
        with self.assertRaises(RpcTimeoutError):
            await client.drain(deadline, timeout_seconds=10)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, budget + 0.25, f"drain took {elapsed:.3f}s")
        self.assertFalse(client.running)
        with contextlib.suppress(Exception):
            await in_flight
        await client.close()

    async def test_cancellation_during_write_stops_the_worker(self) -> None:
        client = self._client(
            "stallwrite", STALL_AFTER_ONE, max_frame_bytes=8 * 1024 * 1024
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


if __name__ == "__main__":
    unittest.main()
