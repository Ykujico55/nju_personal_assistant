"""Android-sized browser check for task reading, SSE reconnect, and static caching."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import socket
import threading
import unittest
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.util import find_spec
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

PWA_ROOT = Path(__file__).resolve().parents[2] / "web" / "pwa"
NOW = datetime.now(UTC).isoformat()
PLAYWRIGHT_AVAILABLE = find_spec("playwright") is not None


def _task(task_id: str, objective: str) -> dict[str, object]:
    return {
        "id": task_id,
        "objective": objective,
        "state": "QUEUED",
        "version": 1,
        "created_at": NOW,
    }


class _State:
    def __init__(self) -> None:
        self.tasks = [
            _task("task_1", "first task"),
            _task("task_2", "<img src=x onerror=window.__injected=true>"),
        ]
        self.events: queue.Queue[str] = queue.Queue()
        self.messages: dict[str, list[dict[str, object]]] = {}
        self.message_posts: list[dict[str, object]] = []
        self.message_keys: dict[str, tuple[str, str]] = {}
        self.drop_next_message_response = False
        self.next_message_response: object | None = None
        self.push_subscriptions: dict[str, str] = {}
        self.push_reconfigure_ids: set[str] = set()
        self.push_enabled = True
        self.push_posts: list[str] = []
        self.push_keys: list[str] = []
        self.drop_next_push_response = False
        self.next_push_response: object | None = None


class _Site(BaseHTTPRequestHandler):
    state: _State

    def log_message(self, *_args: object) -> None:
        pass

    def _json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if url.path == "/api/v1/push/config":
            self._json({
                "enabled": self.state.push_enabled,
                "public_key": "B" + "A" * 86 if self.state.push_enabled else None,
            })
            return
        if url.path.startswith("/api/v1/push/subscriptions/"):
            identifier = url.path.rsplit("/", 1)[-1]
            self._json({
                "id": identifier,
                "active": identifier in self.state.push_subscriptions
                and identifier not in self.state.push_reconfigure_ids,
                "reconfigure_required": identifier in self.state.push_reconfigure_ids,
            })
            return
        if url.path == "/api/v1/tasks":
            parameters = parse_qs(url.query)
            tasks = list(reversed(self.state.tasks))
            before = parameters.get("before", [None])[0]
            if before is not None:
                tasks = tasks[next(i for i, task in enumerate(tasks) if task["id"] == before) + 1 :]
            limit = int(parameters.get("limit", ["20"])[0])
            items = tasks[:limit]
            self._json(
                {"items": items, "next_before": items[-1]["id"] if len(tasks) > limit else None}
            )
            return
        if url.path.startswith("/api/v1/tasks/"):
            task_id = url.path.rsplit("/", 1)[-1]
            task = next((task for task in self.state.tasks if task["id"] == task_id), None)
            if task is None:
                self._json({"error": {"message": "not found"}}, 404)
            else:
                self._json({"task": task, "messages": self.state.messages.get(task_id, [])})
            return
        if url.path == "/api/v1/extensions":
            self._json({"items": []})
            return
        if url.path == "/api/v1/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
                while True:
                    event = self.state.events.get(timeout=15)
                    self.wfile.write(f"id: 1\nevent: task.queued\ndata: {event}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, queue.Empty):
                return
        static = {
            "/ui/": ("index.html", "text/html; charset=utf-8"),
            "/ui/app.js": ("app.js", "text/javascript"),
            "/ui/styles.css": ("styles.css", "text/css"),
            "/ui/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
            "/ui/service-worker.js": ("service-worker.js", "text/javascript"),
        }.get(url.path)
        if static is None:
            self.send_error(404)
            return
        filename, media_type = static
        body = (PWA_ROOT / filename).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        url = urlsplit(self.path)
        if url.path == "/api/v1/push/subscriptions":
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            endpoint = payload["endpoint"]
            identifier = hashlib.sha256(endpoint.encode()).hexdigest()
            self.state.push_posts.append(endpoint)
            self.state.push_keys.append(self.headers.get("Idempotency-Key", ""))
            self.state.push_subscriptions[identifier] = endpoint
            if self.state.drop_next_push_response:
                self.state.drop_next_push_response = False
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if self.state.next_push_response is not None:
                response = self.state.next_push_response
                self.state.next_push_response = None
                self._json(response, 201)
                return
            self._json({"id": identifier, "created_at": NOW}, 201)
            return
        if not url.path.startswith("/api/v1/tasks/") or not url.path.endswith("/messages"):
            self.send_error(404)
            return
        task_id = url.path.split("/")[4]
        task = next((item for item in self.state.tasks if item["id"] == task_id), None)
        if task is None:
            self._json({"error": {"message": "not found"}}, 404)
            return
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        content = payload["content"].strip()
        key = self.headers.get("Idempotency-Key", "")
        self.state.message_posts.append(
            {
                "task_id": task_id,
                "version": payload["version"],
                "content": payload["content"],
                "key": key,
            }
        )
        replay = self.state.message_keys.get(key)
        if replay is not None and replay != (task_id, content):
            self._json({"error": {"code": "CONCURRENT_MODIFICATION", "message": "key reused"}}, 409)
            return
        if replay is None and payload["version"] != task["version"]:
            self._json(
                {"error": {"code": "CONCURRENT_MODIFICATION", "message": "task version changed"}},
                409,
            )
            return
        if replay is None:
            messages = self.state.messages.setdefault(task_id, [])
            messages.append(
                {
                    "id": f"message_{len(messages) + 1}",
                    "task_id": task_id,
                    "actor": "user",
                    "content": content,
                    "created_at": NOW,
                }
            )
            task["version"] += 1
            self.state.message_keys[key] = (task_id, content)
        if self.state.drop_next_message_response:
            self.state.drop_next_message_response = False
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        if self.state.next_message_response is not None:
            response = self.state.next_message_response
            self.state.next_message_response = None
            self._json(response)
            return
        self._json({"task": task, "messages": self.state.messages[task_id]})

    def do_DELETE(self) -> None:
        url = urlsplit(self.path)
        if url.path.startswith("/api/v1/push/subscriptions/"):
            self.state.push_subscriptions.pop(url.path.rsplit("/", 1)[-1], None)
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        self.send_error(404)


@unittest.skipUnless(PLAYWRIGHT_AVAILABLE, "install the optional browser extra")
class PwaBrowserF08Tests(unittest.TestCase):
    def test_410_revocation_replaces_stale_browser_subscription(self) -> None:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import expect, sync_playwright

        old_endpoint = "https://push.example.test/send/expired"
        new_endpoint = "https://push.example.test/send/replacement"
        old_id = hashlib.sha256(old_endpoint.encode()).hexdigest()
        new_id = hashlib.sha256(new_endpoint.encode()).hexdigest()
        state = _State()
        state.push_subscriptions[old_id] = old_endpoint
        handler = type("PwaPush410Handler", (_Site,), {"state": state})
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
                    context.add_init_script(f"""
                        const probe = {{
                          current: null, subscribeCalls: 0, unsubscribeCalls: 0,
                          failUnsubscribe: false, keepOldAfterUnsubscribe: false
                        }};
                        const oldEndpoint = {json.dumps(old_endpoint)};
                        const newEndpoint = {json.dumps(new_endpoint)};
                        const makeSubscription = (endpoint) => ({{
                          endpoint,
                          toJSON: () => ({{endpoint, keys: {{
                            p256dh: 'browser-key', auth: 'browser-auth'
                          }}}}),
                          unsubscribe: async () => {{
                            probe.unsubscribeCalls += 1;
                            if (probe.failUnsubscribe) return false;
                            if (!probe.keepOldAfterUnsubscribe) probe.current = null;
                            return true;
                          }}
                        }});
                        probe.current = makeSubscription(oldEndpoint);
                        window.__pushProbe = probe;
                        const registration = {{pushManager: {{
                          getSubscription: async () => probe.current,
                          subscribe: async () => {{
                            probe.subscribeCalls += 1;
                            probe.current = makeSubscription(newEndpoint);
                            return probe.current;
                          }}
                        }}}};
                        Object.defineProperty(window, 'Notification', {{value: {{
                          permission: 'default', requestPermission: async () => 'granted'
                        }}}});
                        Object.defineProperty(navigator, 'serviceWorker', {{value: {{
                          register: async () => registration,
                          ready: Promise.resolve(registration)
                        }}}});
                    """)
                    page = context.new_page()
                    page.goto(f"http://127.0.0.1:{server.server_port}/ui/")
                    expect(page.locator("#push-status")).to_contain_text("已订阅")
                    # The host's 410 handling revoked this record; the browser still has it.
                    state.push_subscriptions.pop(old_id)
                    page.locator("#push-refresh").click()
                    expect(page.locator("#push-status")).to_contain_text("未订阅")
                    page.locator("#push-enable").click()
                    expect(page.locator("#push-status")).to_contain_text("已订阅")
                    self.assertEqual([new_endpoint], state.push_posts)
                    self.assertEqual({new_id: new_endpoint}, state.push_subscriptions)
                    self.assertEqual(
                        {"unsubscribeCalls": 1, "subscribeCalls": 1},
                        page.evaluate("""() => ({
                          unsubscribeCalls: window.__pushProbe.unsubscribeCalls,
                          subscribeCalls: window.__pushProbe.subscribeCalls
                        })"""),
                    )

                    state.push_subscriptions.pop(new_id)
                    page.evaluate("window.__pushProbe.failUnsubscribe = true")
                    page.locator("#push-refresh").click()
                    expect(page.locator("#push-status")).to_contain_text("未订阅")
                    page.locator("#push-enable").click()
                    expect(page.locator("#push-status")).to_contain_text("无法清理")
                    self.assertEqual([new_endpoint], state.push_posts)
                    self.assertEqual(
                        1, page.evaluate("window.__pushProbe.subscribeCalls")
                    )
                    page.evaluate("""() => {
                      window.__pushProbe.failUnsubscribe = false;
                      window.__pushProbe.keepOldAfterUnsubscribe = true;
                    }""")
                    page.locator("#push-enable").click()
                    expect(page.locator("#push-status")).to_contain_text("无法清理")
                    self.assertEqual([new_endpoint], state.push_posts)
                    self.assertEqual(
                        1, page.evaluate("window.__pushProbe.subscribeCalls")
                    )
                    page.evaluate("window.__pushProbe.keepOldAfterUnsubscribe = false")
                    page.locator("#push-enable").click()
                    expect(page.locator("#push-status")).to_contain_text("已失效端点")
                    self.assertEqual([new_endpoint], state.push_posts)
                    self.assertEqual(
                        2, page.evaluate("window.__pushProbe.subscribeCalls")
                    )
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_push_subscription_lost_receipt_retry_revoke_and_no_sensitive_cache(self) -> None:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import expect, sync_playwright

        state = _State()
        handler = type("PwaPushHandler", (_Site,), {"state": state})
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
                    browser_subscription = {
                        "endpoint": "https://push.example.test/send/stale"
                    }
                    context.expose_function(
                        "mockPushGet", lambda: browser_subscription["endpoint"]
                    )
                    context.expose_function(
                        "mockPushSet",
                        lambda endpoint: browser_subscription.__setitem__("endpoint", endpoint),
                    )
                    context.add_init_script("""
                        const endpoint = 'https://push.example.test/send/opaque';
                        const probe = { unsubscribeCalls: 0, subscribeCalls: 0 };
                        const makeSubscription = (value) => ({
                          endpoint: value,
                          toJSON: () => ({endpoint: value, keys: {
                            p256dh: 'browser-key', auth: 'browser-auth'
                          }}),
                          unsubscribe: async () => {
                            probe.unsubscribeCalls += 1;
                            await window.mockPushSet(null);
                            return true;
                          }
                        });
                        window.__pushProbe = probe;
                        const registration = {pushManager: {
                          getSubscription: async () => {
                            const value = await window.mockPushGet();
                            return value ? makeSubscription(value) : null;
                          },
                          subscribe: async () => {
                            probe.subscribeCalls += 1;
                            await window.mockPushSet(endpoint);
                            return makeSubscription(endpoint);
                          }
                        }};
                        Object.defineProperty(window, 'Notification', {value: {
                          permission: 'default', requestPermission: async () => 'granted'
                        }});
                        Object.defineProperty(navigator, 'serviceWorker', {value: {
                          register: async () => registration,
                          ready: Promise.resolve(registration)
                        }});
                    """)
                    page = context.new_page()
                    state.drop_next_push_response = True
                    page.goto(f"http://127.0.0.1:{server.server_port}/ui/")
                    expect(page.locator("#push-status")).to_contain_text("未订阅")
                    page.locator("#push-enable").click()
                    expect(page.locator("#push-status")).to_contain_text("结果未确认")
                    self.assertEqual(1, len(state.push_posts))
                    first_identifier = next(iter(state.push_subscriptions))
                    state.push_reconfigure_ids.add(first_identifier)
                    page.locator("#push-enable").click()
                    expect(page.locator("#push-status")).to_contain_text("需重新配置")
                    expect(page.locator("#push-enable")).to_be_disabled()
                    state.push_reconfigure_ids.clear()
                    page.locator("#push-refresh").click()
                    expect(page.locator("#push-status")).to_contain_text("已订阅")
                    self.assertEqual(2, len(state.push_posts))
                    self.assertEqual(state.push_posts[0], state.push_posts[1])
                    self.assertEqual(state.push_keys[0], state.push_keys[1])
                    self.assertEqual(1, len(state.push_subscriptions))
                    page.reload()
                    expect(page.locator("#push-status")).to_contain_text("已订阅")
                    identifier = next(iter(state.push_subscriptions))
                    state.push_reconfigure_ids.add(identifier)
                    page.locator("#push-refresh").click()
                    expect(page.locator("#push-status")).to_contain_text("需重新配置")
                    expect(page.locator("#push-enable")).to_be_disabled()
                    expect(page.locator("#push-disable")).to_be_enabled()
                    state.push_enabled = False
                    page.locator("#push-refresh").click()
                    expect(page.locator("#push-status")).to_contain_text("需重新配置")
                    expect(page.locator("#push-disable")).to_be_enabled()
                    state.push_enabled = True
                    state.push_reconfigure_ids.clear()
                    page.locator("#push-refresh").click()
                    expect(page.locator("#push-status")).to_contain_text("已订阅")
                    page.locator("#push-disable").click()
                    expect(page.locator("#push-status")).to_contain_text("未订阅")
                    self.assertEqual({}, state.push_subscriptions)
                    state.next_push_response = {"id": "0" * 64, "created_at": NOW}
                    page.locator("#push-enable").click()
                    expect(page.locator("#push-status")).to_contain_text("结果未确认")
                    page.locator("#push-enable").click()
                    expect(page.locator("#push-status")).to_contain_text("已订阅")
                    self.assertEqual(state.push_keys[2], state.push_keys[3])
                    self.assertEqual(1, len(state.push_subscriptions))
                    page.goto(f"http://127.0.0.1:{server.server_port}/ui/?task=task_1")
                    expect(page.locator("#task-detail")).to_have_attribute("data-task-id", "task_1")
                    cached = page.evaluate("""async () => {
                      const urls = [];
                      for (const name of await caches.keys()) {
                        const cache = await caches.open(name);
                        for (const request of await cache.keys()) urls.push(request.url);
                      }
                      return urls;
                    }""")
                    self.assertTrue(all("/api/" not in url for url in cached))
                    self.assertTrue(all("push.example.test" not in url for url in cached))
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_task_list_detail_live_update_reconnect_and_static_cache(self) -> None:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import expect, sync_playwright

        state = _State()
        handler = type("PwaHandler", (_Site,), {"state": state})
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
                    expect(page.locator("#connection-state")).to_contain_text("事件连接已建立")
                    expect(page.locator("#task-list button")).to_have_count(2)
                    expect(page.locator("#task-detail")).to_contain_text("<img")
                    self.assertEqual(0, page.locator("#task-detail img").count())
                    self.assertIsNone(page.evaluate("window.__injected"))

                    state.tasks.append(_task("task_3", "new live task"))
                    state.events.put(json.dumps({"type": "task.queued", "task_id": "task_3"}))
                    expect(page.locator("#task-list button")).to_have_count(3)
                    page.locator("#task-list button").first.click()
                    expect(page.locator("#task-detail")).to_contain_text("new live task")

                    context.set_offline(True)
                    expect(page.locator("#connection-state")).to_contain_text(
                        re.compile("离线|中断")
                    )
                    context.set_offline(False)
                    expect(page.locator("#connection-state")).to_contain_text("事件连接已建立")
                    page.evaluate(
                        "async () => { await navigator.serviceWorker.ready; return true; }"
                    )
                    cached = page.evaluate(
                        "async () => { const urls = []; for (const key of await caches.keys()) "
                        "{ const cache = await caches.open(key); "
                        "for (const request of await cache.keys()) "
                        "urls.push(request.url); } return urls; }"
                    )
                    self.assertTrue(cached)
                    self.assertTrue(all("/api/" not in url for url in cached))
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_message_submit_conflict_and_explicit_retry_at_phone_width(self) -> None:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import expect, sync_playwright

        state = _State()
        handler = type("PwaMessageHandler", (_Site,), {"state": state})
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
                    composer = page.locator("#task-message-form")
                    expect(composer.locator("#task-message-version")).to_contain_text("1")
                    draft = composer.locator("#task-message-input")
                    draft.fill("new evidence <img src=x onerror=window.__injected=true>")
                    state.tasks[0]["objective"] = "first task refreshed"
                    state.events.put(json.dumps({"type": "task.queued", "task_id": "task_1"}))
                    expect(page.locator("#task-detail h3")).to_have_text("first task refreshed")
                    expect(draft).to_have_value(
                        "new evidence <img src=x onerror=window.__injected=true>"
                    )
                    composer.locator('button[type="submit"]').click()
                    expect(page.locator("#task-detail ol")).to_contain_text("new evidence <img")
                    expect(draft).to_have_value("")
                    expect(composer.locator("#task-message-version")).to_contain_text("2")
                    self.assertEqual(1, len(state.message_posts))
                    self.assertEqual(1, state.message_posts[0]["version"])
                    self.assertTrue(state.message_posts[0]["key"])
                    self.assertEqual(0, page.locator("#task-detail img").count())
                    self.assertIsNone(page.evaluate("window.__injected"))

                    state.tasks[0]["version"] = 3
                    state.messages["task_1"].append(
                        {
                            "id": "remote",
                            "task_id": "task_1",
                            "actor": "worker",
                            "content": "missing material",
                            "created_at": NOW,
                        }
                    )
                    draft.fill("the missing material")
                    composer.locator('button[type="submit"]').click()
                    expect(composer.locator("#task-message-status")).to_contain_text("版本冲突")
                    expect(composer.locator("#task-message-version")).to_contain_text("3")
                    expect(page.locator("#task-detail ol")).to_contain_text("missing material")
                    expect(draft).to_have_value("the missing material")
                    self.assertEqual(2, len(state.message_posts))
                    self.assertEqual(2, state.message_posts[1]["version"])
                    composer.locator('button[type="submit"]').click()
                    expect(page.locator("#task-detail ol")).to_contain_text("the missing material")
                    expect(composer.locator("#task-message-version")).to_contain_text("4")
                    self.assertEqual(3, len(state.message_posts))
                    self.assertEqual(3, state.message_posts[2]["version"])
                    self.assertNotEqual(
                        state.message_posts[1]["key"], state.message_posts[2]["key"]
                    )
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_message_offline_and_lost_response_never_claim_success(self) -> None:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import expect, sync_playwright

        state = _State()
        handler = type("PwaMessageFailureHandler", (_Site,), {"state": state})
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
                    composer = page.locator("#task-message-form")
                    draft = composer.locator("#task-message-input")
                    draft.fill("context from phone")
                    context.set_offline(True)
                    composer.locator('button[type="submit"]').click()
                    expect(composer.locator("#task-message-status")).to_contain_text("未发送")
                    expect(draft).to_have_value("context from phone")
                    self.assertEqual([], state.message_posts)

                    context.set_offline(False)
                    state.drop_next_message_response = True
                    composer.locator('button[type="submit"]').click()
                    expect(composer.locator("#task-message-status")).to_contain_text("结果未确认")
                    expect(draft).to_have_value("context from phone")
                    self.assertEqual(1, len(state.message_posts))
                    expect(draft).to_have_attribute("readonly", "")
                    context.set_offline(True)
                    composer.locator('button[type="submit"]').click()
                    expect(composer.locator("#task-message-status")).to_contain_text("结果未确认")
                    self.assertEqual(1, len(state.message_posts))
                    context.set_offline(False)
                    composer.locator('button[type="submit"]').click()
                    expect(composer.locator("#task-message-status")).to_contain_text("已发送")
                    expect(draft).to_have_value("")
                    self.assertEqual(2, len(state.message_posts))
                    self.assertEqual(state.message_posts[0]["key"], state.message_posts[1]["key"])
                    self.assertEqual(1, len(state.messages["task_1"]))
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_committed_message_with_incomplete_200_retries_with_original_key(self) -> None:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import expect, sync_playwright

        state = _State()
        handler = type("PwaIncompleteMessageHandler", (_Site,), {"state": state})
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
                    composer = page.locator("#task-message-form")
                    draft = composer.locator("#task-message-input")
                    draft.fill("same material answer")
                    state.next_message_response = {}
                    composer.locator('button[type="submit"]').click()
                    expect(composer.locator("#task-message-status")).to_contain_text("结果未确认")
                    expect(draft).to_have_value("same material answer")
                    expect(draft).to_have_attribute("readonly", "")
                    expect(composer.locator('button[type="submit"]')).to_have_text("重试同一请求")
                    self.assertEqual(1, len(state.messages["task_1"]))
                    self.assertEqual(1, len(state.message_posts))

                    page.locator("#refresh-tasks").click()
                    expect(composer.locator("#task-message-version")).to_contain_text("2")
                    expect(draft).to_have_value("same material answer")
                    composer.locator('button[type="submit"]').click()
                    expect(composer.locator("#task-message-status")).to_contain_text("已发送")
                    expect(draft).to_have_value("")
                    self.assertEqual(2, len(state.message_posts))
                    self.assertEqual(state.message_posts[0]["key"], state.message_posts[1]["key"])
                    self.assertEqual(
                        state.message_posts[0]["version"], state.message_posts[1]["version"]
                    )
                    self.assertEqual(1, len(state.messages["task_1"]))

                    draft.fill("second material answer")
                    state.next_message_response = {
                        "task": {**state.tasks[0], "id": "task_2"},
                        "messages": state.messages["task_1"],
                    }
                    composer.locator('button[type="submit"]').click()
                    expect(composer.locator("#task-message-status")).to_contain_text("结果未确认")
                    expect(draft).to_have_value("second material answer")
                    expect(draft).to_have_attribute("readonly", "")
                    self.assertEqual(2, len(state.messages["task_1"]))
                    composer.locator('button[type="submit"]').click()
                    expect(composer.locator("#task-message-status")).to_contain_text("已发送")
                    self.assertEqual(state.message_posts[2]["key"], state.message_posts[3]["key"])
                    self.assertEqual(2, len(state.messages["task_1"]))

                    draft.fill("third material answer")
                    page.evaluate(
                        """() => {
                          const original = Element.prototype.replaceChildren;
                          Element.prototype.replaceChildren = function(...children) {
                            if (this.classList?.contains('task-detail-content')) {
                              Element.prototype.replaceChildren = original;
                              throw new Error('synthetic render failure');
                            }
                            return original.apply(this, children);
                          };
                        }"""
                    )
                    composer.locator('button[type="submit"]').click()
                    expect(composer.locator("#task-message-status")).to_contain_text("结果未确认")
                    expect(draft).to_have_value("third material answer")
                    expect(draft).to_have_attribute("readonly", "")
                    self.assertEqual(3, len(state.messages["task_1"]))
                    composer.locator('button[type="submit"]').click()
                    expect(composer.locator("#task-message-status")).to_contain_text("已发送")
                    self.assertEqual(state.message_posts[4]["key"], state.message_posts[5]["key"])
                    self.assertEqual(3, len(state.messages["task_1"]))
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_message_with_edge_spaces_uses_the_server_normalized_receipt(self) -> None:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import expect, sync_playwright

        state = _State()
        handler = type("PwaTrimmedMessageHandler", (_Site,), {"state": state})
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
                    composer = page.locator("#task-message-form")
                    draft = composer.locator("#task-message-input")
                    draft.fill("  same material answer  ")
                    composer.locator('button[type="submit"]').click()
                    expect(composer.locator("#task-message-status")).to_contain_text("已发送")
                    expect(draft).to_have_value("")
                    self.assertEqual("same material answer", state.messages["task_1"][0]["content"])
                    self.assertEqual("same material answer", state.message_posts[0]["content"])
                    self.assertEqual(1, len(state.messages["task_1"]))

                    draft.fill("  second material answer  ")
                    state.next_message_response = {}
                    composer.locator('button[type="submit"]').click()
                    expect(composer.locator("#task-message-status")).to_contain_text("结果未确认")
                    expect(draft).to_have_value("  second material answer  ")
                    expect(draft).to_have_attribute("readonly", "")
                    composer.locator('button[type="submit"]').click()
                    expect(composer.locator("#task-message-status")).to_contain_text("已发送")
                    self.assertEqual(state.message_posts[1]["key"], state.message_posts[2]["key"])
                    self.assertEqual(
                        state.message_posts[1]["content"], state.message_posts[2]["content"]
                    )
                    self.assertEqual(
                        "second material answer", state.messages["task_1"][1]["content"]
                    )
                    self.assertEqual(2, len(state.messages["task_1"]))
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
