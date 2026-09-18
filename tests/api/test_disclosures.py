"""F04 disclosure consent API: preview, confirm, revoke and error boundaries."""

from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from personal_assistant.app import create_app
from personal_assistant.bootstrap import build_container
from personal_assistant.core.models import (
    ContextField,
    DataClassification,
    ModelRequest,
    RecipientIdentity,
    recipient_fingerprint,
)
from personal_assistant.settings import Settings

RAW = "private mail body 4242"
RAW_SECRET = "sk-live-should-never-appear"
REMOTE_IDENTITY = RecipientIdentity(
    provider_id="remote.openai",
    adapter="openai_compatible",
    endpoint="https://api.example.test/v1",
    model_id="gpt-test-1",
)
REMOTE_FP = recipient_fingerprint(REMOTE_IDENTITY)


def development_settings() -> Settings:
    environment = {
        "PA_MODEL_REMOTE_BASE_URL": "https://api.example.test/v1",
        "PA_MODEL_REMOTE_MODEL": "gpt-test-1",
        "PA_MODEL_REMOTE_SECRET_HANDLE": "handle-1",
    }
    with patch.dict(os.environ, environment, clear=True):
        return Settings.from_env()


def disclosure_body(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "provider_id": "remote.openai",
        "purpose": "draft reply",
        "instruction": "draft a reply",
        "fields": [
            {
                "name": "mail_body",
                "value": RAW,
                "classification": "SENSITIVE",
                "source": "mail:1",
            }
        ],
        "ttl_seconds": 3600,
    }
    payload.update(overrides)
    return payload


class DisclosureApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = development_settings()
        self.container = build_container(self.settings)
        self.client = TestClient(
            create_app(settings=self.settings, container=self.container)
        )

    def _preview(self, payload: dict[str, object] | None = None) -> dict[str, object]:
        response = self.client.post(
            "/api/v1/disclosures/preview",
            json=payload or disclosure_body(),
            headers={"Idempotency-Key": "preview-1"},
        )
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("no-store", response.headers["cache-control"])
        return response.json()

    def _confirm(
        self, preview: dict[str, object], key: str, payload: dict[str, object] | None = None
    ):
        body = {**(payload or disclosure_body()), "preview_hash": preview["preview_hash"]}
        return self.client.post(
            "/api/v1/disclosures", json=body, headers={"Idempotency-Key": key}
        )

    def test_preview_confirm_revoke_lifecycle(self) -> None:
        preview = self._preview()
        self.assertNotIn(RAW, json.dumps(preview))
        self.assertEqual(1, preview["protected_field_count"])
        self.assertEqual(len(RAW), preview["fields"][0]["value_length"])
        self.assertNotIn(RAW, preview["fields"][0]["redacted_preview"])
        self.assertEqual(REMOTE_FP, preview["recipient_fingerprint"])
        self.assertEqual("openai_compatible", preview["recipient"]["adapter"])
        self.assertEqual("https://api.example.test/v1", preview["recipient"]["endpoint"])

        confirmed = self._confirm(preview, "confirm-1")
        self.assertEqual(202, confirmed.status_code, confirmed.text)
        record = confirmed.json()
        self.assertNotIn(RAW, json.dumps(record))
        self.assertEqual("ACTIVE", record["state"])
        self.assertEqual(preview["field_digest"], record["field_digest"])
        self.assertEqual(0, record["version"])

        replay = self._confirm(preview, "confirm-1")
        self.assertEqual(record["id"], replay.json()["id"])

        view = self.client.get(f"/api/v1/disclosures/{record['id']}")
        self.assertEqual(200, view.status_code)
        self.assertEqual("no-store", view.headers["cache-control"])
        self.assertEqual("ACTIVE", view.json()["state"])

        request = ModelRequest(
            purpose="draft reply",
            instruction="draft a reply",
            fields=(ContextField("mail_body", RAW, DataClassification.SENSITIVE, "mail:1"),),
        )
        authorized = asyncio.run(
            self.container.disclosures.authorize(
                request,
                consent_id=record["id"],
                user_id="development-owner",
                provider_id="remote.openai",
                recipient_fingerprint=REMOTE_FP,
            )
        )
        self.assertIsNotNone(authorized)

        stale = self.client.post(
            f"/api/v1/disclosures/{record['id']}/revoke",
            json={"version": 99},
            headers={"Idempotency-Key": "revoke-stale"},
        )
        self.assertEqual(409, stale.status_code, stale.text)

        revoked = self.client.post(
            f"/api/v1/disclosures/{record['id']}/revoke",
            json={"version": record["version"]},
            headers={"Idempotency-Key": "revoke-1"},
        )
        self.assertEqual(200, revoked.status_code, revoked.text)
        self.assertEqual("REVOKED", revoked.json()["state"])
        self.assertEqual(1, revoked.json()["version"])

        replay_revoke = self.client.post(
            f"/api/v1/disclosures/{record['id']}/revoke",
            json={"version": record["version"]},
            headers={"Idempotency-Key": "revoke-1"},
        )
        self.assertEqual(revoked.json()["revoked_at"], replay_revoke.json()["revoked_at"])

        again = self.client.post(
            f"/api/v1/disclosures/{record['id']}/revoke",
            json={"version": revoked.json()["version"]},
            headers={"Idempotency-Key": "revoke-2"},
        )
        self.assertEqual(409, again.status_code)
        self.assertEqual("DISCLOSURE_STATE_ERROR", again.json()["error"]["code"])

        self.assertIsNone(
            asyncio.run(
                self.container.disclosures.authorize(
                    request,
                    consent_id=record["id"],
                    user_id="development-owner",
                    provider_id="remote.openai",
                    recipient_fingerprint=REMOTE_FP,
                )
            )
        )

    def test_idempotency_conflict_on_changed_content(self) -> None:
        preview = self._preview()
        first = self._confirm(preview, "same-key")
        self.assertEqual(202, first.status_code)
        changed_body = disclosure_body(purpose="summarize")
        changed_preview = self._preview(changed_body)
        changed = self._confirm(changed_preview, "same-key", changed_body)
        self.assertEqual(409, changed.status_code)
        self.assertEqual("IDEMPOTENCY_CONFLICT", changed.json()["error"]["code"])

    def test_secret_material_is_rejected_without_leaking(self) -> None:
        secret_body = disclosure_body(
            fields=[
                {
                    "name": "password",
                    "value": RAW_SECRET,
                    "classification": "SECRET",
                    "source": "vault",
                }
            ]
        )
        preview = self.client.post(
            "/api/v1/disclosures/preview",
            json=secret_body,
            headers={"Idempotency-Key": "preview-secret"},
        )
        self.assertEqual(403, preview.status_code, preview.text)
        self.assertEqual("DISCLOSURE_DENIED", preview.json()["error"]["code"])
        self.assertNotIn(RAW_SECRET, preview.text)

        confirmed = self.client.post(
            "/api/v1/disclosures",
            json={**secret_body, "preview_hash": "0" * 64},
            headers={"Idempotency-Key": "secret-confirm"},
        )
        self.assertEqual(403, confirmed.status_code)
        self.assertNotIn(RAW_SECRET, confirmed.text)

    def test_preview_hash_mismatch_is_conflict(self) -> None:
        preview = self._preview()
        mismatched = self.client.post(
            "/api/v1/disclosures",
            json={**disclosure_body(), "preview_hash": "f" * 64},
            headers={"Idempotency-Key": "mismatch-1"},
        )
        self.assertNotEqual(202, mismatched.status_code)
        self.assertEqual(
            "DISCLOSURE_PREVIEW_MISMATCH", mismatched.json()["error"]["code"]
        )
        del preview

    def test_command_requires_idempotency_key_and_validates_bounds(self) -> None:
        preview = self._preview()
        missing = self.client.post(
            "/api/v1/disclosures",
            json={**disclosure_body(), "preview_hash": preview["preview_hash"]},
        )
        self.assertEqual(400, missing.status_code)
        self.assertEqual("IDEMPOTENCY_KEY_REQUIRED", missing.json()["error"]["code"])

        for bad_ttl in (30, 700_000):
            with self.subTest(ttl=bad_ttl):
                response = self.client.post(
                    "/api/v1/disclosures/preview",
                    json=disclosure_body(ttl_seconds=bad_ttl),
                    headers={"Idempotency-Key": "preview-bounds"},
                )
                self.assertEqual(422, response.status_code)

        empty_fields = self.client.post(
            "/api/v1/disclosures/preview",
            json=disclosure_body(fields=[]),
            headers={"Idempotency-Key": "preview-empty"},
        )
        self.assertEqual(422, empty_fields.status_code)

    def test_unregistered_recipient_is_rejected(self) -> None:
        response = self.client.post(
            "/api/v1/disclosures/preview",
            json=disclosure_body(provider_id="remote.unconfigured"),
            headers={"Idempotency-Key": "preview-unregistered"},
        )
        self.assertEqual(404, response.status_code, response.text)
        self.assertEqual(
            "DISCLOSURE_RECIPIENT_UNKNOWN", response.json()["error"]["code"]
        )

    def test_unknown_consent_is_not_found(self) -> None:
        response = self.client.get("/api/v1/disclosures/missing-consent")
        self.assertEqual(404, response.status_code)


if __name__ == "__main__":
    unittest.main()
