"""Persistent, exact, revocable consent for remote-model data disclosure.

A consent binds the receiving provider, the stated purpose and a canonical
digest of the *exact* field set.  The digest covers every field's name, value
hash, classification and source, so changing any value, classification, source
or the field set invalidates the consent.  Storage keeps metadata and digests
only; raw field values never reach this module's persistent boundary.

This is deliberately a different state machine from R2 external-action
approvals: allowing a model to read data never authorizes sending it anywhere.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from personal_assistant.core.approvals.canonicalize import canonical_sha256
from personal_assistant.domain import (
    DomainError,
    NotFoundError,
    ValidationError,
    utc_now,
)

from .provider import (
    ContextField,
    DataClassification,
    ModelRequest,
    RecipientIdentity,
    classification_of,
    is_secret_classification,
)

DISCLOSURE_POLICY_VERSION = "f04.1"
MIN_DISCLOSURE_TTL = timedelta(minutes=1)
MAX_DISCLOSURE_TTL = timedelta(days=7)
DEFAULT_DISCLOSURE_TTL = timedelta(hours=1)
MAX_PREVIEW_CHARS = 48
MAX_SCOPE_LENGTH = 128


class DisclosureError(DomainError):
    code = "disclosure_error"


class DisclosureDeniedError(DisclosureError):
    """SECRET material or otherwise forbidden content reached a disclosure path."""

    code = "disclosure_denied"


class DisclosureStateError(DisclosureError):
    code = "disclosure_state_error"


class DisclosurePreviewMismatchError(DisclosureError):
    """The confirmed payload is not the exact payload that was previewed."""

    code = "disclosure_preview_mismatch"


class DisclosureIdempotencyConflictError(DisclosureError):
    code = "idempotency_conflict"


class DisclosureRecipientUnknownError(DisclosureError):
    """The requested provider is not a currently registered remote recipient."""

    code = "disclosure_recipient_unknown"


class DisclosureConsentNotFoundError(NotFoundError):
    pass


class DisclosureConsentState(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


def _canonical_classification(value: object) -> DataClassification:
    """Normalize a classification; an unknown value is a hard failure."""

    canonical = classification_of(value)
    if canonical is None:
        raise ValidationError("context field classification is invalid")
    return canonical


def canonical_field_digest(fields: Iterable[ContextField]) -> str:
    """Order-independent digest of every field's name/value/classification/source."""

    material = sorted(
        (
            {
                "name": field.name,
                "value_sha256": _sha256(field.value),
                "classification": _canonical_classification(
                    field.classification
                ).value,
                "source": field.source,
            }
            for field in fields
        ),
        key=lambda item: (
            item["name"],
            item["source"],
            item["classification"],
            item["value_sha256"],
        ),
    )
    return canonical_sha256(material)


def recipient_fingerprint(identity: RecipientIdentity) -> str:
    """Unforgeable-by-label binding of a consent to a concrete receiver.

    Binds provider id, adapter type, normalized endpoint and model, so
    repointing the same ``provider_id`` at another endpoint or model does not
    keep old consents valid.
    """

    return canonical_sha256(
        {
            "provider_id": identity.provider_id,
            "adapter": identity.adapter,
            "endpoint": identity.endpoint,
            "model_id": identity.model_id,
        }
    )


def _same_text(left: str, right: str) -> bool:
    """Constant-time comparison for arbitrary Unicode identifiers.

    ``hmac.compare_digest`` rejects non-ASCII ``str``; identifiers are not
    secrets, but comparing their UTF-8 bytes keeps the comparison uniform for
    ASCII and non-ASCII values alike.
    """

    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def redacted_field_preview(value: str) -> str:
    """Mask every non-whitespace character; the preview holds no content."""

    masked = "".join(character if character.isspace() else "*" for character in value)
    if len(masked) > MAX_PREVIEW_CHARS:
        return masked[:MAX_PREVIEW_CHARS] + "..."
    return masked


def effective_consent_state(
    record: DisclosureConsentRecord, now: datetime
) -> DisclosureConsentState:
    _require_aware(now, "now")
    if record.state is DisclosureConsentState.ACTIVE and record.expires_at <= now:
        return DisclosureConsentState.EXPIRED
    return record.state


