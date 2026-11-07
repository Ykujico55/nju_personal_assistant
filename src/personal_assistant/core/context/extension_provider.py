"""Read-only context from instance-enabled extension providers.

A conversation identity correlates the request; it grants no directory access.
Only the installed extension's existing instance configuration controls sources.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

from personal_assistant.core.models import ContextField, DataClassification
from personal_assistant.domain import Sensitivity, TrustLevel, ValidationError

from .models import Evidence, EvidenceState


class ContextSupervisor(Protocol):
    async def list_context_providers(self) -> tuple[dict[str, Any], ...]: ...

    async def retrieve_context(
        self,
        extension_id: str,
        extension_version: str,
        *,
        query: str,
        limit: int,
        provider_ids: tuple[str, ...] = (),
    ) -> tuple[dict[str, Any], ...]: ...


@dataclass(frozen=True, slots=True)
class RetrievedContext:
    fields: tuple[ContextField, ...] = ()
    citations: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()


def _text(value: object, maximum: int | None) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or (maximum is not None and len(value) > maximum)
    ):
        raise ValueError("invalid context field")
    return value


def _time(value: object) -> datetime:
    parsed = datetime.fromisoformat(_text(value, 100).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("context time must be timezone aware")
    return parsed


def _locator(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or len(value) > 16:
        raise ValueError("invalid context locator")
    result: dict[str, Any] = {}
    for key, item in value.items():
        _text(key, 80)
        if isinstance(item, str):
            if len(item) > 256:
                raise ValueError("oversized context locator")
        elif type(item) is int:
            if not 0 <= item <= 2**53:
                raise ValueError("invalid numeric locator")
        elif item is not None:
            raise ValueError("unsupported context locator")
        result[key] = item
    return result


def _evidence(raw: Mapping[str, Any], owner: Mapping[str, Any], now: datetime) -> Evidence:
    metadata = raw.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("status") != "CURRENT":
        raise ValueError("context is not explicitly current")
    if (
        metadata.get("extension_id") != owner["extension_id"]
        or metadata.get("extension_version") != owner["extension_version"]
    ):
        raise ValueError("context producer does not match selected worker")
    sensitivity = Sensitivity(_text(metadata.get("sensitivity"), 20))
    if sensitivity is Sensitivity.SECRET:
        raise ValueError("secret context is forbidden")
    trust = TrustLevel(_text(metadata.get("trust"), 30))
    uri = _text(raw.get("source_uri"), 2048)
    parsed = urlsplit(uri)
    if not parsed.scheme or parsed.scheme in {"data", "javascript"} or parsed.username:
        raise ValueError("invalid context source")
    content_hash = _text(raw.get("content_hash"), 256)
    if metadata.get("content_hash", content_hash) != content_hash:
        raise ValueError("inconsistent context hash")
    if metadata.get("source_uri", uri) != uri:
        raise ValueError("inconsistent context source")
    valid_from = _time(metadata["valid_from"]) if metadata.get("valid_from") else None
    valid_until = _time(metadata["valid_until"]) if metadata.get("valid_until") else None
    if (valid_from and valid_from > now) or (valid_until and valid_until <= now):
        raise ValueError("context validity window is not current")
    # Older SDK providers omit optional source revision/time: content identity is
    # then the revision and this is the host's receipt time, not a source timestamp.
    observed = _time(metadata["observed_at"]) if metadata.get("observed_at") else now
    if observed > now:
        raise ValueError("context observation is in the future")
    return Evidence(
        id=_text(raw.get("source_id"), 256),
        # Transport already bounds RPC frames; retain the complete accepted body.
        text=_text(raw.get("text"), None),
        source_uri=uri,
        locator=_locator(metadata.get("locator", {})),
        content_hash=content_hash,
        source_version=_text(metadata.get("source_version", content_hash), 256),
        observed_at=observed,
        producer_extension_id=owner["extension_id"],
        producer_extension_version=owner["extension_version"],
        sensitivity=sensitivity,
        trust=trust,
        state=EvidenceState.CURRENT,
        valid_from=valid_from,
        valid_until=valid_until,
    )


class ExtensionContextService:
    def __init__(
        self, supervisor: ContextSupervisor, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._supervisor = supervisor
        self._clock = clock or (lambda: datetime.now(UTC))

    async def catalog(self) -> tuple[dict[str, Any], ...]:
        # Catalog enumeration only reads lifecycle metadata; it never starts code.
        return (await self._supervisor.list_context_providers())[:32]

    async def retrieve(
        self,
        query: str,
        *,
        request_id: str,
        provider_ids: Sequence[str] = (),
        limit: int = 5,
        max_chars: int | None = None,
    ) -> RetrievedContext:
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > 2000
            or not isinstance(request_id, str)
            or not request_id
            or len(request_id) > 200
            or type(limit) is not int
            or not 1 <= limit <= 10
            or (
                max_chars is not None
                and (type(max_chars) is not int or not 256 <= max_chars <= 24000)
            )
            or isinstance(provider_ids, str)
            or len(provider_ids) > 8
            or any(not isinstance(p, str) for p in provider_ids)
        ):
            raise ValidationError("invalid bounded context request")
        if not provider_ids:
            return RetrievedContext()
        catalog = await self.catalog()
        selected = set(provider_ids)
        known = {p for item in catalog for p in item["provider_ids"]}
        if not selected <= known:
            raise ValidationError("context provider is not enabled in this instance")
        owners = [item for item in catalog if selected.intersection(item["provider_ids"])]
        fields: list[ContextField] = []
        citations: list[dict[str, Any]] = []
        warnings: list[str] = []
        identities: set[tuple[str, str, str, str]] = set()
        remaining = max_chars
        async def fetch(owner: Mapping[str, Any]) -> tuple[dict[str, Any], ...] | None:
            try:
                async with asyncio.timeout(8.0):
                    return await self._supervisor.retrieve_context(
                        owner["extension_id"],
                        owner["extension_version"],
                        query=query,
                        limit=limit,
                        provider_ids=tuple(
                            p for p in dict.fromkeys(provider_ids) if p in owner["provider_ids"]
                        ),
                    )
            except Exception:
                return None

        # Selected extensions are independent; one slow provider must not add
        # another full timeout for every selected source. gather propagates
        # cancellation to its children, and output remains in catalog order.
        results = await asyncio.gather(*(fetch(owner) for owner in owners))
        for owner, returned in zip(owners, results, strict=True):
            if returned is None:
                warnings.append("CONTEXT_PROVIDER_UNAVAILABLE")
                continue
            if len(fields) >= limit or (remaining is not None and remaining < 256):
                continue
            for raw in returned[:100]:
                try:
                    item = _evidence(raw, owner, self._clock())
                except (TypeError, ValueError, ValidationError):
                    warnings.append("CONTEXT_EVIDENCE_REJECTED")
                    continue
                identity = (
                    item.source_uri,
                    item.source_version,
                    item.content_hash,
                    json.dumps(dict(item.locator), sort_keys=True),
                )
                if identity in identities:
                    continue
                citation: dict[str, Any] = {
                    "source_id": item.id,
                    "source_uri": item.source_uri,
                    "source_version": item.source_version,
                    "content_hash": item.content_hash,
                    "locator": dict(item.locator),
                    "extension_id": item.producer_extension_id,
                    "extension_version": item.producer_extension_version,
                    "sensitivity": item.sensitivity.value,
                    "trust": item.trust.value,
                }
                # Escaping closes the data delimiter even if either metadata or
                # body contains prompt injection. Never promote text to policy.
                header = json.dumps(citation, ensure_ascii=False, separators=(",", ":"))
                header = header.replace("<UNTRUSTED_DATA>", "[ESCAPED_OPEN_DATA]").replace(
                    "</UNTRUSTED_DATA>", "[ESCAPED_CLOSE_DATA]"
                )
                prefix = "<UNTRUSTED_DATA>\n" + header + "\n"
                suffix = "\n</UNTRUSTED_DATA>"
                available = (
                    None if remaining is None else remaining - len(prefix) - len(suffix)
                )
                if available is not None and available < 1:
                    continue
                safe_text = item.text.replace("<UNTRUSTED_DATA>", "[ESCAPED_OPEN_DATA]").replace(
                    "</UNTRUSTED_DATA>", "[ESCAPED_CLOSE_DATA]"
                )
                rendered = prefix + safe_text[:available] + suffix
                citation["truncated"] = available is not None and len(safe_text) > available
                fields.append(
                    ContextField(
                        name="instance_context_" + str(len(fields) + 1),
                        value=rendered,
                        classification=DataClassification(item.sensitivity.value),
                        source=item.source_uri,
                    )
                )
                citations.append(citation)
                identities.add(identity)
                if remaining is not None:
                    remaining -= len(rendered)
                if len(fields) >= limit or (remaining is not None and remaining < 256):
                    break
        return RetrievedContext(tuple(fields), tuple(citations), tuple(dict.fromkeys(warnings)))
