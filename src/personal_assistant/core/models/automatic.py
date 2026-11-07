"""Exact model-read grants issued under the operator-selected automatic policy.

This is not an R2 action approval or a claim that the user confirmed a preview.
Only trusted composition roots select this policy. SECRET is still rejected by
DisclosureConsentService and ModelRouter, and recipient binding is unchanged.
"""

from __future__ import annotations

from personal_assistant.core.audit import AuditEvent, AuditWriterPort

from .disclosure import DisclosureConsentService
from .provider import ModelRequest


async def authorize_automatic(
    request: ModelRequest,
    *,
    provider_id: str,
    user_id: str,
    key: str,
    disclosures: DisclosureConsentService,
    audit: AuditWriterPort,
) -> str:
    preview = disclosures.preview(request, provider_id=provider_id)
    consent = await disclosures.confirm(
        request,
        provider_id=provider_id,
        user_id=user_id,
        preview_hash=preview.preview_hash,
        idempotency_key=key,
    )
    await audit.append(
        AuditEvent(
            event_type="model.disclosure.automatic",
            actor=user_id,
            resource_type="model_disclosure",
            resource_id=consent.id,
            data={
                "mode": "automatic",
                "provider_id": provider_id,
                "purpose": request.purpose,
                "field_digest": preview.field_digest,
                "recipient_fingerprint": preview.recipient_fingerprint,
            },
        )
    )
    return consent.id