@dataclass(frozen=True, slots=True)
class DisclosureConsentRecord:
    id: str
    user_id: str
    provider_id: str
    purpose: str
    field_digest: str
    recipient_fingerprint: str
    field_count: int
    policy_version: str
    state: DisclosureConsentState
    created_at: datetime
    expires_at: datetime
    version: int = 0
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("id", self.id),
            ("user_id", self.user_id),
            ("provider_id", self.provider_id),
            ("purpose", self.purpose),
            ("policy_version", self.policy_version),
        ):
            _require_text(value, name)
        _require_digest(self.field_digest, "field_digest")
        _require_digest(self.recipient_fingerprint, "recipient_fingerprint")
        if self.field_count < 1:
            raise ValidationError("disclosure consent requires at least one field")
        if self.version < 0:
            raise ValidationError("disclosure consent version cannot be negative")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.created_at:
            raise ValidationError("disclosure consent expiry must follow creation")
        if self.revoked_at is not None:
            _require_aware(self.revoked_at, "revoked_at")
            if self.state is not DisclosureConsentState.REVOKED:
                raise ValidationError("only revoked consent may carry a revocation time")
        if self.state is DisclosureConsentState.REVOKED and self.revoked_at is None:
            raise ValidationError("revoked consent requires a revocation time")


@dataclass(frozen=True, slots=True)
class DisclosureFieldSummary:
    """Metadata-only preview entry; intentionally excludes the field value."""

    name: str
    classification: str
    source: str
    value_sha256: str
    value_length: int
    redacted_preview: str


@dataclass(frozen=True, slots=True)
class DisclosurePreview:
    provider_id: str
    purpose: str
    policy_version: str
    field_digest: str
    recipient_fingerprint: str
    fields: tuple[DisclosureFieldSummary, ...]
    protected_field_count: int
    ttl: timedelta
    suggested_expires_at: datetime
    preview_hash: str
    recipient_identity: RecipientIdentity | None = None


class DisclosureConsentStore(Protocol):
    """Persistence port.  Implementations must be transactional and CAS-safe."""

    async def create(
        self,
        record: DisclosureConsentRecord,
        *,
        command_scope: str,
        idempotency_key: str,
        command_fingerprint: str,
    ) -> DisclosureConsentRecord: ...

    async def get(self, consent_id: str) -> DisclosureConsentRecord: ...

    async def save(
        self, record: DisclosureConsentRecord, *, expected_version: int
    ) -> DisclosureConsentRecord: ...

    async def revoke(
        self,
        consent_id: str,
        *,
        user_id: str,
        expected_version: int,
        revoked_at: datetime,
        command_scope: str,
        idempotency_key: str,
        command_fingerprint: str,
    ) -> DisclosureConsentRecord: ...

    async def find_active(
        self,
        *,
        consent_id: str,
        user_id: str,
        provider_id: str,
        purpose: str,
        field_digest: str,
        recipient_fingerprint: str,
        policy_version: str,
        now: datetime,
    ) -> DisclosureConsentRecord | None: ...

    async def list_for_user(
        self, user_id: str, *, limit: int = 100
    ) -> tuple[DisclosureConsentRecord, ...]: ...


class DisclosureAuthorizer(Protocol):
    """What ``ModelRouter`` needs before it may hand data to a remote provider."""

    async def authorize(
        self,
        request: ModelRequest,
        *,
        consent_id: str,
        user_id: str,
        provider_id: str,
        recipient_fingerprint: str,
        now: datetime,
    ) -> DisclosureConsentRecord | None: ...


