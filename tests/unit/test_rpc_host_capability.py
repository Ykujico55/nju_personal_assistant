"""F05: full-duplex RPC so workers can call generic host capabilities."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from personal_assistant.core.extensions.errors import (
    ExtensionOperationError,
    RpcCallError,
)
from personal_assistant.core.extensions.rpc import JsonRpcProcessClient, WorkerSpec

HOST_CALLER = """
import json, sys

def send(body):
    sys.stdout.write(json.dumps(body) + "\\n")
    sys.stdout.flush()

while True:
    line = sys.stdin.buffer.readline()
    if not line:
        break
    request = json.loads(line)
    method = request.get("method")
    if method == "worker.host":
        send({
            "jsonrpc": "2.0",
            "id": "hc-1",
            "method": "host.data.execute",
            "params": {"statement": "SELECT 1", "parameters": []},
        })
        reply = None
        while reply is None or reply.get("id") != "hc-1":
            frame = sys.stdin.buffer.readline()
            if not frame:
                sys.exit(0)
            reply = json.loads(frame)
        result = reply.get("result")
        error = reply.get("error")
        send({"jsonrpc": "2.0", "id": request["id"], "result": {
            "result": result, "error": error,
        }})
    elif method == "worker.bad-id":
        send({"jsonrpc": "2.0", "id": "unknown-host-response", "result": {}})
    else:
        send({"jsonrpc": "2.0", "id": request["id"], "result": {"pong": True}})
"""


class HostCapabilityRpcTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa_f05_duplex_"))

    def tearDown(self) -> None:
        resolved = self.tmp.resolve()
        if not resolved.is_relative_to(Path(tempfile.gettempdir()).resolve()):
            raise AssertionError(f"refusing to delete {resolved}")
        shutil.rmtree(resolved, ignore_errors=True)

    def _client(self, handler: Any = None) -> JsonRpcProcessClient:
        module = "worker_host_caller"
        (self.tmp / f"{module}.py").write_text(HOST_CALLER, encoding="utf-8")
        return JsonRpcProcessClient(
            WorkerSpec(
                module=module,
                python_executable=sys.executable,
                cwd=str(self.tmp),
                host_handler=handler,
            )
        )

    async def test_worker_host_request_is_served_by_the_handler(self) -> None:
        seen: list[tuple[str, Mapping[str, Any]]] = []

        async def handler(method: str, params: Mapping[str, Any]) -> Any:
            seen.append((method, params))
            return {"rows": [{"value": 1}], "rowcount": 1}

        client = self._client(handler)
        await client.start()
        result = await client.call("worker.host", {}, timeout_seconds=10)
        await client.close()
        self.assertEqual("host.data.execute", seen[0][0])
        self.assertEqual({"rows": [{"value": 1}], "rowcount": 1}, result["result"])
        self.assertIsNone(result["error"])

    async def test_missing_handler_fails_closed_without_breaking_the_stream(self) -> None:
        client = self._client(None)
        await client.start()
        result = await client.call("worker.host", {}, timeout_seconds=10)
        self.assertIsNone(result["result"])
        self.assertEqual("DATA_UNAVAILABLE", result["error"]["data"]["code"])
        # The stream stays usable after a rejected host request.
        self.assertEqual({"pong": True}, await client.call("ping", {}, timeout_seconds=10))
        await client.close()

    async def test_handler_error_maps_to_a_semantic_code(self) -> None:
        async def handler(method: str, params: Mapping[str, Any]) -> Any:
            del method, params
            raise ExtensionOperationError("DATA_STATEMENT_REJECTED", "rejected")

        client = self._client(handler)
        await client.start()
        result = await client.call("worker.host", {}, timeout_seconds=10)
        await client.close()
        self.assertIsNone(result["result"])
        self.assertEqual("DATA_STATEMENT_REJECTED", result["error"]["data"]["code"])

    async def test_unknown_response_id_breaks_the_stream(self) -> None:
        client = self._client(None)
        await client.start()
        with self.assertRaises(RpcCallError) as captured:
            await client.call("worker.bad-id", {}, timeout_seconds=10)
        self.assertEqual(-32093, captured.exception.code)
        self.assertFalse(client.running)
        await client.close()


if __name__ == "__main__":
    unittest.main()
