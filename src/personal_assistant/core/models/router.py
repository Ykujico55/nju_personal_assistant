"""Choose a model without silently widening data disclosure.

Routing rules (F04 contract):

- SECRET material is blocked before any consent lookup or provider call, on
  local, remote, fallback and debug paths alike.
- A remote provider may receive PERSONAL/SENSITIVE fields only under a
  persisted, unexpired, exactly bound disclosure consent.  Ephemeral
  ``DisclosureConsent`` objects are a test-only affordance and require explicit
  opt-in; production constructs the router with a ``DisclosureAuthorizer``.
- Falling back to a local provider is allowed only when it is explicitly
  configured; a provider error never triggers a switch to another provider.
- Model output is returned as untrusted text.  This class never executes tools
  or produces external side effects.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from personal_assistant.core.audit import AuditEvent, AuditWriterPort
from personal_assistant.domain import ValidationError

from .cleanup import run_cleanup
from .disclosure import (
    DisclosureAuthorizer,
    canonical_field_digest,
    recipient_fingerprint,
)
from .provider import (
    AUDITABLE_USAGE_KEYS,
    DataClassification,
    ModelOutput,
    ModelProvider,
    ModelRequest,
    RecipientIdentity,
    bounded_usage,
    classification_of,
    is_secret_classification,
)


class DisclosureDenied(RuntimeError):
    pass


def _same_text(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _field_digest(request: ModelRequest) -> str:
    """Legacy helper retained for the ephemeral consent type; exact field set."""

    return canonical_field_digest(
        field
        for field in request.fields
        if field.classification
        in {DataClassification.PERSONAL, DataClassification.SENSITIVE}
    )


@dataclass(frozen=True, slots=True)
class DisclosureConsent:
    """Ephemeral, non-production consent value (kept for compatibility/tests)."""

    provider_id: str
    purpose: str
    field_digest: str
    recipient_fingerprint: str
    expires_at: datetime
    user_id: str

    def __post_init__(self) -> None:
        for name, value in (
            ("provider_id", self.provider_id),
            ("purpose", self.purpose),
            ("user_id", self.user_id),
        ):
            if not value or not value.strip():
                raise ValidationError(f"disclosure consent {name} is required")
        if len(self.field_digest) != 64 or any(
            character not in "0123456789abcdef" for character in self.field_digest
        ):
            raise ValidationError("disclosure consent field_digest must be a sha256")
        if len(self.recipient_fingerprint) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.recipient_fingerprint
        ):
            raise ValidationError(
                "disclosure consent recipient_fingerprint must be a sha256"
            )
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValidationError("disclosure consent expires_at must be timezone-aware")

    @classmethod
    def for_request(
        cls,
        *,
        provider_id: str,
        recipient_fingerprint: str,
        request: ModelRequest,
        expires_at: datetime,
        user_id: str,
    ) -> DisclosureConsent:
        return cls(
            provider_id=provider_id,
            purpose=request.purpose,
            field_digest=_field_digest(request),
            recipient_fingerprint=recipient_fingerprint,
            expires_at=expires_at,
            user_id=user_id,
        )

    def permits(
        self,
        provider_id: str,
        recipient_fingerprint: str,
        request: ModelRequest,
        now: datetime,
    ) -> bool:
        return (
            _same_text(self.provider_id, provider_id)
            and _same_text(self.purpose, request.purpose)
            and _same_text(self.field_digest, _field_digest(request))
            and _same_text(self.recipient_fingerprint, recipient_fingerprint)
            and self.expires_at > now
        )


@dataclass(frozen=True, slots=True)
class _DisclosureDecision:
    """Whether a remote disclosure is authorized, and which consent was used."""

    authorized: bool
    consent_id: str | None = None


class ModelRouter:
    def __init__(
        self,
        providers: tuple[ModelProvider, ...],
        *,
        disclosure: DisclosureAuthorizer | None = None,
        allow_ephemeral_disclosure: bool = False,
        default_local_fallback_id: str | None = None,
        audit: AuditWriterPort | None = None,
    ) -> None:
        if disclosure is not None and allow_ephemeral_disclosure:
            raise ValueError(
                "persistent disclosure and ephemeral consents are mutually exclusive"
            )
        provider_ids = [provider.provider_id for provider in providers]
        if len(set(provider_ids)) != len(provider_ids):
            raise ValueError("model provider ids must be unique")
        self._providers = {provider.provider_id: provider for provider in providers}
        # Freeze the full recipient identity at registration time.  Authorization
        # and audit both use this snapshot, so a provider that is repointed at
        # runtime cannot reuse an old consent or drift the recorded identity.
        self._recipient_identities: dict[str, RecipientIdentity] = {
            provider.provider_id: provider.recipient for provider in providers
        }
        self._recipients = {
            provider_id: recipient_fingerprint(identity)
            for provider_id, identity in self._recipient_identities.items()
        }
        self._disclosure = disclosure
        self._allow_ephemeral_disclosure = allow_ephemeral_disclosure
        self._default_local_fallback_id = default_local_fallback_id
        self._audit = audit

    @property
    def provider_ids(self) -> tuple[str, ...]:
        return tuple(self._providers)

    async def aclose(self) -> None:
        """Close every adapter-owned transport, then report the first failure.

        A failing provider must not skip the remaining providers, and repeated
        cancellation during shutdown must not interrupt cleanup.
        """

        first_error: BaseException | None = None
        cancelled = False
        for provider in self._providers.values():
            close = getattr(provider, "aclose", None)
            if close is None:
                continue
            try:
                await run_cleanup(close())
            except asyncio.CancelledError:
                cancelled = True
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if cancelled:
            raise asyncio.CancelledError()
        if first_error is not None:
            raise first_error

    async def complete(
        self,
        request: ModelRequest,
        *,
        provider_id: str,
        consent: DisclosureConsent | None = None,
        consent_id: str | None = None,
        user_id: str | None = None,
        local_fallback_id: str | None = None,
        now: datetime | None = None,
    ) -> ModelOutput:
        try:
            provider = self._providers[provider_id]
        except KeyError as exc:
            raise KeyError(f"unknown model provider: {provider_id}") from exc

        # SECRET check happens before any consent lookup or provider call.
        self._reject_secret(request)

        protected = any(
            classification_of(field.classification)
            in {DataClassification.PERSONAL, DataClassification.SENSITIVE}
            for field in request.fields
        )
        current = now or datetime.now(UTC)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValidationError("model routing time must be timezone-aware")
        # A provider repointed after registration must not receive traffic: the
        # registered consent binding and the audit identity are snapshots, and a
        # drifted recipient invalidates both.
        if provider.is_remote and not _same_text(
            recipient_fingerprint(provider.recipient), self._recipients[provider_id]
        ):
            raise DisclosureDenied(
                "the registered model recipient has changed; rebuild the provider"
            )
        # Audit records only a consent the authorizer actually returned and the
        # remote call actually used; a caller-supplied id is never trusted.
        used_consent_id: str | None = None
        if provider.is_remote and protected:
            decision = await self._decide(
                request,
                provider_id=provider_id,
                consent=consent,
                consent_id=consent_id,
                user_id=user_id,
                now=current,
            )
            if not decision.authorized:
                fallback_id = (
                    local_fallback_id
                    if local_fallback_id is not None
                    else self._default_local_fallback_id
                )
                if fallback_id is None:
                    raise DisclosureDenied(
                        "remote disclosure requires consent bound to provider, "
                        "purpose and exact fields"
                    )
                try:
                    fallback = self._providers[fallback_id]
                except KeyError as exc:
                    raise DisclosureDenied("configured local fallback is unavailable") from exc
                if fallback.is_remote:
                    raise DisclosureDenied("fallback provider must be local")
                provider = fallback
                # The SECRET guarantee is re-checked before the fallback call so
                # the local path cannot bypass it either.
                self._reject_secret(request)
            else:
                used_consent_id = decision.consent_id

        # Re-verify immediately before the call: an ``await`` inside
        # authorization (or during provider selection) must not let a provider
        # repointed mid-flight send this request to a new receiver.
        if provider.is_remote and not _same_text(
            recipient_fingerprint(provider.recipient), self._recipients[provider.provider_id]
        ):
            raise DisclosureDenied(
                "the registered model recipient has changed; rebuild the provider"
            )
        output = await provider.complete(request)
        await self._record_success(
            request,
            output,
            provider_id=provider.provider_id,
            # Identity comes from the immutable registration snapshot, never
            # from the provider's live state or the response metadata.
            model_id=self._recipient_identities[provider.provider_id].model_id,
            user_id=user_id,
            consent_id=used_consent_id,
            now=current,
        )
        return output

    async def _decide(
        self,
        request: ModelRequest,
        *,
        provider_id: str,
        consent: DisclosureConsent | None,
        consent_id: str | None,
        user_id: str | None,
        now: datetime,
    ) -> _DisclosureDecision:
        if self._disclosure is not None:
            if not consent_id or not user_id:
                return _DisclosureDecision(authorized=False)
            record = await self._disclosure.authorize(
                request,
                consent_id=consent_id,
                user_id=user_id,
                provider_id=provider_id,
                recipient_fingerprint=self._recipients[provider_id],
                now=now,
            )
            if record is None:
                return _DisclosureDecision(authorized=False)
            return _DisclosureDecision(authorized=True, consent_id=record.id)
        if self._allow_ephemeral_disclosure and consent is not None:
            # Ephemeral consents are a test-only affordance and are never
            # persisted, so no consent id may be recorded for them.
            return _DisclosureDecision(
                authorized=consent.permits(
                    provider_id, self._recipients[provider_id], request, now
                ),
                consent_id=None,
            )
        return _DisclosureDecision(authorized=False)

    async def _record_success(
        self,
        request: ModelRequest,
        output: ModelOutput,
        *,
        provider_id: str,
        model_id: str,
        user_id: str | None,
        consent_id: str | None,
        now: datetime,
    ) -> None:
        if self._audit is None:
            return
        await self._audit.append(
            AuditEvent(
                event_type="model.completed",
                actor=user_id or "system",
                resource_type="model_call",
                resource_id=model_id,
                data=_audit_data(request, output, provider_id, model_id, consent_id, now),
            )
        )

    @staticmethod
    def _reject_secret(request: ModelRequest) -> None:
        if any(
            is_secret_classification(field.classification) for field in request.fields
        ):
            raise DisclosureDenied("secret material may never be sent to a model")


def _audit_data(
    request: ModelRequest,
    output: ModelOutput,
    provider_id: str,
    model_id: str,
    consent_id: str | None,
    now: datetime,
) -> dict[str, Any]:
    """Bounded metadata for audit; never field values, prompts or credentials."""

    return {
        "provider_id": provider_id,
        "model_id": model_id,
        "purpose": request.purpose,
        "field_count": len(request.fields),
        "field_digest": canonical_field_digest(request.fields),
        "classifications": sorted(
            {
                canonical.value
                for canonical in (
                    classification_of(field.classification)
                    for field in request.fields
                )
                if canonical is not None
            }
        ),
        "consent_id": consent_id,
        "output_sha256": hashlib.sha256(output.text.encode("utf-8")).hexdigest(),
        "recorded_at": now.isoformat(),
        # Only well-known token counters ever reach the audit record; an
        # arbitrary provider-supplied key is dropped.
        "usage": bounded_usage(output.usage, AUDITABLE_USAGE_KEYS),
    }
