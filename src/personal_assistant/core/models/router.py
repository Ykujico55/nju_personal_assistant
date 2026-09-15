"""Choose a model without silently widening data disclosure."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from .provider import DataClassification, ModelOutput, ModelProvider, ModelRequest


class DisclosureDenied(RuntimeError):
    pass


def _field_digest(request: ModelRequest) -> str:
    material = [
        {
            "name": field.name,
            "value_sha256": hashlib.sha256(field.value.encode("utf-8")).hexdigest(),
            "classification": field.classification.value,
            "source": field.source,
        }
        for field in request.fields
        if field.classification in {DataClassification.PERSONAL, DataClassification.SENSITIVE}
    ]
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class DisclosureConsent:
    provider_id: str
    purpose: str
    field_digest: str
    expires_at: datetime
    user_id: str

    @classmethod
    def for_request(
        cls,
        *,
        provider_id: str,
        request: ModelRequest,
        expires_at: datetime,
        user_id: str,
    ) -> DisclosureConsent:
        return cls(
            provider_id=provider_id,
            purpose=request.purpose,
            field_digest=_field_digest(request),
            expires_at=expires_at,
            user_id=user_id,
        )

    def permits(self, provider_id: str, request: ModelRequest, now: datetime) -> bool:
        return (
            hmac.compare_digest(self.provider_id, provider_id)
            and hmac.compare_digest(self.purpose, request.purpose)
            and hmac.compare_digest(self.field_digest, _field_digest(request))
            and self.expires_at > now
        )


class ModelRouter:
    def __init__(self, providers: tuple[ModelProvider, ...]) -> None:
        self._providers = {provider.provider_id: provider for provider in providers}

    async def complete(
        self,
        request: ModelRequest,
        *,
        provider_id: str,
        consent: DisclosureConsent | None = None,
        local_fallback_id: str | None = None,
        now: datetime | None = None,
    ) -> ModelOutput:
        try:
            provider = self._providers[provider_id]
        except KeyError as exc:
            raise KeyError(f"unknown model provider: {provider_id}") from exc

        if any(field.classification is DataClassification.SECRET for field in request.fields):
            raise DisclosureDenied("secret material may never be sent to a model")

        protected = any(
            field.classification in {DataClassification.PERSONAL, DataClassification.SENSITIVE}
            for field in request.fields
        )
        current = now or datetime.now(UTC)
        if provider.is_remote and protected and (
            consent is None or not consent.permits(provider_id, request, current)
        ):
            if local_fallback_id is None:
                raise DisclosureDenied(
                    "remote disclosure requires consent bound to provider, purpose and exact fields"
                )
            try:
                fallback = self._providers[local_fallback_id]
            except KeyError as exc:
                raise DisclosureDenied("configured local fallback is unavailable") from exc
            if fallback.is_remote:
                raise DisclosureDenied("fallback provider must be local")
            provider = fallback
        return await provider.complete(request)
