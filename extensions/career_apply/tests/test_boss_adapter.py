from __future__ import annotations

import asyncio
import copy
import hashlib
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from career_apply.boss import BossAdapter, BossNeedsUser, PlaywrightBossPort

TARGET = {
    "url": "https://www.zhipin.com/job_detail/abc12345.html",
    "job_id": "abc12345",
    "company": "Fixture Company",
    "recruiter": "Fixture HR",
}
FINGERPRINT = "a" * 64


class FakePort:
    def __init__(self):
        self.calls = []
        self.snapshot = FINGERPRINT
        self.status = "READY"
        self.result = {
            "status": "SUCCEEDED",
            "state": "MESSAGE_SENT",
            "receipt": {"reference_id": "boss-dom:server-1", "verified": True},
        }
        self.execute_error = None

    async def call(self, method, argument):
        self.calls.append((method, copy.deepcopy(dict(argument))))
        if method == "prepare":
            return {
                "status": self.status,
                "reason_code": "SITE_REQUIRES_USER",
                "target": TARGET,
                "snapshot_fingerprint": self.snapshot,
                "text": argument["text"],
                "consequences": ["Send the exact reviewed message to this recruiter."],
            }
        if self.execute_error:
            raise self.execute_error
        return self.result


class BossAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.port = FakePort()
        self.adapter = BossAdapter(None, port=self.port)

    async def test_prepare_binds_exact_target_message_and_current_snapshot(self):
        envelope = await self.adapter.prepare(TARGET, "message", "Public fixture")
        self.assertEqual(
            set(envelope), {"local_action_id", "target", "operation", "consequences", "attachments"}
        )
        self.assertEqual(TARGET, envelope["target"])
        self.assertEqual("Public fixture", envelope["operation"]["text"])
        self.assertEqual(FINGERPRINT, envelope["operation"]["snapshot_fingerprint"])
        self.assertEqual([], envelope["attachments"])
        self.assertEqual(envelope, await self.adapter.prepare(TARGET, "message", "Public fixture"))
        self.assertTrue(all(method == "prepare" for method, _ in self.port.calls))

    async def test_disabled_and_unsafe_endpoints_do_not_connect(self):
        with self.assertRaises(BossNeedsUser):
            await BossAdapter(None).prepare(TARGET, "message", "Public fixture")
        for endpoint in (
            "http://localhost:9222",
            "http://example.test:9222",
            "http://127.0.0.1:9222/?token=x",
            "http://user@127.0.0.1:9222",
            "http://127.0.0.1",
            "https://127.0.0.1:9222",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                BossAdapter(endpoint)

    async def test_target_mismatch_or_page_challenge_prevents_preparation(self):
        for status in ("NEEDS_USER", "WAITING_REPLY"):
            self.port.status = status
            with self.assertRaises(BossNeedsUser):
                await self.adapter.prepare(TARGET, "message", "Public fixture")
        self.assertTrue(all(method == "prepare" for method, _ in self.port.calls))

    async def test_greet_and_unverified_remote_resume_never_dispatch(self):
        for kind in ("greet", "send_resume"):
            with self.subTest(kind=kind), self.assertRaises(BossNeedsUser):
                await self.adapter.prepare(TARGET, kind, "Public fixture", "resume.pdf")
        self.assertEqual([], self.port.calls)

    async def test_review_rechecks_fresh_snapshot_and_cannot_echo_a_stale_plan(self):
        envelope = await self.adapter.prepare(TARGET, "message", "Public fixture")
        self.assertEqual(envelope, await self.adapter.review(envelope))
        self.port.snapshot = "b" * 64
        with self.assertRaises(BossNeedsUser):
            await self.adapter.review(envelope)
        self.assertTrue(all(method == "prepare" for method, _ in self.port.calls))

    async def test_changed_target_payload_consequences_and_action_id_are_rejected(self):
        envelope = await self.adapter.prepare(TARGET, "message", "Public fixture")
        for field, value in (
            ("consequences", "Different action"),
            ("local_action_id", "boss_" + "0" * 64),
            ("attachments", [{"name": "unbound.pdf"}]),
        ):
            changed = copy.deepcopy(envelope)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(BossNeedsUser):
                await self.adapter.review(changed)

    async def test_execute_calls_browser_once_and_preserves_verified_receipt(self):
        envelope = await self.adapter.prepare(TARGET, "message", "Public fixture")
        result = await self.adapter.execute(envelope)
        self.assertEqual("SUCCEEDED", result["status"])
        self.assertTrue(result["receipt"]["verified"])
        self.assertEqual(1, sum(method == "execute" for method, _ in self.port.calls))

    async def test_timeout_and_cancellation_after_dispatch_are_unknown(self):
        envelope = await self.adapter.prepare(TARGET, "message", "Public fixture")
        for error in (TimeoutError(), asyncio.CancelledError(), RuntimeError("private detail")):
            port = FakePort()
            port.execute_error = error
            result = await BossAdapter(None, port=port).execute(envelope)
            self.assertEqual("UNKNOWN", result["status"])
            self.assertNotIn("private detail", str(result))

    async def test_success_without_a_verified_receipt_is_unknown(self):
        envelope = await self.adapter.prepare(TARGET, "message", "Public fixture")
        for result in (
            {"status": "SUCCEEDED"},
            {"status": "SUCCEEDED", "receipt": {}},
            {"status": "SUCCEEDED", "receipt": {"verified": False}},
        ):
            port = FakePort()
            port.result = result
            self.assertEqual(
                "UNKNOWN", (await BossAdapter(None, port=port).execute(envelope))["status"]
            )

    async def test_changed_page_before_execution_has_zero_dispatch(self):
        envelope = await self.adapter.prepare(TARGET, "message", "Public fixture")
        self.port.snapshot = "b" * 64
        result = await self.adapter.execute(envelope)
        self.assertEqual("NEEDS_USER", result["status"])
        self.assertFalse(any(method == "execute" for method, _ in self.port.calls))

    async def test_unknown_is_not_dispatched_again_in_the_same_worker(self):
        envelope = await self.adapter.prepare(TARGET, "message", "Public fixture")
        self.port.execute_error = TimeoutError()
        first = await self.adapter.execute(envelope)
        second = await self.adapter.execute(envelope)
        self.assertEqual("UNKNOWN", first["status"])
        self.assertEqual(first, second)
        self.assertEqual(1, sum(method == "execute" for method, _ in self.port.calls))


class BossResourceTests(unittest.TestCase):
    def test_payload_resources_take_priority_and_wheel_uses_prefix_share(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "payload"
            installed = root / "venv"
            fake_module = payload / "src" / "career_apply" / "boss.py"
            expected_wheel = installed / "share" / "career-apply" / "browser"
            with (
                patch("career_apply.boss.__file__", str(fake_module)),
                patch("career_apply.boss.sys.prefix", str(installed)),
            ):
                self.assertEqual(
                    expected_wheel,
                    PlaywrightBossPort(
                        "http://127.0.0.1:9222",
                    )._scripts,
                )
                browser = payload / "browser"
                browser.mkdir(parents=True)
                (browser / "boss-dom.js").write_text("fixture", encoding="utf-8")
                (browser / "boss-action.js").write_text("fixture", encoding="utf-8")
                self.assertEqual(
                    browser,
                    PlaywrightBossPort(
                        "http://127.0.0.1:9222",
                    )._scripts,
                )
                (browser / "boss-action.js").unlink()
                self.assertEqual(
                    expected_wheel,
                    PlaywrightBossPort(
                        "http://127.0.0.1:9222",
                    )._scripts,
                )


class FakeArtifacts:
    def __init__(self, data=b"%PDF-1.4\nsynthetic resume\n%%EOF"):
        self.data = data

    async def read(self, artifact_id):
        if artifact_id != "art_fixture":
            raise ValueError("unknown artifact")
        return self.data

    def metadata(self):
        return {
            "artifact_id": "art_fixture",
            "filename": "resume.pdf",
            "sha256": hashlib.sha256(self.data).hexdigest(),
            "size_bytes": len(self.data),
            "media_type": "application/pdf",
        }


class BossResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_metadata_without_matching_owned_bytes_cannot_prepare(self):
        artifacts = FakeArtifacts()
        adapter = BossAdapter(None, port=FakePort(), artifacts=artifacts)
        metadata = artifacts.metadata()
        metadata["sha256"] = "0" * 64
        with self.assertRaises(BossNeedsUser):
            await adapter.prepare(TARGET, "send_resume", resume=metadata)


HTML = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<section class="chat-container"><header class="chat-header">
<a href="/job_detail/abc12345.html">Fixture Job</a>
<span class="company-name">Fixture Company</span><span class="boss-name">Fixture HR</span>
</header><div class="message-controls"><textarea></textarea><button>发送</button></div>
<div id="outgoing"></div></section>
<script>
window.clicks=0;window.acknowledge=true;
Object.defineProperty(document,"cookie",{get(){throw Error("cookie access forbidden");}});
for(const key of ["localStorage","sessionStorage"]){
 Object.defineProperty(window,key,{get(){throw Error("storage forbidden");}});
}
document.querySelector("button").onclick=()=>{
 window.clicks++;
 if(window.acknowledge){
 const item=document.createElement("div");item.dataset.careerBossSelfMessage="";
 item.dataset.messageId="server-"+window.clicks;item.dataset.deliveryState="sent";
 const text=document.createElement("span");text.className="message-text";
 text.textContent=document.querySelector("textarea").value;item.append(text);
 document.querySelector("#outgoing").append(item);document.querySelector("textarea").value="";
 }
};
</script></body></html>"""


class BossRealChromiumTests(unittest.IsolatedAsyncioTestCase):
    async def test_owned_chromium_cdp_exact_message_and_unknown_replay(self):
        from playwright.async_api import async_playwright

        # Fresh owned profile and intercepted origin: never the user's browser or real BOSS.
        with tempfile.TemporaryDirectory() as profile:
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            async with async_playwright() as playwright:
                context = await playwright.chromium.launch_persistent_context(
                    profile,
                    headless=True,
                    channel="chrome",
                    args=[
                        f"--remote-debugging-port={port}",
                        "--remote-debugging-address=127.0.0.1",
                    ],
                )
                try:
                    await context.route(
                        "**/*",
                        lambda route: route.fulfill(
                            status=200,
                            content_type="text/html",
                            body=HTML,
                        ),
                    )
                    page = context.pages[0]
                    await page.goto("https://www.zhipin.com/web/geek/chat")
                    scripts = Path(__file__).resolve().parents[1] / "browser"
                    await page.evaluate((scripts / "boss-dom.js").read_text(encoding="utf-8"))
                    observation = await page.evaluate(
                        "a => CareerBossDOM.prepareAction(a,'message','Public fixture','')",
                        TARGET,
                    )
                    self.assertEqual(
                        "READY",
                        observation["status"],
                        (
                            observation,
                            await page.evaluate(
                                "a => CareerBossDOM.inspectConversation(document,a)",
                                TARGET,
                            ),
                        ),
                    )
                    adapter = BossAdapter(f"http://127.0.0.1:{port}")
                    envelope = await adapter.prepare(TARGET, "message", "Public fixture")
                    result = await adapter.execute(envelope)
                    self.assertEqual("SUCCEEDED", result["status"])
                    self.assertTrue(result["receipt"]["verified"])
                    self.assertFalse(page.is_closed())
                    self.assertEqual(1, await page.evaluate("window.clicks"))
                    self.assertEqual(result, await adapter.execute(envelope))
                    self.assertEqual(1, await page.evaluate("window.clicks"))
                    await page.reload()
                    await page.evaluate("window.acknowledge=false")
                    uncertain = BossAdapter(f"http://127.0.0.1:{port}")
                    pending = await uncertain.prepare(TARGET, "message", "Lost fixture receipt")
                    first = await uncertain.execute(pending)
                    self.assertEqual("UNKNOWN", first["status"])
                    self.assertEqual(first, await uncertain.execute(pending))
                    self.assertEqual(1, await page.evaluate("window.clicks"))
                    self.assertFalse(page.is_closed())
                finally:
                    await context.close()


RESUME_HTML = HTML.replace(
    "<button>发送</button></div>",
    "<button>发送</button>"
    '<div data-career-boss-upload-area>'
    '<input id="upload" type="file" accept="application/pdf"></div>'
    '<button id="send-resume">发简历</button><div id="uploads"></div>'
    '<div id="selected" data-career-boss-selected-upload></div></div>',
)
RESUME_HTML = RESUME_HTML.replace(
    "</body>",
    """<script>
window.uploadCount=0;window.resumeClicks=0;window.uploadAck=true;window.resumeAck=true;
document.querySelector("#upload").onchange=()=>{
 window.uploadCount++;
 if(!window.uploadAck)return;
 const file=document.querySelector("#upload").files[0];
 const item=document.createElement("div");item.dataset.careerBossUpload="";
 item.dataset.uploadId="upload-"+window.uploadCount;item.dataset.fileSize=String(file.size);
 item.dataset.uploadState="uploaded";
 const name=document.createElement("span");name.className="resume-name";name.textContent=file.name;
 item.append(name);document.querySelector("#uploads").append(item);
 const selected=document.querySelector("#selected");selected.dataset.uploadId=item.dataset.uploadId;
 selected.dataset.fileSize=String(file.size);selected.replaceChildren(name.cloneNode(true));
};
document.querySelector("#send-resume").onclick=()=>{
 window.resumeClicks++;
 if(!window.resumeAck)return;
 const selected=document.querySelector("#selected");const item=document.createElement("div");
 item.dataset.careerBossResumeMessage="";item.dataset.messageId="resume-"+window.resumeClicks;
 item.dataset.uploadId=selected.dataset.uploadId;item.dataset.fileSize=selected.dataset.fileSize;
 item.dataset.deliveryState="sent";item.append(selected.firstElementChild.cloneNode(true));
 document.querySelector("#outgoing").append(item);
};
</script></body>""",
)


class BossRealChromiumResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_new_resume_upload_send_and_partial_unknown_are_not_repeated(self):
        from playwright.async_api import async_playwright

        artifacts = FakeArtifacts()
        with tempfile.TemporaryDirectory() as profile:
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            async with async_playwright() as playwright:
                context = await playwright.chromium.launch_persistent_context(
                    profile,
                    headless=True,
                    channel="chrome",
                    args=[
                        f"--remote-debugging-port={port}",
                        "--remote-debugging-address=127.0.0.1",
                    ],
                )
                try:
                    await context.route(
                        "**/*",
                        lambda route: route.fulfill(
                            status=200,
                            content_type="text/html",
                            body=RESUME_HTML,
                        ),
                    )
                    page = context.pages[0]
                    for scenario in ("confirmed", "upload-receipt-lost", "send-receipt-lost"):
                        await page.goto("https://www.zhipin.com/web/geek/chat")
                        if scenario == "upload-receipt-lost":
                            await page.evaluate("window.uploadAck=false")
                        if scenario == "send-receipt-lost":
                            await page.evaluate("window.resumeAck=false")
                        adapter = BossAdapter(f"http://127.0.0.1:{port}", artifacts=artifacts)
                        envelope = await adapter.prepare(
                            TARGET,
                            "send_resume",
                            resume=artifacts.metadata(),
                        )
                        self.assertEqual(
                            artifacts.metadata()["sha256"], envelope["attachments"][0]["sha256"]
                        )
                        result = await adapter.execute(envelope)
                        self.assertEqual(
                            "SUCCEEDED" if scenario == "confirmed" else "UNKNOWN",
                            result["status"],
                            scenario,
                        )
                        if scenario == "confirmed":
                            self.assertEqual("RESUME_SENT", result["state"])
                            self.assertTrue(result["receipt"]["verified"])
                        self.assertEqual(result, await adapter.execute(envelope))
                        self.assertEqual(1, await page.evaluate("window.uploadCount"))
                        self.assertEqual(
                            0 if scenario == "upload-receipt-lost" else 1,
                            await page.evaluate("window.resumeClicks"),
                        )
                        self.assertFalse(page.is_closed())
                finally:
                    await context.close()
