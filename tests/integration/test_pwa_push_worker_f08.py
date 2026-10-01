"""F08.5: notification payload and click behavior in the real Service Worker script."""

from __future__ import annotations

import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path

WORKER = Path(__file__).resolve().parents[2] / "web" / "pwa" / "service-worker.js"


@unittest.skipUnless(shutil.which("node"), "Node.js is required for Service Worker check")
class PwaPushWorkerTests(unittest.TestCase):
    def test_minimal_push_and_click_only_opens_fixed_task_page(self) -> None:
        script = textwrap.dedent("""
            const assert = require('node:assert/strict');
            const fs = require('node:fs');
            const vm = require('node:vm');
            const handlers = {};
            const notifications = [];
            const opened = [];
            const context = {
              URL, Promise,
              self: {
                location: {origin: 'https://assistant.example.test'},
                addEventListener: (name, fn) => { handlers[name] = fn; },
                registration: {showNotification: async (title, options) => {
                  notifications.push({title, options});
                }},
                clients: {openWindow: async (url) => {opened.push(url);}},
              },
              fetch: async () => {throw Error('push/click must never call fetch');},
            };
            vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
            assert.equal(typeof handlers.push, 'function');
            assert.equal(typeof handlers.notificationclick, 'function');
            async function push(data) {
              const waits = [];
              handlers.push({
                data: {json: () => data}, waitUntil: (promise) => waits.push(promise)
              });
              await Promise.all(waits);
            }
            (async () => {
              await push({kind: 'pending', risk: 'R2', task_id: 'task_7',
                subject: 'private mail subject', url: 'https://evil.example/approve'});
              assert.equal(notifications.length, 1);
              assert.ok(!JSON.stringify(notifications[0]).includes('private mail subject'));
              assert.ok(!JSON.stringify(notifications[0]).includes('evil.example'));
              const waits = [];
              handlers.notificationclick({
                notification: {data: notifications[0].options.data, close: () => {}},
                waitUntil: (promise) => waits.push(promise),
              });
              await Promise.all(waits);
              assert.deepEqual(opened, ['/ui/?task=task_7']);
              await push({kind: 'pending', risk: 'R2', task_id: '../../admin'});
              assert.equal(notifications.length, 1);
            })().catch((error) => { console.error(error); process.exitCode = 1; });
        """)
        result = subprocess.run(
            ["node", "-e", script, str(WORKER)], capture_output=True,
            text=True, check=False, timeout=20,
        )
        self.assertEqual(0, result.returncode, result.stderr)
