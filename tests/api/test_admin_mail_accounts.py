"""F06: host-owned mail account registry Admin API (loopback only)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from personal_assistant.admin_app import create_app
from personal_assistant.bootstrap import build_container
from personal_assistant.settings import Settings


def _settings(root: Path) -> Settings:
    return Settings(
        environment="development",
        log_level="INFO",
        public_host="127.0.0.1",
        public_port=8000,
        admin_host="127.0.0.1",
        admin_port=8001,
        health_host="127.0.0.1",
        health_port=8010,
        storage_backend="memory",
        database_url="postgresql+asyncpg://assistant:change-me@127.0.0.1:5432/assistant",
        extension_root=root,
        artifact_root=root / "artifacts",
        trust_cloudflare_access=False,
        public_origin=None,
        cf_access_team_domain=None,
        cf_access_aud=None,
    )


class MailAccountAdminApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="pa_f06_admin_")
        self.root = Path(self._directory.name)
        self.container = build_container(_settings(self.root))
        self.client = TestClient(
            create_app(settings=_settings(self.root), container=self.container)
        )
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self._directory.cleanup()

    def _account(self) -> dict[str, object]:
        return {
            "account_id": "nju",
            "address": "student@smail.nju.edu.cn",
            "imap_host": "imap.example.test",
            "imap_port": 993,
            "smtp_host": "smtp.example.test",
            "smtp_port": 465,
            "secret_handle_id": "smail-handle-1",
            "display_name": "NJU",
            "read_enabled": True,
            "send_enabled": True,
            "tls_mode": "auto",
        }

    def test_registry_roundtrip_never_returns_secret_values(self) -> None:
        empty = self.client.get("/admin/v1/mail/accounts")
        self.assertEqual(200, empty.status_code)
        self.assertEqual([], empty.json()["accounts"])
        updated = self.client.put(
            "/admin/v1/mail/accounts",
            json={"accounts": [self._account()]},
            headers={"Idempotency-Key": "mail-accounts-1"},
        )
        self.assertEqual(200, updated.status_code, updated.text)
        listing = self.client.get("/admin/v1/mail/accounts").json()["accounts"]
        self.assertEqual(1, len(listing))
        self.assertEqual("smail-handle-1", listing[0]["secret_handle_id"])
        self.assertEqual(64, len(listing[0]["fingerprint"]))
        # No password field is ever accepted or returned.
        self.assertNotIn("password", str(updated.json()).lower())
        self.assertNotIn("secret_value", str(updated.json()).lower())

    def test_unknown_fields_and_duplicate_ids_are_rejected(self) -> None:
        bad = self._account()
        bad["password"] = "hunter2"
        response = self.client.put(
            "/admin/v1/mail/accounts",
            json={"accounts": [bad]},
            headers={"Idempotency-Key": "mail-accounts-2"},
        )
        self.assertEqual(422, response.status_code)
        duplicated = self.client.put(
            "/admin/v1/mail/accounts",
            json={"accounts": [self._account(), self._account()]},
            headers={"Idempotency-Key": "mail-accounts-3"},
        )
        self.assertEqual(422, duplicated.status_code)


    def test_second_put_returns_stored_generation_and_fingerprint(self) -> None:
        first = self.client.put(
            "/admin/v1/mail/accounts",
            json={"accounts": [self._account()]},
            headers={"Idempotency-Key": "mail-accounts-r1"},
        ).json()["accounts"][0]
        second = self.client.put(
            "/admin/v1/mail/accounts",
            json={"accounts": [self._account()]},
            headers={"Idempotency-Key": "mail-accounts-r2"},
        ).json()["accounts"][0]
        self.assertEqual(0, first["generation"])
        self.assertEqual(1, second["generation"])
        listing = self.client.get("/admin/v1/mail/accounts").json()["accounts"][0]
        self.assertEqual(second["fingerprint"], listing["fingerprint"])
        self.assertEqual(1, listing["generation"])


if __name__ == "__main__":
    unittest.main()
