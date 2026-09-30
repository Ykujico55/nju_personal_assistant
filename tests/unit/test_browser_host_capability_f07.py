"""F07: host browser capability surfaces risk evidence and companion auth."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from personal_assistant.core.browser import PageSnapshot, RiskSignal
from personal_assistant.domain.enums import RiskLevel
from personal_assistant.infrastructure.browser.companion import DesktopCompanion
from personal_assistant.infrastructure.browser.companion_app import create_companion_app
from personal_assistant.infrastructure.browser.host import (
    HOST_BROWSER_NAVIGATE,
    BrowserCapabilityContext,
    BrowserHostCapability,
)

ORIGIN = "https://ehall.example.test"


class _ProhibitedBroker:
    async def navigate(self, session_id: str, **kwargs: object) -> PageSnapshot:
        del session_id, kwargs
        return PageSnapshot(
            session_id="s1",
            url=ORIGIN + "/apps/proof",
            origin=ORIGIN,
            path="/apps/proof",
            title="proof",
            fingerprint="f" * 64,
            captured_at=datetime(2026, 9, 21, 12, 0, tzinfo=UTC),
            signals=(
                RiskSignal(
                    code="PROHIBITED_TERM",
                    detail="COURSE_WITHDRAWAL",
                    risk=RiskLevel.PROHIBITED,
                ),
            ),
        )


class NavigationRiskTests(unittest.IsolatedAsyncioTestCase):
    async def test_navigation_snapshot_reports_prohibited_risk_evidence(self) -> None:
        capability = BrowserHostCapability(_ProhibitedBroker())  # type: ignore[arg-type]
        view = await capability.handle(
            HOST_BROWSER_NAVIGATE,
            {
                "session_id": "s1",
                "adapter_id": "nju.ehall.proof",
                "transaction_id": "proof.apply",
                "url": ORIGIN + "/apps/proof",
            },
            context=BrowserCapabilityContext(
                extension_id="nju.ehall", extension_version="0.1.0"
            ),
        )
        self.assertEqual("PROHIBITED", view["risk"])
        self.assertEqual(["COURSE_WITHDRAWAL"], view["risk_categories"])
        self.assertTrue(view["escalated"])


class _FakeDriver:
    async def start(self) -> None:
        return None


class CompanionAuthOrderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.companion = DesktopCompanion(
            driver_factory=lambda **kwargs: _FakeDriver()
        )
        self.client = TestClient(
            create_companion_app(self.companion), raise_server_exceptions=False
        )
        self.addCleanup(self.client.close)

    def _create_session(self) -> str:
        response = self.client.post(
            "/v1/sessions",
            json={
                "session_id": "s1",
                "purpose": "test",
                "allowed_origins": [ORIGIN],
                "task_id": "task-1",
                "extension_id": "nju.ehall",
            },
            headers={"Authorization": f"Bearer {self.companion.root_capability}"},
        )
        self.assertEqual(200, response.status_code, response.text)
        return "s1"

    def test_session_route_rejects_before_parsing_the_body(self) -> None:
        self._create_session()
        response = self.client.post(
            "/v1/sessions/s1/navigate",
            content=b"{not-json",
            headers={
                "Authorization": "Bearer wrong-capability",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(401, response.status_code, response.text)
        self.assertEqual("CAPABILITY_INVALID", response.json()["error"]["code"])

    def test_oversized_body_is_rejected_before_routing(self) -> None:
        response = self.client.post(
            "/v1/sessions",
            content=b"x" * (128 * 1024),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(413, response.status_code, response.text)
        self.assertEqual("REQUEST_TOO_LARGE", response.json()["error"]["code"])

    def test_chunked_body_without_content_length_is_rejected(self) -> None:
        # A chunked request has no Content-Length: only counting the real
        # received bytes can stop it.
        def chunks():
            for _ in range(5):
                yield b"x" * (32 * 1024)

        response = self.client.post(
            "/v1/sessions",
            content=chunks(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.companion.root_capability}",
            },
        )
        self.assertEqual(413, response.status_code, response.text)
        self.assertEqual("REQUEST_TOO_LARGE", response.json()["error"]["code"])

    def test_create_and_revoke_require_root_before_parsing(self) -> None:
        for path in ("/v1/sessions", "/v1/revoke"):
            with self.subTest(path=path):
                response = self.client.post(
                    path,
                    content=b"{not-json",
                    headers={
                        "Authorization": "Bearer wrong-capability",
                        "Content-Type": "application/json",
                    },
                )
                self.assertEqual(401, response.status_code, response.text)
                self.assertEqual(
                    "CAPABILITY_INVALID", response.json()["error"]["code"]
                )


if __name__ == "__main__":
    unittest.main()
