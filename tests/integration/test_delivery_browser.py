from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

from tests.integration.test_pwa_browser_f08 import PLAYWRIGHT_AVAILABLE, _Site, _State


@unittest.skipUnless(PLAYWRIGHT_AVAILABLE, "browser extra unavailable")
class DeliveryBrowserTests(unittest.TestCase):
    def test_390px_execution_receipt_loss_keeps_input_and_same_key(self) -> None:
        from playwright.sync_api import Error, expect, sync_playwright

        state = _State()
        commands, posts = {}, []

        class Site(_Site):
            def do_GET(self):
                path = urlsplit(self.path).path
                if path == "/api/v1/system":
                    self._json(
                        {
                            "model_configured": False,
                            "worker_online": True,
                            "notification_configured": False,
                            "notification_channel": "ntfy",
                        }
                    )
                elif path == "/api/v1/workflows":
                    self._json(
                        {
                            "items": [
                                {
                                    "id": "fixture.flow",
                                    "version": "1",
                                    "extension_id": "fixture",
                                    "extension_version": "1",
                                    "supported": True,
                                    "input_schema": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "properties": {
                                            "answer": {"type": "string", "title": "材料"}
                                        },
                                    },
                                    "steps": [],
                                }
                            ]
                        }
                    )
                elif path.endswith("/execution"):
                    self._json(
                        {
                            "task_id": "task_1",
                            "task_version": 1,
                            "state": "QUEUED",
                            "execution": None,
                            "pending_input": None,
                            "workflow": None,
                        }
                    )
                elif path == "/api/v1/rules":
                    self._json({"items": []})
                else:
                    super().do_GET()

            def do_POST(self):
                if not urlsplit(self.path).path.endswith("/execution"):
                    return super().do_POST()
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                key = self.headers["Idempotency-Key"]
                posts.append({"key": key, "body": body})
                commands.setdefault(
                    key,
                    {
                        "task_id": "task_1",
                        "job_id": "job_fixture",
                        "accepted": True,
                        "command": body,
                    },
                )
                self._json({} if len(posts) == 1 else commands[key], 202)

        Site.state = state
        server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True)
                except Error as error:
                    if os.name != "nt" or "spawn EFTYPE" not in str(error):
                        raise
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                try:
                    page = browser.new_page(viewport={"width": 390, "height": 844})
                    page.goto(f"http://127.0.0.1:{server.server_port}/ui/")
                    page.locator("#task-list button").filter(has_text="first task").click()
                    expect(page.locator("#execution-workflow")).to_be_visible()
                    page.locator("#execution-workflow").select_option(label="fixture.flow · 1")
                    page.locator("#execution-fields [name=answer]").fill(
                        "<img src=x onerror=window.__injected=true>"
                    )
                    page.locator("#execution-submit").click()
                    expect(page.locator("#execution-status")).to_contain_text("未确认")
                    expect(page.locator("#execution-fields [name=answer]")).to_have_value(
                        "<img src=x onerror=window.__injected=true>"
                    )
                    expect(page.locator("#execution-submit")).to_contain_text("重试同一")
                    page.locator("#execution-submit").click()
                    expect(page.locator("#execution-status")).to_contain_text("请求已确认")
                    self.assertEqual(1, len(commands))
                    self.assertEqual(posts[0]["key"], posts[1]["key"])
                    self.assertIsNone(page.evaluate("window.__injected"))
                    self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)
            self.assertFalse(thread.is_alive())

    def test_390px_structured_workflow_material_is_json_not_executable_html(self):
        from playwright.sync_api import Error, expect, sync_playwright

        class Site(_Site):
            state = _State()

            def do_GET(self):
                path = urlsplit(self.path).path
                if path == "/api/v1/workflows":
                    self._json({"items": []})
                elif path.endswith("/execution"):
                    task = path.split("/")[-2]
                    self._json(
                        {
                            "task_id": task,
                            "task_version": 1,
                            "state": "WAITING_USER",
                            "execution": None,
                            "workflow": None,
                            "pending_input": {
                                "field": "values",
                                "question": "结构化材料 JSON",
                                "schema": {"type": "object"},
                            },
                        }
                    )
                elif path == "/api/v1/rules":
                    self._json({"items": []})
                else:
                    super().do_GET()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                try:
                    browser = p.chromium.launch(headless=True)
                except Error as error:
                    if os.name != "nt" or "spawn EFTYPE" not in str(error):
                        raise
                    browser = p.chromium.launch(headless=True, channel="chrome")
                try:
                    page = browser.new_page(viewport={"width": 390, "height": 844})
                    page.goto(f"http://127.0.0.1:{server.server_port}/ui/")
                    field = page.locator("#execution-fields textarea[name=values]")
                    expect(field).to_be_visible()
                    field.fill('{"material":"<img src=x onerror=window.__injected=true>"}')
                    self.assertIsNone(page.evaluate("window.__injected"))
                    self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)
            self.assertFalse(thread.is_alive())
