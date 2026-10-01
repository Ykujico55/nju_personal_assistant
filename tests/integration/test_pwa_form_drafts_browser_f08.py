"""390px F08.3 form, provenance, conflict, and lost-receipt counterexamples."""

from __future__ import annotations

import json
import os
import socket
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

from test_pwa_browser_f08 import NOW, PLAYWRIGHT_AVAILABLE, _Site, _State


def _draft(schema: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "task_id": "task_1",
        "extension_id": "example.echo",
        "extension_version": "1.0.0",
        "form_id": "example.form",
        "json_schema": schema
        or {
            "type": "object",
            "properties": {
                "material": {
                    "type": "string",
                    "title": "材料 <img src=x onerror=window.__injected=true>",
                },
                "evidence": {"type": "string", "title": "已有证据"},
                "unknown": {"type": "string", "title": "待确认"},
            },
            "additionalProperties": False,
        },
        "ui_schema": {"material": {"ui:placeholder": "请输入材料"}},
        "values": {"evidence": "saved evidence"},
        "sources": {"material": "UNKNOWN", "evidence": "EVIDENCE", "unknown": "UNKNOWN"},
        "version": 1,
        "updated_at": NOW,
    }


class _FormState(_State):
    def __init__(self) -> None:
        super().__init__()
        self.draft = _draft()
        self.posts: list[dict[str, object]] = []
        self.receipts: dict[str, dict[str, object]] = {}
        self.drop_next_response = False
        self.incomplete_next_response = False
        self.conflict_status = 409
        self.next_put_status: int | None = None
        self.lock = threading.Lock()
        self.catalog: list[dict[str, object]] = []
        self.create_posts: list[dict[str, object]] = []
        self.create_receipts: dict[str, dict[str, object]] = {}
        self.drop_next_create_response = False
        self.other_form_next_create_response = False


class _FormSite(_Site):
    state: _FormState

    def do_GET(self) -> None:
        if urlsplit(self.path).path == "/api/v1/tasks/task_1/form-draft":
            if self.state.draft is None:
                self._json({"error": {"message": "not found"}}, 404)
            else:
                self._json(self.state.draft)
            return
        if urlsplit(self.path).path == "/api/v1/forms":
            self._json({"items": self.state.catalog})
            return
        super().do_GET()

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/api/v1/tasks/task_1/form-draft":
            super().do_POST()
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        key = self.headers["Idempotency-Key"]
        with self.state.lock:
            self.state.create_posts.append({"key": key, **body})
            if key in self.state.create_receipts:
                receipt = self.state.create_receipts[key]
            elif self.state.draft is not None:
                self._json({"error": {"code": "CONCURRENT_MODIFICATION"}}, 409)
                return
            else:
                self.state.draft = _draft()
                self.state.draft["values"] = {}
                self.state.draft["sources"] = {
                    name: "UNKNOWN" for name in self.state.draft["json_schema"]["properties"]
                }
                receipt = dict(self.state.draft)
                self.state.create_receipts[key] = receipt
        if self.state.drop_next_create_response:
            self.state.drop_next_create_response = False
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        if self.state.other_form_next_create_response:
            self.state.other_form_next_create_response = False
            self._json({**receipt, "form_id": "example.other"}, 201)
            return
        self._json(receipt, 201)

    def do_PUT(self) -> None:
        if urlsplit(self.path).path != "/api/v1/tasks/task_1/form-draft":
            self.send_error(404)
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        key = self.headers["Idempotency-Key"]
        if self.state.next_put_status is not None:
            status = self.state.next_put_status
            self.state.next_put_status = None
            self._json({"error": {"message": "invalid value"}}, status)
            return
        with self.state.lock:
            self.state.posts.append({"key": key, **body})
            if key in self.state.receipts:
                receipt = self.state.receipts[key]
            elif body["version"] != self.state.draft["version"]:
                self._json(
                    {"error": {"code": "CONCURRENT_MODIFICATION", "message": "stale"}},
                    self.state.conflict_status,
                )
                return
            else:
                self.state.draft = {
                    **self.state.draft,
                    "version": int(self.state.draft["version"]) + 1,
                    "values": body["values"],
                    "sources": {
                        key: "USER_INPUT" if key in body["values"] else "UNKNOWN"
                        for key in self.state.draft["sources"]
                    },
                }
                receipt = dict(self.state.draft)
                self.state.receipts[key] = receipt
        if self.state.drop_next_response:
            self.state.drop_next_response = False
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        if self.state.incomplete_next_response:
            self.state.incomplete_next_response = False
            self._json({})
            return
        self._json(receipt)


