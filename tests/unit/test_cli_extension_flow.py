"""F02: the CLI confirms, calls the local Admin API and polls operation IDs."""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from personal_assistant.cli.main import main

PREVIEW = {
    "plan_id": "install_plan-1",
    "confirmation_nonce": "nonce-1",
    "preview_hash": "sha256:" + "a" * 64,
    "expires_at": "2026-09-16T12:30:00+00:00",
    "mode": "install",
    "extension_id": "example.echo",
    "extension_name": "Example Echo",
    "extension_version": "0.1.0",
    "artifact_hash": "sha256:" + "b" * 64,
    "slots": {"ToolProvider": ["example.echo"]},
    "tool_risks": {"example.echo": "READ"},
    "required_capabilities": [],
    "optional_capabilities": [],
    "warning": "install means you trust the code",
    "executed_code": False,
}


class _StubAdmin(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []

    def log_message(self, *args: Any) -> None:  # silence the default stderr logging
        del args

    def _reply(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        type(self).requests.append({"method": "POST", "path": self.path, "body": payload})
        if self.path == "/admin/v1/extensions/inspect":
            self._reply(200, PREVIEW)
        elif self.path == "/admin/v1/extensions/install":
            self._reply(
                202,
                {"id": "op-1", "status": "PENDING", "extension_id": "example.echo"},
            )
        elif self.path == "/admin/v1/extension-plans/install_plan-1/reject":
            self._reply(200, {"id": "example.echo", "state": "REJECTED"})
        else:
            self._reply(404, {"error": {"code": "NOT_FOUND", "message": self.path}})

    def do_GET(self) -> None:
        type(self).requests.append({"method": "GET", "path": self.path, "body": {}})
        if self.path == "/admin/v1/extension-operations/op-1":
            polls = sum(1 for item in type(self).requests if item["path"] == self.path)
            status = "RUNNING" if polls < 2 else "SUCCEEDED"
            self._reply(200, {"id": "op-1", "status": status, "operation": "install"})
        else:
            self._reply(404, {"error": {"code": "NOT_FOUND", "message": self.path}})


class CliExtensionFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        _StubAdmin.requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _StubAdmin)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_install_confirms_then_polls_the_operation(self) -> None:
        code = main(
            ["--admin-url", self.url, "extension", "install", "extensions/example_echo", "--yes"]
        )
        self.assertEqual(0, code)
        paths = [item["path"] for item in _StubAdmin.requests]
        self.assertEqual("/admin/v1/extensions/inspect", paths[0])
        self.assertEqual("/admin/v1/extensions/install", paths[1])
        self.assertTrue(paths[2].startswith("/admin/v1/extension-operations/"))
        confirm = _StubAdmin.requests[1]["body"]
        self.assertEqual("install_plan-1", confirm["plan_id"])
        self.assertEqual("nonce-1", confirm["confirmation_nonce"])
        self.assertEqual(PREVIEW["preview_hash"], confirm["preview_hash"])
        self.assertTrue(confirm["accepted_warning"])
        self.assertGreaterEqual(len(paths), 3)

    def test_install_declined_by_user_executes_nothing(self) -> None:
        # Without --yes the CLI asks for explicit confirmation; an empty answer aborts.
        import io
        import sys

        original = sys.stdin
        sys.stdin = io.StringIO("\n")
        try:
            code = main(
                ["--admin-url", self.url, "extension", "install", "extensions/example_echo"]
            )
        finally:
            sys.stdin = original
        self.assertEqual(1, code)
        paths = [item["path"] for item in _StubAdmin.requests]
        self.assertEqual(
            ["/admin/v1/extensions/inspect", "/admin/v1/extension-plans/install_plan-1/reject"],
            paths,
        )


if __name__ == "__main__":
    unittest.main()
