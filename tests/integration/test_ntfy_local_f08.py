"""Real loopback HTTP checks; this fixture is not a real ntfy or Android service."""
from __future__ import annotations

import asyncio
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from personal_assistant.infrastructure.memory import InMemorySecretStore
from personal_assistant.infrastructure.notifications.ntfy import LocalNtfyNotifier

TOKEN = "tk_synthetic_loopback_ntfy_token"


class NtfyLocalHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.records: list[dict[str, object]] = []
        self.lose_receipt = False
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *args: object) -> None:
                pass

            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers["Content-Length"]))
                fixture.records.append({"path": self.path, "body": json.loads(body),
                                        "auth": self.headers["Authorization"]})
                if fixture.lose_receipt:
                    self.close_connection = True
                    return
                data = json.dumps({
                    "id": "fixture12345", "time": 1, "event": "message",
                    "topic": "assistant_fixture", "message": body.decode(),
                    "title": self.headers["Title"], "click": self.headers["Click"],
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True,
        )
        self.thread.start()
        secrets = InMemorySecretStore()
        handle = await secrets.put(name="synthetic fixture", kind="ntfy_publish_token", value=TOKEN)
        self.notifier = LocalNtfyNotifier(
            publish_url=f"http://127.0.0.1:{self.server.server_port}/assistant_fixture",
            public_origin="https://assistant.example.test",
            secret_handle_id=handle.id, secrets=secrets,
        )

    async def asyncTearDown(self) -> None:
        await self.notifier.aclose()
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()
        await asyncio.to_thread(self.thread.join, 3)
        self.assertFalse(self.thread.is_alive())
        self.assertTrue(self.notifier._client.is_closed)

    async def test_real_http_bypasses_ambient_proxy_and_accepts_bound_receipt(self) -> None:
        with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}):
            report = await self.notifier.notify_pending(task_id="task_fixture", risk="R2")
        self.assertEqual((1, 0), (report.accepted, report.unknown))
        self.assertEqual([{
            "path": "/assistant_fixture", "auth": f"Bearer {TOKEN}",
            "body": {"kind": "pending", "risk": "R2", "task_id": "task_fixture"},
        }], self.records)

    async def test_server_receives_hint_then_loses_receipt_without_a_second_post(self) -> None:
        self.lose_receipt = True
        report = await self.notifier.notify_pending(task_id="task_fixture", risk="R2")
        self.assertEqual((0, 1), (report.accepted, report.unknown))
        self.assertEqual(1, len(self.records))
        self.assertEqual({"kind": "pending", "risk": "R2", "task_id": "task_fixture"},
                         self.records[0]["body"])
