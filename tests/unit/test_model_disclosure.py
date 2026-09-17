"""F04 persistent model-disclosure consent: exact binding and lifecycle.

These tests exercise the real ``DisclosureConsentService`` against the in-memory
test double. They are the executable specification for the PostgreSQL adapter
in ``tests/integration/test_postgres_f04.py``.
"""

from __future__ import annotations

import json
import unittest
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from personal_assistant.core.models import (
    ContextField,
    DataClassification,
    ModelRequest,
    RecipientIdentity,
    canonical_field_digest,
    redacted_field_preview,
)
from personal_assistant.core.models.disclosure import (
    DEFAULT_DISCLOSURE_TTL,
    DISCLOSURE_POLICY_VERSION,
    MAX_DISCLOSURE_TTL,
    DisclosureConsentRecord,
    DisclosureConsentService,
    DisclosureConsentState,
    DisclosureDeniedError,
    DisclosureIdempotencyConflictError,
    DisclosurePreviewMismatchError,
    DisclosureRecipientUnknownError,
    DisclosureStateError,
    effective_consent_state,
    recipient_fingerprint,
)
from personal_assistant.domain import ConcurrentModificationError, NotFoundError, ValidationError
from personal_assistant.infrastructure.memory import InMemoryDisclosureConsentStore

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


class ForeignClassification(StrEnum):
    """A SECRET-valued StrEnum that is not the core ``DataClassification``."""

    SECRET = "SECRET"


RAW_SENSITIVE = "private mail body 42"
RAW_PERSONAL = "personal note"

REMOTE_IDENTITY = RecipientIdentity(
    provider_id="remote.openai",
    adapter="openai_compatible",
    endpoint="https://api.example.test/v1",
    model_id="gpt-test-1",
)
OTHER_IDENTITY = RecipientIdentity(
    provider_id="remote.other",
    adapter="openai_compatible",
    endpoint="https://other.example.test/v1",
    model_id="gpt-test-2",
)
RECIPIENT_FP = recipient_fingerprint(REMOTE_IDENTITY)
OTHER_FP = recipient_fingerprint(OTHER_IDENTITY)
RECIPIENTS = {
    REMOTE_IDENTITY.provider_id: REMOTE_IDENTITY,
    OTHER_IDENTITY.provider_id: OTHER_IDENTITY,
}


def field_request(
    *,
    value: str = RAW_SENSITIVE,
    classification: DataClassification = DataClassification.SENSITIVE,
    source: str = "mail:1",
    purpose: str = "draft reply",
) -> ModelRequest:
    return ModelRequest(
        purpose=purpose,
        instruction="draft",
        fields=(
            ContextField("mail_body", value, classification, source),
            ContextField("note", RAW_PERSONAL, DataClassification.PERSONAL, "note:1"),
        ),
    )


class DisclosureConsentServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.service = DisclosureConsentService(
            InMemoryDisclosureConsentStore(), recipients=RECIPIENTS
        )

    def test_digest_is_order_independent_and_binds_every_component(self) -> None:
        base = ModelRequest(
            purpose="draft",
            instruction="i",
            fields=(
                ContextField("a", "value-a", DataClassification.SENSITIVE, "src:a"),
                ContextField("b", "value-b", DataClassification.PERSONAL, "src:b"),
            ),
        )
        reordered = ModelRequest(
            purpose="draft",
            instruction="i",
            fields=tuple(reversed(base.fields)),
        )
        self.assertEqual(
            canonical_field_digest(base.fields), canonical_field_digest(reordered.fields)
        )

        variants = {
            "value": ContextField("a", "changed", DataClassification.SENSITIVE, "src:a"),
            "classification": ContextField("a", "value-a", DataClassification.PERSONAL, "src:a"),
            "source": ContextField("a", "value-a", DataClassification.SENSITIVE, "src:other"),
            "name": ContextField("renamed", "value-a", DataClassification.SENSITIVE, "src:a"),
        }
        base_digest = canonical_field_digest(base.fields)
        for label, changed in variants.items():
            with self.subTest(label=label):
                candidate = ModelRequest(
                    purpose="draft",
                    instruction="i",
                    fields=(changed, base.fields[1]),
                )
                self.assertNotEqual(base_digest, canonical_field_digest(candidate.fields))

    def test_preview_never_contains_raw_values(self) -> None:
        preview = self.service.preview(field_request(), provider_id="remote.openai", now=NOW)
        rendered = json.dumps(asdict(preview), default=str, ensure_ascii=False)
        self.assertNotIn(RAW_SENSITIVE, rendered)
        self.assertNotIn(RAW_PERSONAL, rendered)
        self.assertEqual("remote.openai", preview.provider_id)
        self.assertEqual("draft reply", preview.purpose)
        self.assertEqual(2, preview.protected_field_count)
        summaries = {item.name: item for item in preview.fields}
        self.assertEqual(64, len(summaries["mail_body"].value_sha256))
        self.assertEqual(len(RAW_SENSITIVE), summaries["mail_body"].value_length)
        self.assertEqual(DEFAULT_DISCLOSURE_TTL, preview.ttl)

    def test_preview_rejects_secret_fields(self) -> None:
        request = ModelRequest(
            purpose="draft",
            instruction="i",
            fields=(ContextField("password", "hunter2", DataClassification.SECRET, "vault"),),
        )
        with self.assertRaises(DisclosureDeniedError):
            self.service.preview(request, provider_id="local.ollama", now=NOW)

    def test_preview_rejects_foreign_and_string_secret_classifications(self) -> None:
        for label, classification in (
            ("foreign-enum", ForeignClassification.SECRET),
            ("plain-string", "SECRET"),
        ):
            with self.subTest(label=label):
                request = ModelRequest(
                    purpose="draft",
                    instruction="i",
                    fields=(
                        ContextField("password", "hunter2", classification, "vault"),
                    ),
                )
                self.assertIs(
                    DataClassification.SECRET, request.fields[0].classification
                )
                with self.assertRaises(DisclosureDeniedError):
                    self.service.preview(request, provider_id="remote.openai", now=NOW)

    def test_invalid_classification_is_rejected_at_construction(self) -> None:
        with self.assertRaises(ValidationError):
            ContextField("password", "hunter2", "TOP_SECRET", "vault")

    async def test_confirm_requires_matching_preview_hash(self) -> None:
        request = field_request()
        preview = self.service.preview(request, provider_id="remote.openai", now=NOW)
        with self.assertRaises(DisclosurePreviewMismatchError):
            await self.service.confirm(
                request,
                provider_id="remote.openai",
                user_id="owner",
                preview_hash="0" * 64,
                idempotency_key="confirm-1",
                now=NOW,
            )
        # A changed field must also invalidate the preview the user saw.
        changed = field_request(value="changed body")
        with self.assertRaises(DisclosurePreviewMismatchError):
            await self.service.confirm(
                changed,
                provider_id="remote.openai",
                user_id="owner",
                preview_hash=preview.preview_hash,
                idempotency_key="confirm-2",
                now=NOW,
            )
        record = await self.service.confirm(
            request,
            provider_id="remote.openai",
            user_id="owner",
            preview_hash=preview.preview_hash,
            idempotency_key="confirm-3",
            now=NOW,
        )
        self.assertEqual(DisclosureConsentState.ACTIVE, record.state)
        self.assertEqual(preview.field_digest, record.field_digest)
        self.assertEqual(NOW, record.created_at)
        self.assertEqual(NOW + DEFAULT_DISCLOSURE_TTL, record.expires_at)
        self.assertEqual(0, record.version)

    async def test_confirm_is_idempotent_and_conflicts_on_new_content(self) -> None:
        request = field_request()
        preview = self.service.preview(request, provider_id="remote.openai", now=NOW)
        first = await self.service.confirm(
            request,
            provider_id="remote.openai",
            user_id="owner",
            preview_hash=preview.preview_hash,
            idempotency_key="confirm-1",
            now=NOW,
        )
        replay = await self.service.confirm(
            request,
            provider_id="remote.openai",
            user_id="owner",
            preview_hash=preview.preview_hash,
            idempotency_key="confirm-1",
            now=NOW + timedelta(seconds=5),
        )
        self.assertEqual(first.id, replay.id)
        self.assertEqual(first.created_at, replay.created_at)

        changed = field_request(purpose="summarize")
        changed_preview = self.service.preview(changed, provider_id="remote.openai", now=NOW)
        with self.assertRaises(DisclosureIdempotencyConflictError):
            await self.service.confirm(
                changed,
                provider_id="remote.openai",
                user_id="owner",
                preview_hash=changed_preview.preview_hash,
                idempotency_key="confirm-1",
                now=NOW,
            )

    async def test_unregistered_recipient_is_rejected_before_preview(self) -> None:
        request = field_request()
        with self.assertRaises(DisclosureRecipientUnknownError):
            self.service.preview(request, provider_id="remote.unconfigured", now=NOW)
        preview = self.service.preview(request, provider_id="remote.openai", now=NOW)
        with self.assertRaises(DisclosureRecipientUnknownError):
            await self.service.confirm(
                request,
                provider_id="remote.unconfigured",
                user_id="owner",
                preview_hash=preview.preview_hash,
                idempotency_key="confirm-unregistered",
                now=NOW,
            )

    def test_recipient_fingerprint_binds_endpoint_adapter_and_model(self) -> None:
        fingerprints = {
            RECIPIENT_FP,
            recipient_fingerprint(replace(REMOTE_IDENTITY, endpoint="https://other.test/v1")),
            recipient_fingerprint(replace(REMOTE_IDENTITY, adapter="other_adapter")),
            recipient_fingerprint(replace(REMOTE_IDENTITY, model_id="gpt-test-2")),
            recipient_fingerprint(replace(REMOTE_IDENTITY, provider_id="remote.other")),
        }
        self.assertEqual(5, len(fingerprints))

    async def test_repointed_provider_cannot_reuse_an_old_consent(self) -> None:
        store = InMemoryDisclosureConsentStore()
        original = DisclosureConsentService(store, recipients=RECIPIENTS)
        repointed_identity = replace(
            REMOTE_IDENTITY, endpoint="https://elsewhere.example.test/v1"
        )
        repointed = DisclosureConsentService(
            store, recipients={"remote.openai": repointed_identity}
        )
        request = field_request()
        preview = original.preview(request, provider_id="remote.openai", now=NOW)
        record = await original.confirm(
            request,
            provider_id="remote.openai",
            user_id="owner",
            preview_hash=preview.preview_hash,
            idempotency_key="confirm-1",
            now=NOW,
        )
        self.assertIsNone(
            await repointed.authorize(
                request,
                consent_id=record.id,
                user_id="owner",
                provider_id="remote.openai",
                recipient_fingerprint=recipient_fingerprint(repointed_identity),
                now=NOW,
            )
        )
        self.assertIsNotNone(
            await original.authorize(
                request,
                consent_id=record.id,
                user_id="owner",
                provider_id="remote.openai",
                recipient_fingerprint=RECIPIENT_FP,
                now=NOW,
            )
        )

    async def test_authorize_requires_matching_recipient_fingerprint(self) -> None:
        request = field_request()
        preview = self.service.preview(request, provider_id="remote.openai", now=NOW)
        record = await self.service.confirm(
            request,
            provider_id="remote.openai",
            user_id="owner",
            preview_hash=preview.preview_hash,
            idempotency_key="confirm-1",
            now=NOW,
        )
        self.assertIsNone(
            await self.service.authorize(
                request,
                consent_id=record.id,
                user_id="owner",
                provider_id="remote.openai",
                recipient_fingerprint=OTHER_FP,
                now=NOW,
            )
        )

    async def test_authorize_requires_exact_binding(self) -> None:
        request = field_request()
        preview = self.service.preview(request, provider_id="remote.openai", now=NOW)
        record = await self.service.confirm(
            request,
            provider_id="remote.openai",
            user_id="owner",
            preview_hash=preview.preview_hash,
            idempotency_key="confirm-1",
            now=NOW,
        )
        authorized = await self.service.authorize(
            request,
            consent_id=record.id,
            user_id="owner",
            provider_id="remote.openai",
            recipient_fingerprint=RECIPIENT_FP,
            now=NOW + timedelta(minutes=1),
        )
        self.assertIsNotNone(authorized)

        denied_cases = {
            "provider": {"provider_id": "remote.other"},
            "purpose": {},
            "consent": {"consent_id": "missing-consent"},
            "user": {"user_id": "someone-else"},
        }
        for label, overrides in denied_cases.items():
            with self.subTest(label=label):
                arguments = {
                    "consent_id": record.id,
                    "user_id": "owner",
                    "provider_id": "remote.openai",
                    "recipient_fingerprint": RECIPIENT_FP,
                    **overrides,
                }
                candidate = request
                if label == "purpose":
                    candidate = field_request(purpose="summarize")
                result = await self.service.authorize(
                    candidate, now=NOW + timedelta(minutes=1), **arguments
                )
                self.assertIsNone(result)

        for changed in (
            field_request(value="changed"),
            field_request(classification=DataClassification.PERSONAL),
            field_request(source="mail:2"),
        ):
            self.assertIsNone(
                await self.service.authorize(
                    changed,
                    consent_id=record.id,
                    user_id="owner",
                    provider_id="remote.openai",
                    recipient_fingerprint=RECIPIENT_FP,
                    now=NOW + timedelta(minutes=1),
                )
            )

    async def test_expiry_denies_authorization(self) -> None:
        request = field_request()
        preview = self.service.preview(
            request, provider_id="remote.openai", ttl=timedelta(minutes=1), now=NOW
        )
        record = await self.service.confirm(
            request,
            provider_id="remote.openai",
            user_id="owner",
            preview_hash=preview.preview_hash,
            idempotency_key="confirm-1",
            ttl=timedelta(minutes=1),
            now=NOW,
        )
        self.assertIsNone(
            await self.service.authorize(
                request,
                consent_id=record.id,
                user_id="owner",
                provider_id="remote.openai",
                recipient_fingerprint=RECIPIENT_FP,
                now=NOW + timedelta(minutes=1),
            )
        )
        self.assertEqual(
            DisclosureConsentState.EXPIRED,
            effective_consent_state(record, NOW + timedelta(minutes=2)),
        )
        self.assertEqual(
            DisclosureConsentState.ACTIVE,
            effective_consent_state(record, NOW + timedelta(seconds=30)),
        )

    async def test_revoke_is_terminal_and_replay_safe(self) -> None:
        request = field_request()
        preview = self.service.preview(request, provider_id="remote.openai", now=NOW)
        record = await self.service.confirm(
            request,
            provider_id="remote.openai",
            user_id="owner",
            preview_hash=preview.preview_hash,
            idempotency_key="confirm-1",
            now=NOW,
        )
        revoked = await self.service.revoke(
            record.id,
            user_id="owner",
            expected_version=record.version,
            idempotency_key="revoke-1",
            now=NOW + timedelta(minutes=1),
        )
        self.assertEqual(DisclosureConsentState.REVOKED, revoked.state)
        self.assertEqual(1, revoked.version)
        replay = await self.service.revoke(
            record.id,
            user_id="owner",
            expected_version=record.version,
            idempotency_key="revoke-1",
            now=NOW + timedelta(minutes=2),
        )
        self.assertEqual(revoked.version, replay.version)
        self.assertEqual(revoked.revoked_at, replay.revoked_at)
        self.assertIsNone(
            await self.service.authorize(
                request,
                consent_id=record.id,
                user_id="owner",
                provider_id="remote.openai",
                recipient_fingerprint=RECIPIENT_FP,
                now=NOW + timedelta(minutes=1),
            )
        )
        with self.assertRaises(DisclosureStateError):
            await self.service.revoke(
                record.id,
                user_id="owner",
                expected_version=revoked.version,
                idempotency_key="revoke-2",
                now=NOW + timedelta(minutes=3),
            )
        with self.assertRaises(ConcurrentModificationError):
            await self.service.revoke(
                record.id,
                user_id="owner",
                expected_version=record.version,
                idempotency_key="revoke-3",
                now=NOW + timedelta(minutes=3),
            )
        with self.assertRaises(NotFoundError):
            await self.service.revoke(
                record.id,
                user_id="intruder",
                expected_version=record.version,
                idempotency_key="revoke-4",
                now=NOW + timedelta(minutes=3),
            )

    async def test_unicode_binding_values_do_not_crash_authorize(self) -> None:
        request = field_request(purpose="撰写回复")
        preview = self.service.preview(request, provider_id="remote.openai", now=NOW)
        record = await self.service.confirm(
            request,
            provider_id="remote.openai",
            user_id="用户-甲",
            preview_hash=preview.preview_hash,
            idempotency_key="确认-1",
            now=NOW,
        )
        authorized = await self.service.authorize(
            request,
            consent_id=record.id,
            user_id="用户-甲",
            provider_id="remote.openai",
            recipient_fingerprint=RECIPIENT_FP,
            now=NOW,
        )
        self.assertIsNotNone(authorized)
        self.assertIsNone(
            await self.service.authorize(
                field_request(purpose="总结要点"),
                consent_id=record.id,
                user_id="用户-甲",
                provider_id="remote.openai",
                recipient_fingerprint=RECIPIENT_FP,
                now=NOW,
            )
        )
        revoked = await self.service.revoke(
            record.id,
            user_id="用户-甲",
            expected_version=record.version,
            idempotency_key="撤销-1",
            now=NOW + timedelta(minutes=1),
        )
        self.assertEqual(DisclosureConsentState.REVOKED, revoked.state)

    async def test_expired_consent_cannot_be_revoked(self) -> None:
        request = field_request()
        preview = self.service.preview(
            request, provider_id="remote.openai", ttl=timedelta(minutes=1), now=NOW
        )
        record = await self.service.confirm(
            request,
            provider_id="remote.openai",
            user_id="owner",
            preview_hash=preview.preview_hash,
            idempotency_key="confirm-short",
            ttl=timedelta(minutes=1),
            now=NOW,
        )
        with self.assertRaises(DisclosureStateError):
            await self.service.revoke(
                record.id,
                user_id="owner",
                expected_version=record.version,
                idempotency_key="revoke-expired",
                now=NOW + timedelta(minutes=2),
            )
        stored = await self.service.get(record.id, user_id="owner", now=NOW)
        self.assertEqual(DisclosureConsentState.EXPIRED, stored.state)
        self.assertEqual(1, stored.version)

    async def test_ttl_bounds_and_scope_validation(self) -> None:
        request = field_request()
        preview = self.service.preview(request, provider_id="remote.openai", now=NOW)
        for bad_ttl in (
            timedelta(0),
            timedelta(seconds=-1),
            MAX_DISCLOSURE_TTL + timedelta(seconds=1),
        ):
            with self.subTest(ttl=bad_ttl), self.assertRaises(ValidationError):
                self.service.preview(request, provider_id="remote.openai", ttl=bad_ttl, now=NOW)
        with self.assertRaises(ValidationError):
            self.service.preview(request, provider_id="", now=NOW)
        with self.assertRaises(ValidationError):
            await self.service.confirm(
                request,
                provider_id="remote.openai",
                user_id="owner",
                preview_hash=preview.preview_hash,
                idempotency_key="",
                now=NOW,
            )

    async def test_consent_record_never_holds_raw_field_values(self) -> None:
        request = field_request()
        preview = self.service.preview(request, provider_id="remote.openai", now=NOW)
        record = await self.service.confirm(
            request,
            provider_id="remote.openai",
            user_id="owner",
            preview_hash=preview.preview_hash,
            idempotency_key="confirm-1",
            now=NOW,
        )
        rendered = repr(record) + json.dumps(asdict(record), default=str, ensure_ascii=False)
        self.assertNotIn(RAW_SENSITIVE, rendered)
        self.assertNotIn(RAW_PERSONAL, rendered)
        stored = await self.service.get(record.id, user_id="owner", now=NOW)
        self.assertNotIn(RAW_SENSITIVE, repr(stored))

    def test_effective_state_requires_aware_now(self) -> None:
        record = DisclosureConsentRecord(
            id="consent-1",
            user_id="owner",
            provider_id="remote.openai",
            purpose="draft reply",
            field_digest="a" * 64,
            recipient_fingerprint=RECIPIENT_FP,
            field_count=1,
            policy_version=DISCLOSURE_POLICY_VERSION,
            state=DisclosureConsentState.ACTIVE,
            created_at=NOW,
            expires_at=NOW + timedelta(hours=1),
        )
        with self.assertRaises(ValidationError):
            effective_consent_state(record, datetime(2026, 9, 17, 12, 30))

    async def test_confirm_rejects_oversized_provider_id(self) -> None:
        request = field_request()
        with self.assertRaises(ValidationError):
            await self.service.confirm(
                request,
                provider_id="x" * 5000,
                user_id="owner",
                preview_hash="0" * 64,
                idempotency_key="confirm-huge",
                now=NOW,
            )

    async def test_confirm_rejects_oversized_purpose(self) -> None:
        request = ModelRequest(
            purpose="p" * 5000,
            instruction="draft",
            fields=(
                ContextField(
                    "mail_body", RAW_SENSITIVE, DataClassification.SENSITIVE, "mail:1"
                ),
            ),
        )
        with self.assertRaises(ValidationError):
            await self.service.confirm(
                request,
                provider_id="remote.openai",
                user_id="owner",
                preview_hash="0" * 64,
                idempotency_key="confirm-purpose",
                now=NOW,
            )

    async def test_list_for_user_rejects_nonpositive_limit(self) -> None:
        store = InMemoryDisclosureConsentStore()
        with self.assertRaises(ValidationError):
            await store.list_for_user("owner", limit=0)
        with self.assertRaises(ValidationError):
            await store.list_for_user("owner", limit=-1)

    def test_active_record_cannot_carry_revocation_time(self) -> None:
        with self.assertRaises(ValidationError):
            DisclosureConsentRecord(
                id="consent-1",
                user_id="owner",
                provider_id="remote.openai",
                purpose="draft reply",
                field_digest="a" * 64,
                recipient_fingerprint=RECIPIENT_FP,
                field_count=1,
                policy_version=DISCLOSURE_POLICY_VERSION,
                state=DisclosureConsentState.ACTIVE,
                created_at=NOW,
                expires_at=NOW + timedelta(hours=1),
                revoked_at=NOW,
            )

    async def test_authorize_rejects_a_store_that_ignores_expiry(self) -> None:
        expired = DisclosureConsentRecord(
            id="consent-1",
            user_id="owner",
            provider_id="remote.openai",
            purpose="draft reply",
            field_digest=canonical_field_digest(field_request().fields),
            recipient_fingerprint=RECIPIENT_FP,
            field_count=2,
            policy_version=DISCLOSURE_POLICY_VERSION,
            state=DisclosureConsentState.ACTIVE,
            created_at=NOW,
            expires_at=NOW + timedelta(seconds=1),
        )

        class SloppyStore(InMemoryDisclosureConsentStore):
            async def find_active(self, **kwargs: object):  # type: ignore[override]
                del kwargs
                return expired

        service = DisclosureConsentService(SloppyStore(), recipients=RECIPIENTS)
        self.assertIsNone(
            await service.authorize(
                field_request(),
                consent_id="consent-1",
                user_id="owner",
                provider_id="remote.openai",
                recipient_fingerprint=RECIPIENT_FP,
                now=NOW + timedelta(minutes=5),
            )
        )

    def test_redacted_preview_masks_content(self) -> None:
        preview = redacted_field_preview(RAW_SENSITIVE)
        self.assertNotIn("private", preview)
        self.assertNotIn("42", preview)
        self.assertEqual(len(RAW_SENSITIVE), len(preview))


if __name__ == "__main__":
    unittest.main()
