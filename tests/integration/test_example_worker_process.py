from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from personal_assistant.core.extensions.manifest import ManifestParser
from personal_assistant.core.extensions.rpc import (
    ExtensionWorker,
    JsonRpcProcessClient,
    WorkerSpec,
)

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "extensions" / "example_echo"


class ExampleWorkerProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdio_process_handshake_health_and_invoke(self) -> None:
        manifest = ManifestParser().parse(EXAMPLE)
        python_path = str(EXAMPLE / "src")
        if inherited := os.environ.get("PYTHONPATH"):
            python_path = python_path + os.pathsep + inherited
        client = JsonRpcProcessClient(
            WorkerSpec(
                module=manifest.module_name,
                python_executable=sys.executable,
                cwd=str(ROOT),
                environment={"PYTHONPATH": python_path},
            )
        )
        worker = ExtensionWorker(manifest, client)
        try:
            handshake = await worker.start(non_secret_config={"prefix": "Process: "})
            self.assertEqual("example.echo", handshake["id"])
            self.assertTrue((await worker.health())["healthy"])
            result = await worker.invoke_tool(
                "example.echo",
                {"text": "hello"},
                {
                    "task_id": "task-1",
                    "run_id": "run-1",
                    "deadline": "2099-01-01T00:00:00Z",
                    "idempotency_key": "process-1",
                    "context_handles": [],
                    "artifact_handles": [],
                    "capability_handles": [],
                },
                timeout_seconds=5,
            )
            self.assertEqual("Process: hello", result["output"]["echo"])
        finally:
            await worker.close()


if __name__ == "__main__":
    unittest.main()

