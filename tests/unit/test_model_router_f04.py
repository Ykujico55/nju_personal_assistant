"""F04 ModelRouter integration with persistent disclosure consents and fallback."""

from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import personal_assistant.core.models.router as router_module
from personal_assistant.core.audit import AuditEvent
from personal_assistant.core.models import (
    ContextField,
    DataClassification,
    DisclosureConsent,
    DisclosureDenied,
    ModelOutput,
    ModelRequest,
    ModelRouter,
    RecipientIdentity,
)
from personal_assistant.core.models import (
    recipient_fingerprint as recipient_fp,
)
from personal_assistant.core.models.disclosure import (
    MAX_DISCLOSURE_TTL,
    DisclosureConsentService,
)
from personal_assistant.domain import ValidationError
from personal_assistant.infrastructure.memory import (
    InMemoryAuditWriter,
    InMemoryDisclosureConsentStore,
)

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


class ForeignClassification(StrEnum):
    """A SECRET-valued StrEnum that is not the core ``DataClassification``."""

    SECRET = "SECRET"
    SENSITIVE = "SENSITIVE"
RAW = "private body that must never leak"


class FakeProvider:
    def __init__(
        self,
        provider_id: str,
        *,
        remote: bool,
        fail: Exception | None = None,
        endpoint: str = "https://fake.example.test/v1",
    ) -> None:
        self.provider_id = provider_id
        self.is_remote = remote
        self.calls = 0
        self.fail = fail
        self.seen_requests: list[ModelRequest] = []
        self.recipient = RecipientIdentity(
            provider_id=provider_id,
            adapter="fake",
            endpoint=endpoint,
            model_id="fake",
        )
        self.closed = 0
        self.close_error: BaseException | None = None
        self.close_delay = 0.0

    async def complete(self, request: ModelRequest) -> ModelOutput:
        self.calls += 1
        self.seen_requests.append(request)
        if self.fail is not None:
            raise self.fail
        return ModelOutput(text="ok", provider_id=self.provider_id, model_id="fake")

    async def aclose(self) -> None:
        if self.close_delay:
            await asyncio.sleep(self.close_delay)
        if self.close_error is not None:
            raise self.close_error
        self.closed += 1


def sensitive_request(
    *, value: str = RAW, source: str = "mail:1", purpose: str = "draft reply"
) -> ModelRequest:
    return ModelRequest(
        purpose=purpose,
        instruction="draft",
        fields=(ContextField("mail_body", value, DataClassification.SENSITIVE, source),),
    )


class RouterDisclosureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.remote = FakeProvider("remote.openai", remote=True)
        self.local = FakeProvider("local.ollama", remote=False)
        self.audit = InMemoryAuditWriter()
        self.store = InMemoryDisclosureConsentStore()
        self.consents = DisclosureConsentService(
            self.store, recipients={self.remote.provider_id: self.remote.recipient}
        )
        self.router = ModelRouter(
            (self.remote, self.local), disclosure=self.consents, audit=self.audit
        )

    async def _grant(self, request: ModelRequest, *, provider_id: str = "remote.openai") -> str:
        preview = self.consents.preview(request, provider_id=provider_id, now=NOW)
        record = await self.consents.confirm(
            request,
            provider_id=provider_id,
            user_id="owner",
            preview_hash=preview.preview_hash,
            idempotency_key=f"confirm-{request.purpose}",
            now=NOW,
        )
        return record.id

    async def test_remote_requires_persisted_consent(self) -> None:
        request = sensitive_request()
        with self.assertRaises(DisclosureDenied) as caught:
            await self.router.complete(
                request, provider_id="remote.openai", user_id="owner", now=NOW
            )
        self.assertNotIn(RAW, str(caught.exception))
        self.assertEqual(0, self.remote.calls)

        consent_id = await self._grant(request)
        output = await self.router.complete(
            request,
            provider_id="remote.openai",
            consent_id=consent_id,
            user_id="owner",
            now=NOW + timedelta(minutes=1),
        )
        self.assertEqual("remote.openai", output.provider_id)
        self.assertEqual(1, self.remote.calls)

    async def test_repointed_provider_cannot_reuse_consent(self) -> None:
        request = sensitive_request()
        consent_id = await self._grant(request)
        moved = FakeProvider(
            "remote.openai", remote=True, endpoint="https://moved.example.test/v1"
        )
        moved_consents = DisclosureConsentService(
            self.store, recipients={moved.provider_id: moved.recipient}
        )
        moved_router = ModelRouter((moved,), disclosure=moved_consents)
        with self.assertRaises(DisclosureDenied):
            await moved_router.complete(
                request,
                provider_id="remote.openai",
                consent_id=consent_id,
                user_id="owner",
                now=NOW + timedelta(minutes=1),
            )
        self.assertEqual(0, moved.calls)

    async def test_persisted_consent_required_even_with_ephemeral_object(self) -> None:
        request = sensitive_request()
        ephemeral = DisclosureConsent.for_request(
            provider_id="remote.openai",
            recipient_fingerprint=recipient_fp(self.remote.recipient),
            request=request,
            expires_at=NOW + timedelta(minutes=5),
            user_id="owner",
        )
        with self.assertRaises(DisclosureDenied):
            await self.router.complete(
                request,
                provider_id="remote.openai",
                consent=ephemeral,
                user_id="owner",
                now=NOW,
            )
        self.assertEqual(0, self.remote.calls)

    async def test_ephemeral_consent_requires_explicit_opt_in(self) -> None:
        router = ModelRouter((self.remote,), allow_ephemeral_disclosure=True)
        request = sensitive_request()
        with self.assertRaises(DisclosureDenied):
            await router.complete(request, provider_id="remote.openai", now=NOW)
        consent = DisclosureConsent.for_request(
            provider_id="remote.openai",
            recipient_fingerprint=recipient_fp(self.remote.recipient),
            request=request,
            expires_at=NOW + timedelta(minutes=5),
            user_id="owner",
        )
        output = await router.complete(
            request, provider_id="remote.openai", consent=consent, now=NOW
        )
        self.assertEqual("remote.openai", output.provider_id)
        with self.assertRaises(ValueError):
            ModelRouter(
                (self.remote,),
                disclosure=self.consents,
                allow_ephemeral_disclosure=True,
            )

    async def test_binding_changes_invalidate_persisted_consent(self) -> None:
        request = sensitive_request()
        consent_id = await self._grant(request)
        changed_requests = (
            sensitive_request(value="changed"),
            sensitive_request(source="mail:2"),
            sensitive_request(purpose="summarize"),
        )
        for changed in changed_requests:
            with self.subTest(changed=changed.purpose), self.assertRaises(DisclosureDenied):
                await self.router.complete(
                    changed,
                    provider_id="remote.openai",
                    consent_id=consent_id,
                    user_id="owner",
                    now=NOW + timedelta(minutes=1),
                )
        classification_changed = ModelRequest(
            purpose=request.purpose,
            instruction=request.instruction,
            fields=(
                ContextField("mail_body", RAW, DataClassification.PERSONAL, "mail:1"),
            ),
        )
        with self.assertRaises(DisclosureDenied):
            await self.router.complete(
                classification_changed,
                provider_id="remote.openai",
                consent_id=consent_id,
                user_id="owner",
                now=NOW + timedelta(minutes=1),
            )
        self.assertEqual(0, self.remote.calls)

    async def test_provider_change_and_wrong_user_invalidate_consent(self) -> None:
        other_remote = FakeProvider("remote.other", remote=True)
        router = ModelRouter(
            (self.remote, other_remote, self.local), disclosure=self.consents, audit=self.audit
        )
        request = sensitive_request()
        consent_id = await self._grant(request, provider_id="remote.openai")
        with self.assertRaises(DisclosureDenied):
            await router.complete(
                request,
                provider_id="remote.other",
                consent_id=consent_id,
                user_id="owner",
                now=NOW,
            )
        with self.assertRaises(DisclosureDenied):
            await router.complete(
                request,
                provider_id="remote.openai",
                consent_id=consent_id,
                user_id="intruder",
                now=NOW,
            )
        self.assertEqual(0, self.remote.calls)
        self.assertEqual(0, other_remote.calls)

    async def test_expired_and_revoked_consents_are_denied(self) -> None:
        request = sensitive_request()
        preview = self.consents.preview(
            request, provider_id="remote.openai", ttl=timedelta(minutes=1), now=NOW
        )
        record = await self.consents.confirm(
            request,
            provider_id="remote.openai",
            user_id="owner",
            preview_hash=preview.preview_hash,
            idempotency_key="confirm-short",
            ttl=timedelta(minutes=1),
            now=NOW,
        )
        with self.assertRaises(DisclosureDenied):
            await self.router.complete(
                request,
                provider_id="remote.openai",
                consent_id=record.id,
                user_id="owner",
                now=NOW + timedelta(minutes=2),
            )

        long_id = await self._grant(request)
        await self.consents.revoke(
            long_id,
            user_id="owner",
            expected_version=0,
            idempotency_key="revoke-long",
            now=NOW + timedelta(minutes=1),
        )
        with self.assertRaises(DisclosureDenied):
            await self.router.complete(
                request,
                provider_id="remote.openai",
                consent_id=long_id,
                user_id="owner",
                now=NOW + timedelta(minutes=2),
            )
        self.assertEqual(0, self.remote.calls)

    async def test_secret_is_blocked_before_consent_lookup_on_every_path(self) -> None:
        request = ModelRequest(
            purpose="draft",
            instruction="i",
            fields=(ContextField("password", "hunter2", DataClassification.SECRET, "vault"),),
        )
        lookup_count = 0
        original_authorize = self.consents.authorize

        async def counting_authorize(*args: object, **kwargs: object) -> object:
            nonlocal lookup_count
            lookup_count += 1
            return await original_authorize(*args, **kwargs)  # type: ignore[arg-type]

        self.consents.authorize = counting_authorize  # type: ignore[method-assign]
        for provider_id, fallback_id in (
            ("local.ollama", None),
            ("remote.openai", None),
            ("remote.openai", "local.ollama"),
        ):
            with self.subTest(
                provider_id=provider_id, fallback=fallback_id
            ), self.assertRaises(DisclosureDenied):
                await self.router.complete(
                    request,
                    provider_id=provider_id,
                    consent_id="anything",
                    user_id="owner",
                    local_fallback_id=fallback_id,
                    now=NOW,
                )
        self.assertEqual(0, self.remote.calls)
        self.assertEqual(0, self.local.calls)
        self.assertEqual(0, lookup_count)

    async def test_local_provider_does_not_require_remote_consent(self) -> None:
        request = sensitive_request()
        output = await self.router.complete(request, provider_id="local.ollama", now=NOW)
        self.assertEqual("local.ollama", output.provider_id)
        self.assertEqual(1, self.local.calls)
        self.assertEqual(0, self.remote.calls)

    async def test_remote_failure_never_falls_back_to_another_provider(self) -> None:
        from personal_assistant.core.models.errors import ModelProviderUnavailableError

        request = sensitive_request()
        consent_id = await self._grant(request)
        self.remote.fail = ModelProviderUnavailableError(
            "remote endpoint is unreachable", provider_id="remote.openai"
        )
        with self.assertRaises(ModelProviderUnavailableError):
            await self.router.complete(
                request,
                provider_id="remote.openai",
                consent_id=consent_id,
                user_id="owner",
                local_fallback_id="local.ollama",
                now=NOW,
            )
        self.assertEqual(1, self.remote.calls)
        self.assertEqual(0, self.local.calls)

    async def test_fallback_only_on_missing_consent_and_must_be_local(self) -> None:
        request = sensitive_request()
        with self.assertRaises(DisclosureDenied):
            await self.router.complete(
                request,
                provider_id="remote.openai",
                local_fallback_id="remote.openai",
                now=NOW,
            )
        output = await self.router.complete(
            request,
            provider_id="remote.openai",
            local_fallback_id="local.ollama",
            now=NOW,
        )
        self.assertEqual("local.ollama", output.provider_id)
        self.assertEqual(0, self.remote.calls)

        with self.assertRaises(DisclosureDenied):
            await self.router.complete(
                request,
                provider_id="remote.openai",
                local_fallback_id="missing-provider",
                now=NOW,
            )

    async def test_default_fallback_comes_from_constructor_configuration(self) -> None:
        router = ModelRouter(
            (self.remote, self.local),
            disclosure=self.consents,
            default_local_fallback_id="local.ollama",
        )
        output = await router.complete(sensitive_request(), provider_id="remote.openai", now=NOW)
        self.assertEqual("local.ollama", output.provider_id)

        public_only = ModelRequest(
            purpose="draft",
            instruction="i",
            fields=(ContextField("topic", "weather", DataClassification.PUBLIC, "user"),),
        )
        output = await router.complete(public_only, provider_id="remote.openai", now=NOW)
        self.assertEqual("remote.openai", output.provider_id)

    async def test_audit_records_binding_without_sensitive_values_or_credentials(self) -> None:
        request = sensitive_request()
        consent_id = await self._grant(request)
        await self.router.complete(
            request,
            provider_id="remote.openai",
            consent_id=consent_id,
            user_id="owner",
            now=NOW,
        )
        events = await self.audit.list_events()
        self.assertTrue(events)
        rendered = json.dumps([asdict(event) for event in events], default=str, ensure_ascii=False)
        self.assertNotIn(RAW, rendered)
        self.assertNotIn("hunter2", rendered)
        self.assertIn(consent_id, rendered)

    async def test_audit_uses_configured_model_id_not_provider_metadata(self) -> None:
        self.remote.complete = lambda request: _metadata_injecting_output(self.remote)  # type: ignore[method-assign]
        request = sensitive_request()
        consent_id = await self._grant(request)
        await self.router.complete(
            request,
            provider_id="remote.openai",
            consent_id=consent_id,
            user_id="owner",
            now=NOW,
        )
        events = await self.audit.list_events()
        self.assertTrue(events)
        rendered = json.dumps(
            [asdict(event) for event in events], default=str, ensure_ascii=False
        )
        self.assertNotIn(RAW, rendered)
        for event in events:
            self.assertEqual("model_call", event.resource_type)
            self.assertEqual("fake", event.resource_id)
            self.assertEqual("fake", event.data["model_id"])
            self.assertEqual({"prompt_tokens": 1}, event.data["usage"])

    async def test_foreign_or_string_secret_classification_cannot_bypass_block(self) -> None:
        for label, classification in (
            ("foreign-enum", ForeignClassification.SECRET),
            ("plain-string", "SECRET"),
        ):
            with self.subTest(label=label):
                request = ModelRequest(
                    purpose="login",
                    instruction="use the credential",
                    fields=(
                        ContextField("password", "hunter2", classification, "vault"),
                    ),
                )
                self.assertIs(
                    DataClassification.SECRET, request.fields[0].classification
                )
                with self.assertRaises(DisclosureDenied):
                    await self.router.complete(
                        request, provider_id="remote.openai", now=NOW
                    )
                with self.assertRaises(DisclosureDenied):
                    await self.router.complete(
                        request, provider_id="local.ollama", now=NOW
                    )
        self.assertEqual(0, self.remote.calls)
        self.assertEqual(0, self.local.calls)

    async def test_duck_typed_secret_field_still_fails_closed(self) -> None:
        class ForeignField:
            name = "password"
            value = "hunter2"
            classification = ForeignClassification.SECRET
            source = "vault"

        request = ModelRequest(
            purpose="login", instruction="use the credential", fields=(ForeignField(),)
        )
        with self.assertRaises(DisclosureDenied):
            await self.router.complete(request, provider_id="local.ollama", now=NOW)

    async def test_forged_consent_id_is_never_recorded_in_audit(self) -> None:
        forged = "forged-consent-id"
        router = ModelRouter(
            (self.remote, self.local),
            disclosure=self.consents,
            default_local_fallback_id="local.ollama",
            audit=self.audit,
        )
        output = await router.complete(
            sensitive_request(),
            provider_id="remote.openai",
            consent_id=forged,
            user_id="owner",
            now=NOW,
        )
        self.assertEqual("local.ollama", output.provider_id)
        await router.complete(
            ModelRequest(purpose="draft", instruction="hello"),
            provider_id="remote.openai",
            consent_id=forged,
            user_id="owner",
            now=NOW,
        )
        events = await self.audit.list_events()
        self.assertTrue(events)
        rendered = json.dumps(
            [asdict(event) for event in events], default=str, ensure_ascii=False
        )
        self.assertNotIn(forged, rendered)
        for event in events:
            self.assertIsNone(event.data["consent_id"])

    async def test_ephemeral_consent_is_recorded_without_a_forged_id(self) -> None:
        router = ModelRouter(
            (self.remote, self.local),
            allow_ephemeral_disclosure=True,
            audit=self.audit,
        )
        request = sensitive_request()
        consent = DisclosureConsent.for_request(
            provider_id="remote.openai",
            recipient_fingerprint=recipient_fp(self.remote.recipient),
            request=request,
            expires_at=NOW + timedelta(minutes=5),
            user_id="owner",
        )
        await router.complete(
            request,
            provider_id="remote.openai",
            consent=consent,
            user_id="owner",
            now=NOW,
        )
        events = await self.audit.list_events()
        for event in events:
            self.assertIsNone(event.data["consent_id"])

    async def test_repointed_recipient_invalidates_the_old_consent(self) -> None:
        request = sensitive_request()
        consent_id = await self._grant(request)
        self.remote.recipient = replace(
            self.remote.recipient, endpoint="https://new.example.test/v1"
        )
        with self.assertRaises(DisclosureDenied):
            await self.router.complete(
                request,
                provider_id="remote.openai",
                consent_id=consent_id,
                user_id="owner",
                now=NOW,
            )
        self.assertEqual(0, self.remote.calls)
        self.assertEqual([], list(await self.audit.list_events()))

    async def test_repointed_model_invalidates_the_old_consent(self) -> None:
        request = sensitive_request()
        consent_id = await self._grant(request)
        self.remote.recipient = replace(self.remote.recipient, model_id="gpt-other")
        with self.assertRaises(DisclosureDenied):
            await self.router.complete(
                request,
                provider_id="remote.openai",
                consent_id=consent_id,
                user_id="owner",
                now=NOW,
            )
        self.assertEqual(0, self.remote.calls)

    async def test_audit_identity_comes_from_the_registration_snapshot(self) -> None:
        registered = self.local.recipient
        self.local.recipient = replace(registered, model_id="drifted-model")
        await self.router.complete(
            ModelRequest(purpose="draft", instruction="hello"),
            provider_id="local.ollama",
            now=NOW,
        )
        events = await self.audit.list_events()
        self.assertEqual(1, len(events))
        self.assertEqual(registered.model_id, events[0].resource_id)
        self.assertEqual(registered.model_id, events[0].data["model_id"])

    async def test_duck_typed_public_field_is_coerced_safely(self) -> None:
        class PublicField:
            name = "note"
            value = "public note"
            classification = "PUBLIC"
            source = "note:1"

        request = ModelRequest(
            purpose="draft", instruction="hello", fields=(PublicField(),)
        )
        self.assertIsInstance(request.fields[0], ContextField)
        await self.router.complete(request, provider_id="local.ollama", now=NOW)
        events = await self.audit.list_events()
        self.assertEqual(["PUBLIC"], events[0].data["classifications"])

    async def test_malformed_request_is_rejected_before_any_call(self) -> None:
        with self.assertRaises(ValidationError):
            ModelRequest(purpose="draft", instruction=123)  # type: ignore[arg-type]

        class BadField:
            name = "note"
            value = 5
            classification = "PUBLIC"
            source = "note:1"

        with self.assertRaises(ValidationError):
            ModelRequest(purpose="draft", instruction="hello", fields=(BadField(),))
        self.assertEqual(0, self.remote.calls)
        self.assertEqual(0, self.local.calls)

    async def test_recipient_change_during_authorization_is_denied(self) -> None:
        request = sensitive_request()
        consent_id = await self._grant(request)
        started = asyncio.Event()
        release = asyncio.Event()
        remote = self.remote
        service = self.consents

        class BlockingAuthorizer:
            async def authorize(self, request: ModelRequest, **kwargs: object) -> object:
                started.set()
                await release.wait()
                remote.recipient = replace(
                    remote.recipient, endpoint="https://new.example.test/v1"
                )
                return await service.authorize(request, **kwargs)  # type: ignore[arg-type]

        router = ModelRouter(
            (remote,), disclosure=BlockingAuthorizer(), audit=self.audit  # type: ignore[arg-type]
        )
        task = asyncio.create_task(
            router.complete(
                request,
                provider_id="remote.openai",
                consent_id=consent_id,
                user_id="owner",
                now=NOW,
            )
        )
        await started.wait()
        release.set()
        with self.assertRaises(DisclosureDenied):
            await task
        self.assertEqual(0, remote.calls)
        self.assertEqual([], list(await self.audit.list_events()))

    async def test_field_with_lying_equality_is_still_copied(self) -> None:
        class TrickyField:
            def __init__(self) -> None:
                self.name = "mail_body"
                self.value = "approved-value"
                self.classification = DataClassification.SENSITIVE
                self.source = "mail:1"

            def __eq__(self, other: object) -> bool:
                return True

        tricky = TrickyField()
        request = ModelRequest(purpose="draft", instruction="i", fields=(tricky,))
        self.assertIsInstance(request.fields[0], ContextField)
        self.assertEqual("approved-value", request.fields[0].value)
        consent_id = await self._grant(request)
        tricky.value = "changed-without-consent"
        self.assertEqual("approved-value", request.fields[0].value)
        await self.router.complete(
            request,
            provider_id="remote.openai",
            consent_id=consent_id,
            user_id="owner",
            now=NOW,
        )
        self.assertEqual(1, self.remote.calls)
        self.assertEqual("approved-value", self.remote.seen_requests[-1].fields[0].value)

    async def test_tool_style_model_output_is_text_not_execution(self) -> None:
        toolish = json.dumps(
            {"tool_call": {"tool_id": "mail.send", "arguments": {"to": "x@example.test"}}}
        )
        provider = FakeProvider("remote.openai", remote=True)
        provider.complete = lambda request: _toolish_output(provider, toolish)  # type: ignore[method-assign]
        router = ModelRouter((provider,), allow_ephemeral_disclosure=True)
        request = sensitive_request()
        consent = DisclosureConsent.for_request(
            provider_id="remote.openai",
            recipient_fingerprint=recipient_fp(self.remote.recipient),
            request=request,
            expires_at=NOW + timedelta(minutes=5),
            user_id="owner",
        )
        output = await router.complete(
            request, provider_id="remote.openai", consent=consent, now=NOW
        )
        self.assertEqual(toolish, output.text)
        self.assertEqual("remote.openai", output.provider_id)
        self.assertFalse(hasattr(router, "gateway"))
        self.assertFalse(hasattr(router_module, "ToolGateway"))

    async def test_naive_now_is_rejected_with_typed_error(self) -> None:
        router = ModelRouter((self.remote,), allow_ephemeral_disclosure=True)
        request = sensitive_request()
        consent = DisclosureConsent.for_request(
            provider_id="remote.openai",
            recipient_fingerprint=recipient_fp(self.remote.recipient),
            request=request,
            expires_at=NOW + timedelta(minutes=5),
            user_id="owner",
        )
        with self.assertRaises(ValidationError):
            await router.complete(
                request,
                provider_id="remote.openai",
                consent=consent,
                now=datetime(2026, 9, 17, 12, 0),
            )
        self.assertEqual(0, self.remote.calls)

    async def test_duplicate_provider_ids_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ModelRouter(
                (FakeProvider("dup", remote=True), FakeProvider("dup", remote=False))
            )

    async def test_ephemeral_consent_requires_aware_expiry(self) -> None:
        with self.assertRaises(ValidationError):
            DisclosureConsent.for_request(
                provider_id="remote.openai",
                recipient_fingerprint=recipient_fp(self.remote.recipient),
                request=sensitive_request(),
                expires_at=datetime(2026, 9, 17, 12, 5),
                user_id="owner",
            )

    async def test_audit_failure_fails_closed(self) -> None:
        class FailingAudit:
            async def append(self, event: AuditEvent) -> None:
                del event
                raise RuntimeError("audit unavailable")

        router = ModelRouter(
            (self.remote,), allow_ephemeral_disclosure=True, audit=FailingAudit()
        )
        request = sensitive_request()
        consent = DisclosureConsent.for_request(
            provider_id="remote.openai",
            recipient_fingerprint=recipient_fp(self.remote.recipient),
            request=request,
            expires_at=NOW + timedelta(minutes=5),
            user_id="owner",
        )
        with self.assertRaises(RuntimeError):
            await router.complete(
                request, provider_id="remote.openai", consent=consent, now=NOW
            )
        self.assertEqual(1, self.remote.calls)

    async def test_unicode_ephemeral_consent_is_compared_without_crashing(self) -> None:
        router = ModelRouter((self.remote,), allow_ephemeral_disclosure=True)
        request = sensitive_request(purpose="撰写回复")
        consent = DisclosureConsent.for_request(
            provider_id="remote.openai",
            recipient_fingerprint=recipient_fp(self.remote.recipient),
            request=request,
            expires_at=NOW + timedelta(minutes=5),
            user_id="owner",
        )
        output = await router.complete(
            request, provider_id="remote.openai", consent=consent, now=NOW
        )
        self.assertEqual("remote.openai", output.provider_id)
        with self.assertRaises(DisclosureDenied):
            await router.complete(
                sensitive_request(purpose="总结要点"),
                provider_id="remote.openai",
                consent=consent,
                now=NOW,
            )

    async def test_aclose_closes_every_provider_before_raising_first_error(self) -> None:
        first = FakeProvider("first.remote", remote=True)
        second = FakeProvider("second.remote", remote=True)
        third = FakeProvider("third.remote", remote=True)
        first.close_error = RuntimeError("first close failed")
        router = ModelRouter((first, second, third))
        with self.assertRaises(RuntimeError):
            await router.aclose()
        self.assertEqual(1, second.closed)
        self.assertEqual(1, third.closed)

    async def test_aclose_is_resistant_to_repeated_cancellation(self) -> None:
        slow = FakeProvider("slow.remote", remote=True)
        slow.close_delay = 0.2
        other = FakeProvider("other.remote", remote=True)
        other.close_delay = 0.05
        router = ModelRouter((slow, other))
        task = asyncio.create_task(router.aclose())
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(1, slow.closed)
        self.assertEqual(1, other.closed)

    async def test_ttl_ceiling_is_enforced_through_the_service(self) -> None:
        request = sensitive_request()
        with self.assertRaises(ValidationError):
            self.consents.preview(
                request,
                provider_id="remote.openai",
                ttl=MAX_DISCLOSURE_TTL + timedelta(seconds=1),
                now=NOW,
            )


async def _toolish_output(provider: FakeProvider, text: str) -> ModelOutput:
    provider.calls += 1
    return ModelOutput(text=text, provider_id=provider.provider_id, model_id="fake")


async def _metadata_injecting_output(provider: FakeProvider) -> ModelOutput:
    provider.calls += 1
    return ModelOutput(
        text="ok",
        provider_id=provider.provider_id,
        model_id=RAW,
        usage={RAW: 5, "prompt_tokens": 1},
    )


if __name__ == "__main__":
    unittest.main()