@unittest.skipUnless(PLAYWRIGHT_AVAILABLE, "install the optional browser extra")
class PwaFormDraftBrowserTests(unittest.TestCase):
    def _run(self, state: _FormState, callback: object) -> None:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        handler = type("FormHandler", (_FormSite,), {"state": state})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True)
                except PlaywrightError as exc:
                    if os.name != "nt" or "spawn EFTYPE" not in str(exc):
                        raise
                    browser = playwright.chromium.launch(headless=True, channel="chrome")
                try:
                    context = browser.new_context(viewport={"width": 390, "height": 844})
                    page = context.new_page()
                    page.goto(f"http://127.0.0.1:{server.server_port}/ui/")
                    page.locator('#task-list button[data-task-id="task_1"]').click()
                    callback(page, context)
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_schema_provenance_injection_and_unsupported_feature(self) -> None:
        from playwright.sync_api import expect

        state = _FormState()

        def check(page: object, _context: object) -> None:
            form = page.locator("#form-draft")
            expect(form.locator('[data-field="evidence"] .field-source')).to_contain_text(
                "已有证据"
            )
            expect(form.locator('[data-field="unknown"] .field-source')).to_contain_text("未确定")
            expect(form.locator('[data-field="material"] label')).to_contain_text("<img")
            self.assertEqual(0, form.locator("img").count())
            self.assertIsNone(page.evaluate("window.__injected"))
            state.draft = _draft({**state.draft["json_schema"], "$ref": "https://evil.invalid/a.js"})
            page.reload()
            page.locator('#task-list button[data-task-id="task_1"]').click()
            expect(page.locator("#form-draft-status")).to_contain_text("不支持")
            self.assertEqual(0, page.locator("#form-draft input").count())
            state.draft = _draft()
            state.draft["ui_schema"] = {"material": {"ui:widget": "javascript"}}
            page.reload()
            page.locator('#task-list button[data-task-id="task_1"]').click()
            expect(page.locator("#form-draft-status")).to_contain_text("不支持")
            self.assertEqual(0, page.locator("#form-draft input").count())

        self._run(state, check)

    def test_catalog_creates_once_after_lost_response_and_rejects_bad_schema(self) -> None:
        from playwright.sync_api import expect

        state = _FormState()
        state.draft = None
        valid = _draft()
        valid["values"] = {}
        state.catalog = [
            {
                "extension_id": "example.echo",
                "extension_version": "1.0.0",
                "id": "example.form",
                "json_schema": valid["json_schema"],
                "ui_schema": valid["ui_schema"],
            },
            {
                "extension_id": "example.echo",
                "extension_version": "1.0.0",
                "id": "example.unsupported",
                "json_schema": {**valid["json_schema"], "$ref": "javascript:alert(1)"},
                "ui_schema": {},
            },
        ]

        def check(page: object, _context: object) -> None:
            expect(page.locator("#form-draft-status")).to_contain_text("Schema 不支持")
            expect(page.locator("#form-draft-selector option:disabled")).to_have_count(1)
            state.drop_next_create_response = True
            page.locator('#form-draft-body button:has-text("开始此 Schema")').click()
            expect(page.locator("#form-draft-status")).to_contain_text("结果未确认")
            self.assertEqual(1, len(state.create_posts))
            page.locator('#form-draft-body button:has-text("重试同一创建请求")').click()
            expect(page.locator("#form-draft-status")).to_contain_text("已创建")
            self.assertEqual(2, len(state.create_posts))
            self.assertEqual(state.create_posts[0]["key"], state.create_posts[1]["key"])
            expect(page.locator('[data-field="material"] input')).to_have_count(1)

        self._run(state, check)

    def test_complete_receipt_for_other_form_keeps_original_create_key(self) -> None:
        from playwright.sync_api import expect

        state = _FormState()
        state.draft = None
        form = _draft()
        state.catalog = [{
            "extension_id": form["extension_id"],
            "extension_version": form["extension_version"],
            "id": form["form_id"],
            "json_schema": form["json_schema"],
            "ui_schema": form["ui_schema"],
        }]

        def check(page: object, _context: object) -> None:
            state.other_form_next_create_response = True
            page.locator('#form-draft-body button:has-text("开始此 Schema")').click()
            expect(page.locator("#form-draft-status")).to_contain_text("结果未确认")
            self.assertEqual(1, len(state.create_receipts))
            page.locator('#form-draft-body button:has-text("重试同一创建请求")').click()
            expect(page.locator("#form-draft-status")).to_contain_text("已创建")
            self.assertEqual(1, len(state.create_receipts))
            self.assertEqual(2, len(state.create_posts))
            self.assertEqual(state.create_posts[0]["key"], state.create_posts[1]["key"])
            expect(page.locator('[data-field="material"] input')).to_have_count(1)

        self._run(state, check)

    def test_boolean_unknown_is_not_silently_saved_as_false(self) -> None:
        from playwright.sync_api import expect

        state = _FormState()
        schema = state.draft["json_schema"]
        state.draft["json_schema"] = {
            **schema,
            "properties": {**schema["properties"], "confirmed": {"type": "boolean"}},
        }
        state.draft["sources"] = {**state.draft["sources"], "confirmed": "UNKNOWN"}

        def check(page: object, _context: object) -> None:
            choice = page.locator('[data-field="confirmed"] select')
            expect(choice).to_have_value("")
            expect(page.locator('[data-field="confirmed"] .field-source')).to_contain_text(
                "未确定"
            )
            choice.select_option('false')
            page.locator('#form-draft button[type="submit"]').click()
            expect(page.locator("#form-draft-status")).to_contain_text("已保存")
            self.assertIs(False, state.draft["values"]["confirmed"])
            self.assertEqual("USER_INPUT", state.draft["sources"]["confirmed"])

        self._run(state, check)

    def test_conflict_lost_receipt_retry_refresh_and_static_cache(self) -> None:
        from playwright.sync_api import expect

        state = _FormState()

        def check(page: object, context: object) -> None:
            form = page.locator("#form-draft")
            material = form.locator('[data-field="material"] input')
            material.fill("phone private answer")
            state.draft = {**state.draft, "version": 2, "values": {"material": "remote"}}
            form.locator('button[type="submit"]').click()
            expect(page.locator("#form-draft-status")).to_contain_text("版本冲突")
            expect(material).to_have_value("phone private answer")
            expect(page.locator("#form-draft-server-version")).to_contain_text("2")
            self.assertEqual(1, len(state.posts))
            page.locator("#form-draft-use-version").click()
            state.drop_next_response = True
            form.locator('button[type="submit"]').click()
            expect(page.locator("#form-draft-status")).to_contain_text("结果未确认")
            expect(material).to_have_value("phone private answer")
            expect(material).to_have_attribute("readonly", "")
            form.locator('button[type="submit"]').click()
            expect(page.locator("#form-draft-status")).to_contain_text("已保存")
            self.assertEqual(state.posts[1]["key"], state.posts[2]["key"])
            self.assertEqual(3, state.draft["version"])
            material.fill("second private answer")
            state.incomplete_next_response = True
            form.locator('button[type="submit"]').click()
            expect(page.locator("#form-draft-status")).to_contain_text("结果未确认")
            expect(material).to_have_value("second private answer")
            form.locator('button[type="submit"]').click()
            expect(page.locator("#form-draft-status")).to_contain_text("已保存")
            self.assertEqual(state.posts[3]["key"], state.posts[4]["key"])
            self.assertEqual(4, state.draft["version"])
            page.reload()
            page.locator('#task-list button[data-task-id="task_1"]').click()
            expect(page.locator('[data-field="material"] input')).to_have_value(
                "second private answer"
            )
            expect(page.locator('[data-field="material"] .field-source')).to_contain_text(
                "用户输入"
            )
            page.evaluate("async () => { await navigator.serviceWorker.ready; return true; }")
            cached = page.evaluate(
                "async () => { const urls = []; for (const key of await caches.keys()) "
                "{ const cache = await caches.open(key); for (const request of await cache.keys()) "
                "urls.push(request.url); } return urls; }"
            )
            self.assertTrue(cached)
            self.assertTrue(all("/api/" not in url for url in cached))
            cached_bodies = page.evaluate(
                "async () => { const bodies = []; for (const key of await caches.keys()) "
                "{ const cache = await caches.open(key); for (const request of await cache.keys()) "
                "bodies.push(await (await cache.match(request)).text()); } "
                "return bodies.join('\\n'); }"
            )
            self.assertNotIn("second private answer", cached_bodies)
            self.assertNotIn("saved evidence", cached_bodies)
            context.set_offline(True)
            page.locator('[data-field="material"] input').fill("offline secret")
            form.locator('button[type="submit"]').click()
            expect(page.locator("#form-draft-status")).to_contain_text("离线")
            self.assertEqual(4, state.draft["version"])

        self._run(state, check)

    def test_precondition_412_preserves_local_input_until_explicit_choice(self) -> None:
        from playwright.sync_api import expect

        state = _FormState()
        state.conflict_status = 412

        def check(page: object, _context: object) -> None:
            material = page.locator('[data-field="material"] input')
            material.fill("phone answer")
            state.draft = {**state.draft, "version": 2, "values": {"material": "worker answer"}}
            page.locator('#form-draft button[type="submit"]').click()
            expect(page.locator("#form-draft-status")).to_contain_text("版本冲突")
            expect(material).to_have_value("phone answer")
            expect(page.locator("#form-draft-server-version")).to_contain_text("2")
            self.assertEqual(1, len(state.posts))
            page.locator('#form-draft-conflict button:has-text("使用服务器草稿")').click()
            expect(material).to_have_value("worker answer")
            self.assertEqual(1, len(state.posts))

        self._run(state, check)

    def test_validation_error_keeps_unsaved_draft_editable(self) -> None:
        from playwright.sync_api import expect

        state = _FormState()

        def check(page: object, _context: object) -> None:
            material = page.locator('[data-field="material"] input')
            material.fill("answer to correct")
            state.next_put_status = 422
            page.locator('#form-draft button[type="submit"]').click()
            expect(page.locator("#form-draft-status")).to_contain_text("未保存")
            expect(material).to_have_value("answer to correct")
            expect(material).not_to_have_attribute("readonly", "")
            material.fill("corrected answer")
            page.locator('#form-draft button[type="submit"]').click()
            expect(page.locator("#form-draft-status")).to_contain_text("已保存")
            self.assertEqual("corrected answer", state.draft["values"]["material"])

        self._run(state, check)
