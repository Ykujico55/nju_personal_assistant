"""Actual Admin HTML/JS in an isolated browser; all network requests are routed fixtures."""

from __future__ import annotations

import importlib.util
import json
import os
import unittest
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
AVAILABLE = importlib.util.find_spec("playwright") is not None
ORIGIN = "http://127.0.0.1:18111"


@unittest.skipUnless(AVAILABLE, "browser extra unavailable")
class AdminRecoveryBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright

        cls.playwright = sync_playwright().start()
        channel = os.getenv("PA_TEST_BROWSER_CHANNEL", "msedge" if os.name == "nt" else "")
        try:
            cls.browser = cls.playwright.chromium.launch(
                headless=True, **({"channel": channel} if channel else {})
            )
        except BaseException:
            cls.playwright.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.set_default_timeout(5000)
        self.posts = []
        self.gets = []
        self.config = {"prefix": "old"}
        self.mode = "upgrade"
        self.inspect_failure = False
        self.drop_config = False
        self.config_get_failure = False
        self.hold_config = False
        self.held = None
        self.addCleanup(self.abort_held)
        self.hold_install = False
        self.install_response = None
        self.extension_state = "DISABLED"
        self.drop_enable = False
        self.context.route("**/*", self.route)
        self.page.goto(ORIGIN + "/admin/ui/")
        self.page.wait_for_function("document.querySelector('#extensions').children.length === 1")

    def abort_held(self):
        if self.held is not None:
            route, self.held = self.held, None
            route.abort("connectionreset")

    def hold(self, route):
        self.held = route
        self.page.evaluate("window.__adminFixtureHeld = true")

    def reply(self, route, value, status=200):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(value))

    def route(self, route):
        request = route.request
        parsed = urlsplit(request.url)
        if parsed.netloc != "127.0.0.1:18111":
            route.abort()
            return
        path = parsed.path
        if path.startswith("/admin/ui/"):
            name = path.rsplit("/", 1)[-1] or "index.html"
            mime = {
                "index.html": "text/html",
                "app.js": "application/javascript",
                "styles.css": "text/css",
            }
            if name in mime:
                route.fulfill(
                    content_type=mime[name],
                    body=(ROOT / "web/admin" / name).read_text(encoding="utf-8"),
                )
            else:
                route.fulfill(status=404, body="")
            return
        if request.method == "GET":
            self.gets.append(path)
            if path == "/admin/v1/host":
                self.reply(route, {"frozen": False})
            elif path == "/admin/v1/extensions":
                self.reply(
                    route,
                    {"items": [{"id": "fixture", "version": "1", "state": self.extension_state}]},
                )
            elif path == "/admin/v1/schedules":
                self.reply(route, {"items": []})
            elif path.endswith("/config"):
                if self.config_get_failure:
                    self.reply(route, {"error": {"message": "fixture read unavailable"}}, 503)
                    return
                self.reply(
                    route,
                    {
                        "extension_id": path.split("/")[-2],
                        "config": self.config,
                        "config_schema": {"type": "object"},
                    },
                )
            else:
                self.reply(route, {}, 404)
            return
        body = request.post_data_json
        self.posts.append(
            {
                "path": path,
                "method": request.method,
                "body": body,
                "key": request.headers.get("idempotency-key"),
            }
        )
        if path.startswith("/admin/v1/host/"):
            self.reply(route, {"frozen": path.endswith("freeze")})
        elif path == "/admin/v1/extensions/inspect":
            if self.inspect_failure:
                self.reply(route, {"error": {"message": "fixture inspect failed"}}, 422)
            else:
                self.reply(
                    route,
                    {
                        "mode": self.mode,
                        "extension_id": "fixture",
                        "plan_id": "plan-1",
                        "confirmation_nonce": "nonce-1",
                        "preview_hash": "hash-1",
                        "artifact_hash": "artifact-1",
                    },
                )
        elif request.method == "PUT":
            self.config = body["config"]
            if self.hold_config:
                self.hold(route)
            elif self.drop_config:
                self.drop_config = False
                route.abort("connectionreset")
            else:
                self.reply(route, {"extension_id": "fixture", "config": self.config})
        elif path.endswith("/enable"):
            self.extension_state = "ENABLED"
            if self.drop_enable:
                self.drop_enable = False
                route.abort("connectionreset")
            else:
                self.reply(
                    route,
                    {
                        "id": "op-enable",
                        "extension_id": "fixture",
                        "operation": "enable",
                        "status": "PENDING",
                    },
                )
        elif self.hold_install:
            self.hold(route)
        elif self.install_response is not None:
            status, response = self.install_response
            self.reply(route, response, status)
        else:
            self.reply(
                route,
                {
                    "id": "op-install",
                    "extension_id": "fixture",
                    "operation": path.rsplit("/", 1)[-1],
                    "status": "PENDING",
                },
            )

    def inspect(self):
        self.page.locator("#source").fill("fixture/source")
        self.page.locator("#inspect button").click()
        self.page.wait_for_function(
            "document.querySelector('#preview').textContent.includes('plan-1')"
        )
        self.page.locator("#trust").check()

    def load_config(self):
        self.page.locator("#extension-id").fill("fixture")
        self.page.locator("#config-form button").click()
        self.page.wait_for_function("document.querySelector('#config-json').value.includes('old')")

    def test_host_buttons_reusable_and_dynamic_unknown_survives_refresh(self):
        from playwright.sync_api import expect

        for name in ("freeze", "resume", "freeze"):
            self.page.locator("#" + name).click()
            expect(self.page.locator("#" + name)).to_be_enabled()
        freezes = [p for p in self.posts if p["path"].endswith("/freeze")]
        self.assertEqual(2, len(freezes))
        self.assertNotEqual(freezes[0]["key"], freezes[1]["key"])
        self.drop_enable = True
        self.page.locator("#extensions button").filter(has_text="enable").click()
        expect(self.page.locator("#status")).to_contain_text("Failed")
        self.page.locator("#refresh").click()
        expect(
            self.page.locator("#extensions").get_by_role("button", name="disable", exact=True)
        ).to_be_visible()
        self.page.locator("#pending-commands button").click()
        self.page.wait_for_function("document.querySelector('#pending-commands').hidden")
        enables = [p for p in self.posts if p["path"].endswith("/enable")]
        self.assertEqual(2, len(enables))
        self.assertEqual(enables[0]["key"], enables[1]["key"])

    def test_upgrade_route_is_bound_and_pending_controls_cannot_reopen(self):
        from playwright.sync_api import expect

        self.inspect()
        self.hold_install = True
        self.page.locator("#install").click()
        self.page.wait_for_function("window.__adminFixtureHeld === true")
        self.assertEqual("/admin/v1/extensions/fixture/upgrade", self.posts[-1]["path"])
        self.page.locator("#trust").dispatch_event("change")
        expect(self.page.locator("#install")).to_be_disabled()
        self.page.locator("#inspect").evaluate(
            "f => f.dispatchEvent(new Event('submit', {bubbles:true,cancelable:true}))"
        )
        self.assertEqual(1, sum(p["path"].endswith("inspect") for p in self.posts))
        self.abort_held()
        expect(self.page.locator("#install")).to_be_enabled()
        self.hold_install = False
        self.page.locator("#refresh").click()
        self.page.locator("#install").click()
        expect(self.page.locator("#status")).to_contain_text("已确认")
        attempts = [p for p in self.posts if p["path"].endswith("/upgrade")]
        self.assertEqual(2, len(attempts))
        self.assertEqual(attempts[0], attempts[1])

    def test_new_failed_inspect_invalidates_previous_plan_and_trust(self):
        from playwright.sync_api import expect

        self.inspect()
        self.inspect_failure = True
        self.page.locator("#source").fill("other/source")
        self.page.locator("#inspect button").click()
        expect(self.page.locator("#status")).to_contain_text("inspect failed")
        expect(self.page.locator("#trust")).not_to_be_checked()
        expect(self.page.locator("#install")).to_be_disabled()
        expect(self.page.locator("#preview")).to_have_text("")

    def test_config_unknown_is_bound_to_original_id_and_preserves_later_edits(self):
        from playwright.sync_api import expect

        expect(self.page.locator("#save-config")).to_be_disabled()
        self.load_config()
        self.page.locator("#config-json").fill('{"prefix":"sent"}')
        self.drop_config = True
        self.page.locator("#save-config").click()
        expect(self.page.locator("#config-status")).to_contain_text("未确认")
        self.page.locator("#config-json").fill('{"prefix":"later"}')
        self.page.locator("#extension-id").fill("other")
        self.page.locator("#refresh").click()
        self.page.locator("#save-config").dispatch_event("click")
        self.config_get_failure = True
        self.page.locator("#reconcile-config").click()
        expect(self.page.locator("#config-status")).to_contain_text("仍未确认")
        expect(self.page.locator("#save-config")).to_be_disabled()
        self.config_get_failure = False
        self.page.locator("#reconcile-config").click()
        expect(self.page.locator("#config-status")).to_contain_text("当前配置与原请求相符")
        self.assertEqual("/admin/v1/extensions/fixture/config", self.gets[-1])
        expect(self.page.locator("#config-json")).to_have_value('{"prefix":"later"}')
        expect(self.page.locator("#extension-id")).to_have_value("other")
        self.assertEqual(1, sum(p["method"] == "PUT" for p in self.posts))
        expect(self.page.locator("#save-config")).to_be_disabled()
        self.assertEqual([0, 0], self.page.evaluate("[localStorage.length,sessionStorage.length]"))

    def test_config_different_requires_explicit_resolution_without_write(self):
        from playwright.sync_api import expect

        self.load_config()
        self.page.locator("#config-json").fill('{"prefix":"sent"}')
        self.drop_config = True
        self.page.locator("#save-config").click()
        expect(self.page.locator("#config-status")).to_contain_text("未确认")
        self.config = {"prefix": "another-device"}
        self.page.locator("#config-json").fill('{"prefix":"local"}')
        self.page.locator("#config-form button").click()
        expect(self.page.locator("#config-conflict")).to_be_visible()
        expect(self.page.locator("#save-config")).to_be_disabled()
        self.page.locator("#keep-local-config").click()
        expect(self.page.locator("#config-json")).to_have_value('{"prefix":"local"}')
        expect(self.page.locator("#save-config")).to_be_enabled()
        self.assertEqual(1, sum(p["method"] == "PUT" for p in self.posts))
        self.drop_config = True
        self.page.locator("#save-config").click()
        expect(self.page.locator("#config-status")).to_contain_text("未确认")
        self.config = {"prefix": "server"}
        self.page.locator("#reconcile-config").click()
        self.page.locator("#use-server-config").click()
        self.assertEqual(self.config, json.loads(self.page.locator("#config-json").input_value()))
        self.assertEqual(2, sum(p["method"] == "PUT" for p in self.posts))

    def test_pending_put_and_refresh_do_not_duplicate_or_discard_attempt(self):
        from playwright.sync_api import expect

        self.load_config()
        self.page.locator("#config-json").fill('{"prefix":"pending"}')
        self.hold_config = True
        self.page.locator("#save-config").click()
        self.page.wait_for_function("window.__adminFixtureHeld === true")
        expect(self.page.locator("#save-config")).to_be_disabled()
        self.page.locator("#refresh").click()
        self.page.locator("#save-config").dispatch_event("click")
        self.page.locator("#config-form").evaluate(
            "f => f.dispatchEvent(new Event('submit', {bubbles:true,cancelable:true}))"
        )
        self.assertEqual(1, sum(p["method"] == "PUT" for p in self.posts))
        self.assertTrue(
            self.page.evaluate(
                "(() => {const e=new Event('beforeunload',{cancelable:true});"
                "window.dispatchEvent(e);return e.defaultPrevented})()"
            )
        )
        self.page.locator("#config-json").fill('{"prefix":"later"}')
        self.abort_held()
        expect(self.page.locator("#config-status")).to_contain_text("未确认")
        self.page.locator("#reconcile-config").click()
        expect(self.page.locator("#config-status")).to_contain_text("当前配置与原请求相符")
        expect(self.page.locator("#config-json")).to_have_value('{"prefix":"later"}')
        self.assertEqual(1, sum(p["method"] == "PUT" for p in self.posts))

    def test_install_preflight_rejection_releases_plan_but_unknown_retries_same_command(self):
        from playwright.sync_api import expect

        cases = [
            ("install", 409, "CONFIRMATION_REQUIRED", True),
            ("upgrade", 404, "PLAN_NOT_FOUND", True),
            ("upgrade", 409, "PLAN_MODE_MISMATCH", True),
            ("upgrade", 503, "CONFIRMATION_REQUIRED", False),
            ("upgrade", 409, "UNRECOGNIZED_ERROR", False),
            ("install", 200, None, False),
        ]
        for mode, status, code, rejected in cases:
            with self.subTest(mode=mode, status=status, code=code):
                self.page.reload()
                self.mode = mode
                self.install_response = (
                    status,
                    {"error": {"code": code, "message": "fixture refusal"}} if code else {},
                )
                self.inspect()
                self.page.locator("#install").click()
                expected = "本次被拒绝" if rejected else "未确认"
                expect(self.page.locator("#status")).to_contain_text(expected)
                original = self.posts[-1]
                before = len(self.posts)
                if rejected:
                    expect(self.page.locator("#inspect button")).to_be_enabled()
                    expect(self.page.locator("#trust")).not_to_be_checked()
                    expect(self.page.locator("#install")).to_be_disabled()
                    expect(self.page.locator("#preview")).to_have_text("")
                    self.assertEqual(before, len(self.posts))
                else:
                    expect(self.page.locator("#inspect button")).to_be_disabled()
                    expect(self.page.locator("#install")).to_be_enabled()
                    self.install_response = None
                    self.page.locator("#install").click()
                    expect(self.page.locator("#status")).to_contain_text("已确认")
                    self.assertEqual(original, self.posts[-1])
