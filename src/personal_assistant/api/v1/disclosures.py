"""Minimal disclosure-consent control plane (preview, confirm, revoke).

Responses never contain raw field values: the preview shows metadata and a
masked rendering, and the consent record stores only the canonical digest.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response, status

from personal_assistant.api.dependencies import get_actor, get_container
from personal_assistant.bootstrap import Container
from personal_assistant.core.models import effective_consent_state
from personal_assistant.core.models.disclosure import (
    DisclosureConsentRecord,
    DisclosurePreview,
)
from personal_assistant.domain import utc_now

from .schemas import (
    DisclosureConfirmRequest,
    DisclosureConsentView,
    DisclosureFieldPreviewView,
    DisclosureMutation,
    DisclosurePreviewView,
    DisclosureRecipientView,
    DisclosureRequest,
)

router = APIRouter(prefix="/disclosures", tags=["disclosures"])


def _preview_view(preview: DisclosurePreview) -> DisclosurePreviewView:
    identity = preview.recipient_identity
    recipient = DisclosureRecipientView(
        provider_id=preview.provider_id,
        adapter=identity.adapter if identity else "",
        endpoint=identity.endpoint if identity else "",
        model_id=identity.model_id if identity else "",
        fingerprint=preview.recipient_fingerprint,
    )
    return DisclosurePreviewView(
        provider_id=preview.provider_id,
        purpose=preview.purpose,
        policy_version=preview.policy_version,
        field_digest=preview.field_digest,
        recipient_fingerprint=preview.recipient_fingerprint,
        recipient=recipient,
        fields=[
            DisclosureFieldPreviewView(
                name=field.name,
                classification=field.classification,
                source=field.source,
                value_sha256=field.value_sha256,
                value_length=field.value_length,
                redacted_preview=field.redacted_preview,
            )
            for field in preview.fields
        ],
        protected_field_count=preview.protected_field_count,
        ttl_seconds=int(preview.ttl.total_seconds()),
        suggested_expires_at=preview.suggested_expires_at,
        preview_hash=preview.preview_hash,
    )


def _consent_view(record: DisclosureConsentRecord) -> DisclosureConsentView:
    return DisclosureConsentView(
        id=record.id,
        state=effective_consent_state(record, utc_now()).value,
        provider_id=record.provider_id,
        purpose=record.purpose,
        field_digest=record.field_digest,
        recipient_fingerprint=record.recipient_fingerprint,
        field_count=record.field_count,
        policy_version=record.policy_version,
        created_at=record.created_at,
        expires_at=record.expires_at,
        revoked_at=record.revoked_at,
        version=record.version,
    )


@router.post("/preview", response_model=DisclosurePreviewView)
async def preview_disclosure(
    payload: DisclosureRequest,
    response: Response,
    container: Container = Depends(get_container),
) -> DisclosurePreviewView:
    response.headers["Cache-Control"] = "no-store"
    preview = container.disclosures.preview(
        payload.to_model_request(),
        provider_id=payload.provider_id,
        ttl=_ttl(payload),
    )
    return _preview_view(preview)


@router.post("", response_model=DisclosureConsentView, status_code=status.HTTP_202_ACCEPTED)
async def confirm_disclosure(
    payload: DisclosureConfirmRequest,
    request: Request,
    response: Response,
    container: Container = Depends(get_container),
    actor: str = Depends(get_actor),
) -> DisclosureConsentView:
    response.headers["Cache-Control"] = "no-store"
    record = await container.disclosures.confirm(
        payload.to_model_request(),
        provider_id=payload.provider_id,
        user_id=actor,
        preview_hash=payload.preview_hash,
        idempotency_key=request.state.idempotency_key,
        ttl=_ttl(payload),
    )
    return _consent_view(record)


@router.get("/{consent_id}", response_model=DisclosureConsentView)
async def get_disclosure(
    consent_id: str,
    response: Response,
    container: Container = Depends(get_container),
    actor: str = Depends(get_actor),
) -> DisclosureConsentView:
    response.headers["Cache-Control"] = "no-store"
    record = await container.disclosures.get(consent_id, user_id=actor)
    return _consent_view(record)


@router.post("/{consent_id}/revoke", response_model=DisclosureConsentView)
async def revoke_disclosure(
    consent_id: str,
    payload: DisclosureMutation,
    request: Request,
    response: Response,
    container: Container = Depends(get_container),
    actor: str = Depends(get_actor),
) -> DisclosureConsentView:
    response.headers["Cache-Control"] = "no-store"
    record = await container.disclosures.revoke(
        consent_id,
        user_id=actor,
        expected_version=payload.version,
        idempotency_key=request.state.idempotency_key,
    )
    return _consent_view(record)


def _ttl(payload: DisclosureRequest) -> timedelta:
    return timedelta(seconds=payload.ttl_seconds)