class DisclosureConsentService:
    """Preview/confirm/authorize/revoke lifecycle over a persistent store.

    ``recipients`` is the registry of currently configured *remote* providers
    (provider id -> concrete identity).  Only registered receivers can be
    previewed or confirmed, so a consent can never be pre-generated for an
    unconfigured endpoint.
    """

    def __init__(
        self,
        store: DisclosureConsentStore,
        *,
        recipients: Mapping[str, RecipientIdentity] | None = None,
    ) -> None:
        self._store = store
        self._recipients = dict(recipients or {})

    def _registered_recipient(self, provider_id: str) -> tuple[RecipientIdentity, str]:
        identity = self._recipients.get(provider_id)
        if identity is None:
            raise DisclosureRecipientUnknownError(
                f"model recipient is not a registered remote provider: {provider_id}"
            )
        if identity.provider_id != provider_id:
            raise DisclosureRecipientUnknownError(
                f"model recipient registry entry is inconsistent: {provider_id}"
            )
        return identity, recipient_fingerprint(identity)

    def preview(
        self,
        request: ModelRequest,
        *,
        provider_id: str,
        ttl: timedelta = DEFAULT_DISCLOSURE_TTL,
        now: datetime | None = None,
    ) -> DisclosurePreview:
        _validate_scoped_text(provider_id, "provider_id")
        _validate_scoped_text(request.purpose, "purpose")
        _validate_ttl(ttl)
        current = now or utc_now()
        _require_aware(current, "now")
        self._reject_secret(request)
        identity, fingerprint = self._registered_recipient(provider_id)
        digest = canonical_field_digest(request.fields)
        protected = [
            field
            for field in request.fields
            if classification_of(field.classification)
            in {DataClassification.PERSONAL, DataClassification.SENSITIVE}
        ]
        summaries = tuple(
            DisclosureFieldSummary(
                name=field.name,
                classification=_canonical_classification(
                    field.classification
                ).value,
                source=field.source,
                value_sha256=_sha256(field.value),
                value_length=len(field.value),
                redacted_preview=redacted_field_preview(field.value),
            )
            for field in request.fields
        )
        preview_hash = self._preview_hash(
            provider_id=provider_id,
            purpose=request.purpose,
            field_digest=digest,
            recipient_fingerprint=fingerprint,
            protected_field_count=len(protected),
            ttl=ttl,
        )
        return DisclosurePreview(
            provider_id=provider_id,
            purpose=request.purpose,
            policy_version=DISCLOSURE_POLICY_VERSION,
            field_digest=digest,
            recipient_fingerprint=fingerprint,
            fields=summaries,
            protected_field_count=len(protected),
            ttl=ttl,
            suggested_expires_at=current + ttl,
            preview_hash=preview_hash,
            recipient_identity=identity,
        )

    async def confirm(
        self,
        request: ModelRequest,
        *,
        provider_id: str,
        user_id: str,
        preview_hash: str,
        idempotency_key: str,
        ttl: timedelta = DEFAULT_DISCLOSURE_TTL,
        now: datetime | None = None,
    ) -> DisclosureConsentRecord:
        _validate_scoped_text(provider_id, "provider_id")
        _validate_scoped_text(request.purpose, "purpose")
        _validate_scoped_text(user_id, "user_id")
        _validate_idempotency_key(idempotency_key)
        _validate_ttl(ttl)
        current = now or utc_now()
        _require_aware(current, "now")
        self._reject_secret(request)
        identity, recipient = self._registered_recipient(provider_id)
        protected = [
            field
            for field in request.fields
            if classification_of(field.classification)
            in {DataClassification.PERSONAL, DataClassification.SENSITIVE}
        ]
        if not protected:
            raise ValidationError("request has no protected fields requiring disclosure")
        digest = canonical_field_digest(request.fields)
        expected = self._preview_hash(
            provider_id=provider_id,
            purpose=request.purpose,
            field_digest=digest,
            recipient_fingerprint=recipient,
            protected_field_count=len(protected),
            ttl=ttl,
        )
        if not _same_text(expected, preview_hash):
            raise DisclosurePreviewMismatchError(
                "the confirmed fields do not match the previewed disclosure"
            )
        fingerprint = canonical_sha256(
            {
                "user_id": user_id,
                "provider_id": provider_id,
                "purpose": request.purpose,
                "field_digest": digest,
                "recipient_fingerprint": recipient,
                "protected_field_count": len(protected),
                "policy_version": DISCLOSURE_POLICY_VERSION,
                "preview_hash": preview_hash,
                "ttl_seconds": _ttl_seconds(ttl),
            }
        )
        record = DisclosureConsentRecord(
            id=str(uuid.uuid4()),
            user_id=user_id,
            provider_id=provider_id,
            purpose=request.purpose,
            field_digest=digest,
            recipient_fingerprint=recipient,
            field_count=len(protected),
            policy_version=DISCLOSURE_POLICY_VERSION,
            state=DisclosureConsentState.ACTIVE,
            created_at=current,
            expires_at=current + ttl,
            version=0,
        )
        return await self._store.create(
            record,
            command_scope=f"disclosure:confirm:{user_id}",
            idempotency_key=idempotency_key,
            command_fingerprint=fingerprint,
        )

    async def authorize(
        self,
        request: ModelRequest,
        *,
        consent_id: str,
        user_id: str,
        provider_id: str,
        recipient_fingerprint: str,
        now: datetime | None = None,
    ) -> DisclosureConsentRecord | None:
        _validate_scoped_text(consent_id, "consent_id")
        _validate_scoped_text(user_id, "user_id")
        _validate_scoped_text(provider_id, "provider_id")
        _validate_scoped_text(request.purpose, "purpose")
        _require_digest(recipient_fingerprint, "recipient_fingerprint")
        current = now or utc_now()
        _require_aware(current, "now")
        if any(
            is_secret_classification(field.classification) for field in request.fields
        ):
            return None
        digest = canonical_field_digest(request.fields)
        record = await self._store.find_active(
            consent_id=consent_id,
            user_id=user_id,
            provider_id=provider_id,
            purpose=request.purpose,
            field_digest=digest,
            recipient_fingerprint=recipient_fingerprint,
            policy_version=DISCLOSURE_POLICY_VERSION,
            now=current,
        )
        # Re-verify the complete binding in the service itself rather than
        # trusting a store-side filter, so a defective adapter cannot widen
        # disclosure.
        if record is None or not (
            _same_text(record.id, consent_id)
            and _same_text(record.user_id, user_id)
            and _same_text(record.provider_id, provider_id)
            and _same_text(record.purpose, request.purpose)
            and _same_text(record.field_digest, digest)
            and _same_text(record.recipient_fingerprint, recipient_fingerprint)
            and _same_text(record.policy_version, DISCLOSURE_POLICY_VERSION)
            and record.state is DisclosureConsentState.ACTIVE
            and record.expires_at > current
        ):
            return None
        return record

    async def get(
        self, consent_id: str, *, user_id: str | None = None, now: datetime | None = None
    ) -> DisclosureConsentRecord:
        del now  # effective state is derived by callers; storage state is authoritative
        _validate_scoped_text(consent_id, "consent_id")
        record = await self._store.get(consent_id)
        if user_id is not None and not _same_text(record.user_id, user_id):
            raise DisclosureConsentNotFoundError(f"disclosure consent not found: {consent_id}")
        return record

    async def revoke(
        self,
        consent_id: str,
        *,
        user_id: str,
        expected_version: int,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> DisclosureConsentRecord:
        _validate_scoped_text(consent_id, "consent_id")
        _validate_scoped_text(user_id, "user_id")
        _validate_idempotency_key(idempotency_key)
        if expected_version < 0:
            raise ValidationError("expected_version cannot be negative")
        current = now or utc_now()
        _require_aware(current, "now")
        fingerprint = canonical_sha256(
            {
                "user_id": user_id,
                "consent_id": consent_id,
                "expected_version": expected_version,
            }
        )
        return await self._store.revoke(
            consent_id,
            user_id=user_id,
            expected_version=expected_version,
            revoked_at=current,
            command_scope=f"disclosure:revoke:{user_id}",
            idempotency_key=idempotency_key,
            command_fingerprint=fingerprint,
        )

    async def list_for_user(
        self, user_id: str, *, now: datetime | None = None
    ) -> tuple[DisclosureConsentRecord, ...]:
        del now
        _validate_scoped_text(user_id, "user_id")
        return await self._store.list_for_user(user_id)

    @staticmethod
    def _reject_secret(request: ModelRequest) -> None:
        if any(
            is_secret_classification(field.classification) for field in request.fields
        ):
            raise DisclosureDeniedError(
                "secret material may never be previewed, confirmed or disclosed"
            )

    @staticmethod
    def _preview_hash(
        *,
        provider_id: str,
        purpose: str,
        field_digest: str,
        recipient_fingerprint: str,
        protected_field_count: int,
        ttl: timedelta,
    ) -> str:
        return canonical_sha256(
            {
                "provider_id": provider_id,
                "purpose": purpose,
                "policy_version": DISCLOSURE_POLICY_VERSION,
                "field_digest": field_digest,
                "recipient_fingerprint": recipient_fingerprint,
                "protected_field_count": protected_field_count,
                "ttl_seconds": _ttl_seconds(ttl),
            }
        )


def _ttl_seconds(ttl: timedelta) -> int:
    return int(ttl.total_seconds())


def _validate_ttl(ttl: timedelta) -> None:
    if ttl < MIN_DISCLOSURE_TTL or ttl > MAX_DISCLOSURE_TTL:
        raise ValidationError(
            "disclosure ttl must be between 1 minute and 7 days"
        )


def _validate_scoped_text(value: str, name: str) -> None:
    _require_text(value, name)
    if len(value) > MAX_SCOPE_LENGTH:
        raise ValidationError(f"{name} must be at most {MAX_SCOPE_LENGTH} characters")


def _validate_idempotency_key(value: str) -> None:
    _require_text(value, "idempotency_key")
    if len(value) > 200:
        raise ValidationError("idempotency_key must be at most 200 characters")


def _require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} is required")


def _require_digest(value: str, name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValidationError(f"{name} must be a lowercase 64 character sha256")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{name} must be timezone-aware")


__all__ = [
    "DEFAULT_DISCLOSURE_TTL",
    "DISCLOSURE_POLICY_VERSION",
    "MAX_DISCLOSURE_TTL",
    "MIN_DISCLOSURE_TTL",
    "DisclosureAuthorizer",
    "DisclosureConsentNotFoundError",
    "DisclosureConsentRecord",
    "DisclosureConsentService",
    "DisclosureConsentState",
    "DisclosureConsentStore",
    "DisclosureDeniedError",
    "DisclosureError",
    "DisclosureFieldSummary",
    "DisclosureIdempotencyConflictError",
    "DisclosurePreview",
    "DisclosurePreviewMismatchError",
    "DisclosureRecipientUnknownError",
    "DisclosureStateError",
    "canonical_field_digest",
    "effective_consent_state",
    "recipient_fingerprint",
    "redacted_field_preview",
]
