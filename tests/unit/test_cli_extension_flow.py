"""F02: the CLI confirms, calls the local Admin API and polls operation IDs."""

from __future__ import annotations

import json
import threading
import unittest
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from unittest.mock import patch

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


class CliConnectivityTests(unittest.TestCase):
    url = "http://127.0.0.1:18001"
    recovery = [
        "assistantctl", "--admin-url", url, "extension", "operation", "op-1"
    ]

    def test_install_and_upgrade_offline_before_preview_return_diagnostic(self) -> None:
        for command in (
            ["install", "fixture", "--yes"],
            ["upgrade", "example.echo", "fixture", "--yes"],
        ):
            with self.subTest(command=command), patch(
                "personal_assistant.cli.main._request",
                side_effect=ConnectionRefusedError("offline"),
            ) as request, patch("personal_assistant.cli.main._print") as output:
                self.assertEqual(2, main(["--admin-url", self.url, "extension", *command]))
                self.assertEqual("ADMIN_API_UNREACHABLE", output.call_args.args[0]["error"])
                self.assertEqual(1, request.call_count)

    def test_submission_receipt_loss_never_repeats_write_or_claims_failure(self) -> None:
        for mode in ("install", "upgrade"):
            command = ([mode, "fixture"] if mode == "install"
                       else [mode, "example.echo", "fixture"])
            with self.subTest(mode=mode), patch(
                "personal_assistant.cli.main._request",
                side_effect=[(200, {**PREVIEW, "mode": mode}), TimeoutError("lost receipt")],
            ) as request, patch("personal_assistant.cli.main._print") as output:
                code = main(["--admin-url", self.url, "extension", *command, "--yes"])
                self.assertEqual(2, code)
                diagnostic = output.call_args.args[0]
                self.assertEqual("COMMAND_RESULT_UNCONFIRMED", diagnostic["error"])
                self.assertNotIn("operation_id", diagnostic)
                self.assertEqual(
                    ["assistantctl", "--admin-url", self.url,
                     "extension", "status", "example.echo"],
                    diagnostic["recovery_command"],
                )
                self.assertIn("not resubmit", diagnostic["message"])
                self.assertEqual(2, request.call_count)
                self.assertTrue(all(call.args[1] == "POST" for call in request.call_args_list))

    def test_accepted_operation_poll_disconnect_preserves_read_only_recovery(self) -> None:
        for prior_poll in ([], [(200, {"id": "op-1", "status": "RUNNING"})]):
            with self.subTest(prior_poll=bool(prior_poll)), patch(
                "personal_assistant.cli.main._request",
                side_effect=[
                    (200, PREVIEW),
                    (202, {"id": "op-1", "status": "PENDING"}),
                    *prior_poll, ConnectionResetError("poll disconnected"),
                ],
            ) as request, patch("personal_assistant.cli.main._print") as output, patch(
                "personal_assistant.cli.main.time.sleep"
            ):
                code = main([
                    "--admin-url", self.url, "extension", "install", "fixture", "--yes"
                ])
                self.assertEqual(2, code)
                diagnostic = output.call_args.args[0]
                self.assertEqual("op-1", diagnostic["operation_id"])
                self.assertEqual(self.recovery, diagnostic["recovery_command"])
                self.assertEqual(2, sum(call.args[1] == "POST" for call in request.call_args_list))
                self.assertTrue(all(
                    call.args[1:3] == ("GET", "/admin/v1/extension-operations/op-1")
                    for call in request.call_args_list[2:]
                ))

    def test_lifecycle_write_receipt_loss_is_also_unconfirmed(self) -> None:
        with patch(
            "personal_assistant.cli.main._request", side_effect=TimeoutError("lost receipt")
        ) as request, patch("personal_assistant.cli.main._print") as output:
            self.assertEqual(2, main([
                "--admin-url", self.url, "extension", "disable", "example.echo"
            ]))
            self.assertEqual("COMMAND_RESULT_UNCONFIRMED", output.call_args.args[0]["error"])
            self.assertEqual(1, request.call_count)

    def test_operation_command_queries_once_and_reports_server_status(self) -> None:
        for status in ("PENDING", "RUNNING", "SUCCEEDED", "FAILED"):
            with self.subTest(status=status), patch(
                "personal_assistant.cli.main._request",
                return_value=(200, {"id": "op-1", "status": status}),
            ) as request, patch("personal_assistant.cli.main._print") as output:
                code = main(["--admin-url", self.url, "extension", "operation", "op-1"])
                self.assertEqual(1 if status == "FAILED" else 0, code)
                request.assert_called_once_with(
                    self.url, "GET", "/admin/v1/extension-operations/op-1", None
                )
                self.assertEqual(status, output.call_args.args[0]["status"])

    def test_operation_lookup_and_poll_http_error_retain_identifier(self) -> None:
        from personal_assistant.cli.main import _poll_operation

        body = {"error": {"code": "EXTENSION_OPERATION_NOT_FOUND"}}
        invocations: tuple[Callable[[], int], ...] = (
            lambda: main(["--admin-url", self.url, "extension", "operation", "op-1"]),
            lambda: _poll_operation(self.url, "op-1"),
        )
        for invoke in invocations:
            with self.subTest(invoke=invoke), patch(
                "personal_assistant.cli.main._request", return_value=(404, body)
            ) as request, patch("personal_assistant.cli.main._print") as output:
                self.assertEqual(1, invoke())
                diagnostic = output.call_args.args[0]
                self.assertEqual(body["error"], diagnostic["error"])
                self.assertEqual("op-1", diagnostic["operation_id"])
                self.assertEqual(self.recovery, diagnostic["recovery_command"])
                self.assertEqual(1, request.call_count)
                self.assertEqual("GET", request.call_args.args[1])

    def test_operation_query_offline_preserves_identifier(self) -> None:
        with patch(
            "personal_assistant.cli.main._request", side_effect=ConnectionRefusedError("offline")
        ) as request, patch("personal_assistant.cli.main._print") as output:
            self.assertEqual(2, main([
                "--admin-url", self.url, "extension", "operation", "op-1"
            ]))
            diagnostic = output.call_args.args[0]
            self.assertEqual("op-1", diagnostic["operation_id"])
            self.assertEqual(self.recovery, diagnostic["recovery_command"])
            self.assertEqual(1, request.call_count)

    def test_poll_timeout_retains_query_command_without_starting_another_operation(self) -> None:
        from personal_assistant.cli.main import _poll_operation

        with patch("personal_assistant.cli.main._request") as request, patch(
            "personal_assistant.cli.main._print"
        ) as output:
            self.assertEqual(1, _poll_operation(self.url, "op-1", timeout_seconds=0))
            diagnostic = output.call_args.args[0]
            self.assertEqual("OPERATION_TIMEOUT", diagnostic["error"])
            self.assertEqual("op-1", diagnostic["operation_id"])
            self.assertEqual(self.recovery, diagnostic["recovery_command"])
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
