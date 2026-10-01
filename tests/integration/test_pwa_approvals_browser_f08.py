"""F08.4 390px approval review and ambiguous receipt counterexamples."""

from __future__ import annotations

import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

from test_pwa_browser_f08 import NOW, PLAYWRIGHT_AVAILABLE, _Site, _State


def _approval(*, ready: bool = True) -> dict[str, object]:
    return {
        "id": "approval-1",
        "state": "WAITING_APPROVAL",
        "action": {
            "action_type": "smail.send",
            "task_id": "task_1",
            "tool_id": "smail.send",
            "tool_version": "1",
            "extension_id": "nju.smail",
            "extension_version": "0.1.0",
            "target": {"account_id": "nju"},
            "payload": {
                "account_id": "nju",
                "from_address": "sender@example.test",
                "to": ["recipient@example.test"],
                "cc": [],
                "bcc": [],
                "subject": "Subject <img src=x onerror=window.__injected=true>",
                "mime_sha256": "a" * 64,
                "attachment_hashes": [],
            },
            "attachments": [],
            "form_version": None,
        },
        "nonce": "one-use-nonce" if ready else None,
        "action_fingerprint": "f" * 64,
        "created_at": NOW,
        "expires_at": "2099-01-01T00:00:00Z",
        "version": 0,
        "review": {
            "kind": "MAIL" if ready else "EHALL",
            "ready": ready,
            "reason": None if ready else "完整材料尚未核验",
            "details": {
                "account_id": "nju",
                "from_address": "sender@example.test",
                "to": ["recipient@example.test"],
                "cc": [],
                "bcc": [],
                "subject": "Subject <img src=x onerror=window.__injected=true>",
                "body_text": "Body <script>window.__injected=true</script>",
                "mime_sha256": "a" * 64,
                "attachments": [],
            } if ready else None,
        },
    }


class _ApprovalState(_State):
    def __init__(self) -> None:
        super().__init__()
        self.approval = _approval()
        self.approve_posts = 0
        self.incomplete_next_response = False


class _ApprovalSite(_Site):
    state: _ApprovalState

    def do_GET(self) -> None:
        if urlsplit(self.path).path == "/api/v1/approvals/approval-1":
            self._json(self.state.approval)
            return
        super().do_GET()

    def do_POST(self) -> None:
        if urlsplit(self.path).path == "/api/v1/approvals/approval-1/approve":
            self.state.approve_posts += 1
            self.state.approval = {
                **self.state.approval,
                "state": "APPROVED",
                "nonce": None,
                "version": 1,
            }
            if self.state.incomplete_next_response:
                self.state.incomplete_next_response = False
                self._json({})
            else:
                self._json(self.state.approval)
            return
        super().do_POST()


@unittest.skipUnless(PLAYWRIGHT_AVAILABLE, "install the optional browser extra")
class PwaApprovalBrowserTests(unittest.TestCase):
    def test_review_is_text_only_and_incomplete_approval_receipt_is_uncertain(self) -> None:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import expect, sync_playwright

        state = _ApprovalState()
        handler = type("PwaApprovalHandler", (_ApprovalSite,), {"state": state})
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
                    page.locator("#approval-id").fill("approval-1")
                    state.approval["review"]["details"]["mime_sha256"] = "b" * 64
                    page.locator("#approval-form button").click()
                    expect(page.locator("#approve-button")).to_be_disabled()
                    self.assertEqual(0, state.approve_posts)
                    state.approval["review"]["details"]["mime_sha256"] = "a" * 64
                    page.locator("#approval-form button").click()
                    expect(page.locator("#approval-material")).to_contain_text("Body <script>")
                    expect(page.locator("#approval-action")).to_contain_text("0.1.0")
                    expect(page.locator("#approval-action")).to_contain_text("one-use-nonce")
                    self.assertEqual(0, page.locator("#approval-preview img").count())
                    self.assertIsNone(page.evaluate("window.__injected"))
                    expect(page.locator("#approve-button")).to_be_enabled()
                    state.incomplete_next_response = True
                    page.on("dialog", lambda dialog: dialog.accept())
                    page.locator("#approve-button").click()
                    expect(page.locator("#approval-status")).to_contain_text("结果未确认")
                    expect(page.locator("#approve-button")).to_be_disabled()
                    self.assertEqual(1, state.approve_posts)
                    page.locator("#approval-form button").click()
                    expect(page.locator("#approval-status")).to_contain_text("APPROVED")
                    self.assertEqual(1, state.approve_posts)
                    cached = page.evaluate(
                        "async () => { const items = []; for (const key of await caches.keys()) "
                        "{ const cache = await caches.open(key); "
                        "for (const request of await cache.keys()) "
                        "items.push([request.url, await (await cache.match(request)).text()]); "
                        "} return items; }"
                    )
                    self.assertTrue(all("/api/" not in url for url, _ in cached))
                    self.assertTrue(
                        all("Body <script>window.__injected" not in body for _, body in cached)
                    )
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_incomplete_ehall_review_cannot_be_approved(self) -> None:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import expect, sync_playwright

        state = _ApprovalState()
        state.approval = _approval(ready=False)
        state.approval["action"]["action_type"] = "ehall.submit"
        state.approval["action"]["tool_id"] = "ehall.submit"
        handler = type("PwaBlockedApprovalHandler", (_ApprovalSite,), {"state": state})
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
                    page = browser.new_page(viewport={"width": 390, "height": 844})
                    page.goto(f"http://127.0.0.1:{server.server_port}/ui/")
                    page.locator("#approval-id").fill("approval-1")
                    page.locator("#approval-form button").click()
                    expect(page.locator("#approval-status")).to_contain_text("完整材料尚未核验")
                    expect(page.locator("#approve-button")).to_be_disabled()
                    self.assertEqual(0, state.approve_posts)
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
