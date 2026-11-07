from __future__ import annotations

import copy
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2]


class ExtensionActionPwaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.driver = await async_playwright().start()
        self.addAsyncCleanup(self.driver.stop)
        self.browser = await self.driver.chromium.launch(channel="chrome", headless=True)
        self.addAsyncCleanup(self.browser.close)
        self.page = await self.browser.new_page(viewport={"width": 390, "height": 844})
        await self.page.set_content('''<div id="approval-preview">
            <pre id="approval-action"></pre><pre id="approval-material"></pre>
            <div id="approval-status"></div><button id="approve-button"></button>
            <button id="reject-button"></button></div>''')
        app = (ROOT / "web/pwa/app.js").read_text(encoding="utf-8")
        begin = app.index("function approvalShape(")
        end = app.index("function uncertainApprovalResult(", begin)
        stable = app.index("function stableJson(")
        stable_end = app.index("\n}", stable) + 2
        await self.page.add_script_tag(content=app[begin:end] + app[stable:stable_end])

    def record(self):
        payload = {"local_action_id": "fixture", "target": {"company": "Fixture", "id": "1"},
                   "operation": {"text": "<img src=x onerror=alert(1)>"},
                   "consequences": "Send the exact text", "attachments": []}
        action = {"tool_id": "fixture.send", "action_type": "fixture.send", "task_id": "t1",
                  "extension_id": "fixture", "extension_version": "1", "payload": payload,
                  "target": {"id": "1", "company": "Fixture"}, "attachments": []}
        return {"id": "approval1", "version": 1, "state": "WAITING_APPROVAL", "nonce": "fixture",
                "action_fingerprint": "a" * 64, "created_at": datetime.now(UTC).isoformat(),
                "expires_at": (datetime.now(UTC) + timedelta(minutes=4)).isoformat(),
                "action": action, "review": {"kind": "EXTENSION_ACTION", "ready": True,
                    "details": {"target": payload["target"], "payload": copy.deepcopy(payload),
                                "consequences": payload["consequences"], "attachments": []}}}

    async def render(self, record):
        await self.page.evaluate("record => renderApproval(record)", record)
        return await self.page.locator("#approve-button").is_enabled()

    async def test_complete_generic_review_renders_text_and_accepts_reordered_keys(self):
        self.assertTrue(await self.render(self.record()))
        self.assertIn("onerror", await self.page.locator("#approval-material").inner_text())
        self.assertEqual(0, await self.page.locator("img").count())

    async def test_payload_target_or_attachment_drift_disables_approval(self):
        for field, value in (("payload", {}), ("target", {"id": "other"}),
                             ("attachments", [{"name": "undeclared"}])):
            record = self.record()
            record["review"]["details"][field] = value
            with self.subTest(field=field):
                self.assertFalse(await self.render(record))
