"""390px Access-unavailable reads retain local work and recover by polling."""

from __future__ import annotations

import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

from test_pwa_browser_f08 import PLAYWRIGHT_AVAILABLE, _Site, _State


class _AccessState(_State):
    def __init__(self) -> None:
        super().__init__()
        self.tasks = [self.tasks[0]]
        self.tasks[0]["objective"] = "saved task retained during Access outage"
        self.push_enabled = False
        self.read_status: int | None = None
        self.read_code = "CLOUDFLARE_ACCESS_UNAVAILABLE"
        self.list_statuses: list[int] = []
        self.detail_statuses: list[int] = []


class _AccessSite(_Site):
    state: _AccessState

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        statuses = (
            self.state.list_statuses if path == "/api/v1/tasks"
            else self.state.detail_statuses if path == "/api/v1/tasks/task_1"
            else None
        )
        if statuses is not None:
            status = self.state.read_status or 200
            statuses.append(status)
            if status != 200:
                self._json({"error": {
                    "code": self.state.read_code,
                    "message": "Cloudflare Access verification is unavailable.",
                    "retryable": status == 503,
                }}, status)
                return
        super().do_GET()


@unittest.skipUnless(PLAYWRIGHT_AVAILABLE, "install the optional browser extra")
class PwaAccessRecoveryBrowserF086Tests(unittest.TestCase):
    def setUp(self) -> None:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import expect, sync_playwright

        self.state = _AccessState()
        handler = type("AccessRecoveryHandler", (_AccessSite,), {"state": self.state})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_site)
        self.playwright = sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        try:
            self.browser = self.playwright.chromium.launch(headless=True)
        except PlaywrightError as error:
            if os.name != "nt" or "spawn EFTYPE" not in str(error):
                raise
            self.browser = self.playwright.chromium.launch(headless=True, channel="chrome")
        self.addCleanup(self.browser.close)
        self.context = self.browser.new_context(viewport={"width": 390, "height": 844})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.methods: list[str] = []
        self.navigation_urls: list[str] = []
        self.page.on("request", lambda request: self.methods.append(request.method))
        self.page.on(
            "framenavigated",
            lambda frame: self.navigation_urls.append(frame.url)
            if frame == self.page.main_frame else None,
        )
        self.clock = getattr(self.page, "clock", None)
        if self.clock is not None:
            self.clock.install()
        self.url = f"http://127.0.0.1:{self.server.server_port}/ui/"
        self.page.goto(self.url)
        expect(self.page.locator("#connection-state")).to_contain_text("事件连接已建立")
        expect(self.page.locator("#task-list button")).to_have_count(1)
        expect(self.page.locator("#task-detail")).to_contain_text(
            "saved task retained during Access outage"
        )
        expect(self.page.locator("#task-message-version")).to_contain_text("1")
        self.unsent = "F08.6 unsent private message survive Access 503"
        self.page.locator("#task-message-input").fill(self.unsent)
        self.page.evaluate("async () => { await navigator.serviceWorker.ready; }")

    def _stop_site(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _poll(self) -> None:
        # Exercise the product's existing 30-second read-only interval.
        if self.clock is not None:
            self.clock.fast_forward(30_001)
        else:
            self.page.wait_for_timeout(31_000)

    def _assert_local_work_and_no_commands(self) -> None:
        from playwright.sync_api import expect

        expect(self.page.locator("#task-list button")).to_have_count(1)
        expect(self.page.locator("#task-message-input")).to_have_value(self.unsent)
        self.assertNotIn("POST", self.methods)
        self.assertEqual([], self.state.message_posts)
        self.assertEqual([], self.state.push_posts)
        self.assertEqual(self.url, self.page.url)
        self.assertTrue(self.navigation_urls)
        self.assertTrue(all(url == self.url for url in self.navigation_urls))

    def _assert_static_cache_only(self) -> None:
        cache = self.page.evaluate("""async (unsent) => {
          const names = await caches.keys();
          const urls = [];
          let containsInput = false;
          for (const name of names) {
            const current = await caches.open(name);
            for (const request of await current.keys()) {
              urls.push(new URL(request.url).pathname);
              const response = await current.match(request);
              if ((await response.text()).includes(unsent)) containsInput = true;
            }
          }
          return { names, urls, containsInput };
        }""", self.unsent)
        self.assertEqual(1, len(cache["names"]))
        self.assertEqual(
            ["/ui/", "/ui/app.js", "/ui/manifest.webmanifest", "/ui/styles.css"],
            sorted(cache["urls"]),
        )
        self.assertFalse(cache["containsInput"])

    def test_access_503_keeps_work_and_recovers_without_login_or_manual_refresh(self) -> None:
        from playwright.sync_api import expect

        self.assertIn(200, self.state.list_statuses)
        self.assertIn(200, self.state.detail_statuses)
        self.state.read_status = 503
        self._poll()
        error = self.page.locator("#task-list-error")
        expect(error).to_be_visible()
        expect(self.page.locator(".task-detail-error")).to_be_visible()
        self.assertIn(503, self.state.list_statuses)
        self.assertIn(503, self.state.detail_statuses)
        expect(self.page.locator("#task-list")).to_contain_text(
            "saved task retained during Access outage"
        )
        self._assert_local_work_and_no_commands()
        self._assert_static_cache_only()
        expect(error).to_contain_text("助手暂时无法校验登录")
        expect(error).to_contain_text("无需退出重新登录")

        self.state.tasks[0]["version"] = 2
        self.state.tasks[0]["objective"] = "updated task after Access recovery"
        self.state.read_status = None
        self._poll()
        expect(error).to_be_hidden()
        expect(error).to_have_text("")
        expect(self.page.locator("#task-list")).to_contain_text(
            "updated task after Access recovery"
        )
        expect(self.page.locator("#task-detail")).to_contain_text(
            "updated task after Access recovery"
        )
        expect(self.page.locator("#task-message-version")).to_contain_text("2")
        expect(self.page.locator(".task-detail-error")).to_have_count(0)
        self.assertEqual(200, self.state.list_statuses[-1])
        self.assertEqual(200, self.state.detail_statuses[-1])
        self._assert_local_work_and_no_commands()
        self._assert_static_cache_only()

    def test_other_read_failures_do_not_offer_access_503_recovery(self) -> None:
        from playwright.sync_api import expect

        for status, code in (
            (401, "CLOUDFLARE_ACCESS_UNAVAILABLE"),
            (503, "ANOTHER_SERVICE_UNAVAILABLE"),
        ):
            with self.subTest(status=status, code=code):
                self.state.read_status = status
                self.state.read_code = code
                self._poll()
                error = self.page.locator("#task-list-error")
                expect(error).to_be_visible()
                expect(self.page.locator(".task-detail-error")).to_be_visible()
                self.assertNotIn("助手暂时无法校验登录", error.inner_text())
                self.assertNotIn("无需退出重新登录", error.inner_text())
                self._assert_local_work_and_no_commands()


if __name__ == "__main__":
    unittest.main()
