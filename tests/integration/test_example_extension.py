from __future__ import annotations

import unittest
from pathlib import Path

from example_echo.worker import create_extension
from personal_assistant_sdk.rpc import RpcRequest
from personal_assistant_sdk.worker import ExtensionDispatcher

from personal_assistant.core.extensions.manifest import ManifestParser, compute_schema_hash

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "extensions" / "example_echo"


class ExampleExtensionTests(unittest.IsolatedAsyncioTestCase):
    async def test_handshake_and_tool_call_use_only_sdk_rpc(self) -> None:
        manifest = ManifestParser().parse(EXAMPLE)
        dispatcher = ExtensionDispatcher(create_extension)
        handshake = await dispatcher.dispatch(
            RpcRequest(
                id="1",
                method="system.handshake",
                params={
                    "protocol_version": "1",
                    "runtime": {
                        "protocol_version": "1",
                        "extension_id": manifest.id,
                        "extension_version": manifest.version,
                        "data_namespace": "ext_example_echo",
                        "manifest_schema_hash": compute_schema_hash(manifest),
                        "non_secret_config": {"prefix": "Echo: "},
                        "capability_handles": [],
                    },
                },
            )
        )
        self.assertIsNone(handshake.error)
        self.assertEqual(manifest.id, handshake.result["id"])

        result = await dispatcher.dispatch(
            RpcRequest(
                id="2",
                method="tool.invoke",
                params={
                    "tool_id": "example.echo",
                    "arguments": {"text": "hello"},
                    "context": {
                        "task_id": "task-1",
                        "run_id": "run-1",
                        "deadline": "2099-01-01T00:00:00Z",
                        "idempotency_key": "echo-1",
                        "context_handles": [],
                        "artifact_handles": [],
                        "capability_handles": [],
                    },
                },
            )
        )
        self.assertIsNone(result.error)
        self.assertEqual("Echo: hello", result.result["output"]["echo"])


if __name__ == "__main__":
    unittest.main()

