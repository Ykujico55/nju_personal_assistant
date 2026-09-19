"""In-memory test doubles for the F06 host mail capability."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from collections.abc import Collection, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from personal_assistant.core.extensions.artifact_access import (
    HOST_ARTIFACT_DELETE,
    HOST_ARTIFACT_METHODS,
    HOST_ARTIFACT_PUT,
    ArtifactAccessError,
    ExtensionArtifactContext,
    decode_artifact_data,
    normalize_media_type,
)
from personal_assistant.core.extensions.artifact_access import (
    artifact_id as parse_artifact_id,
)
from personal_assistant.core.mail import (
    MailDeliveryRecord,
    MailDeliveryStatus,
    MailLedgerConflictError,
    MailLedgerStateError,
    MailRecipientResult,
)


class InMemoryMailDeliveryLedger:
    """Same durable state machine as the PostgreSQL ledger (single process)."""

    def __init__(self) -> None:
        self._records: dict[str, MailDeliveryRecord] = {}
        self._lock = asyncio.Lock()

    async def prepare(self, record: MailDeliveryRecord) -> MailDeliveryStatus:
        async with self._lock:
            existing = self._records.get(record.local_action_id)
            if existing is None:
                self._records[record.local_action_id] = replace(
                    record, status=MailDeliveryStatus.PREPARED
                )
                return MailDeliveryStatus.PREPARED
            if existing.envelope_digest != record.envelope_digest:
                raise MailLedgerConflictError(
                    "the local action id is already bound to a different envelope"
                )
            return existing.status

    async def begin_execution(
        self,
        local_action_id: str,
        envelope_digest: str,
        *,
        owner_id: str,
        lease_seconds: float,
    ) -> MailDeliveryStatus:
        if not owner_id or not 1 <= lease_seconds <= 86_400:
            raise MailLedgerStateError("invalid execution lease")
        async with self._lock:
            record = self._records.get(local_action_id)
            if record is None:
                raise MailLedgerStateError("unknown local action id")
            if record.envelope_digest != envelope_digest:
                raise MailLedgerConflictError(
                    "the local action id is already bound to a different envelope"
                )
            if record.status is MailDeliveryStatus.PREPARED:
                self._records[local_action_id] = replace(
                    record,
                    status=MailDeliveryStatus.EXECUTING,
                    owner_id=owner_id,
                    lease_expires_at=datetime.now(UTC) + timedelta(seconds=lease_seconds),
                )
                return MailDeliveryStatus.EXECUTING
            # A live EXECUTING row is returned unchanged; only the recovery
            # sweep may turn it into UNKNOWN.
            return record.status

    async def finalize(
        self,
        local_action_id: str,
        envelope_digest: str,
        *,
        status: MailDeliveryStatus,
        recipient_results: tuple[MailRecipientResult, ...] = (),
        server_code: str | None = None,
        diagnostic_code: str | None = None,
    ) -> MailDeliveryStatus:
        if not status.terminal:
            raise MailLedgerStateError("finalize requires a terminal status")
        async with self._lock:
            record = self._records.get(local_action_id)
            if record is None:
                raise MailLedgerStateError("unknown local action id")
            if record.envelope_digest != envelope_digest:
                raise MailLedgerConflictError(
                    "the local action id is already bound to a different envelope"
                )
            if record.status.terminal:
                return record.status
            if record.status is not MailDeliveryStatus.EXECUTING:
                raise MailLedgerStateError("the ledger row is not EXECUTING")
            self._records[local_action_id] = replace(
                record,
                status=status,
                recipient_results=recipient_results,
                server_code=server_code,
                diagnostic_code=diagnostic_code,
            )
            return status

    async def reconcile(
        self,
        local_action_id: str,
        *,
        account_id: str,
        message_id: str,
        status: MailDeliveryStatus,
        diagnostic_code: str | None = None,
    ) -> MailDeliveryStatus:
        if not status.terminal:
            raise MailLedgerStateError("reconciliation requires a terminal status")
        async with self._lock:
            record = self._records.get(local_action_id)
            if record is None:
                raise MailLedgerStateError("unknown local action id")
            if (
                record.status is MailDeliveryStatus.UNKNOWN
                and record.account_id == account_id
                and record.message_id == message_id
            ):
                self._records[local_action_id] = replace(
                    record,
                    status=status,
                    server_code=record.server_code or "SENT_RECONCILED",
                    diagnostic_code=diagnostic_code,
                )
                return status
            return record.status

    async def heartbeat(
        self, local_action_id: str, owner_id: str, *, lease_seconds: float
    ) -> bool:
        if not owner_id or not 1 <= lease_seconds <= 86_400:
            raise MailLedgerStateError("invalid execution lease")
        async with self._lock:
            record = self._records.get(local_action_id)
            if (
                record is None
                or record.status is not MailDeliveryStatus.EXECUTING
                or record.owner_id != owner_id
            ):
                return False
            self._records[local_action_id] = replace(
                record,
                lease_expires_at=datetime.now(UTC) + timedelta(seconds=lease_seconds),
            )
            return True

    async def recover_stale_executions(
        self, *, active_owners: Collection[str] = ()
    ) -> tuple[str, ...]:
        now = datetime.now(UTC)
        recovered: list[str] = []
        live = set(active_owners)
        async with self._lock:
            for local_action_id, record in list(self._records.items()):
                if (
                    record.status is MailDeliveryStatus.EXECUTING
                    and record.lease_expires_at is not None
                    and record.lease_expires_at <= now
                    and (record.owner_id is None or record.owner_id not in live)
                ):
                    self._records[local_action_id] = replace(
                        record,
                        status=MailDeliveryStatus.UNKNOWN,
                        diagnostic_code="EXECUTION_LEASE_EXPIRED",
                    )
                    recovered.append(local_action_id)
        return tuple(recovered)

    async def get(self, local_action_id: str) -> MailDeliveryRecord | None:
        async with self._lock:
            return self._records.get(local_action_id)

    async def close(self) -> None:
        return None


class InMemoryExtensionArtifactAccess:
    """Content-addressed artifacts with per-extension ownership metadata."""

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}
        self._metadata: dict[str, dict[str, Any]] = {}
        self._counter = 0

    @property
    def available(self) -> bool:
        return True

    async def handle(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        context: ExtensionArtifactContext,
    ) -> Any:
        if method not in HOST_ARTIFACT_METHODS:
            raise ArtifactAccessError(
                "ARTIFACT_PROTOCOL_ERROR", f"unknown artifact method: {method}"
            )
        if method == HOST_ARTIFACT_PUT:
            data = decode_artifact_data(params)
            media_type = normalize_media_type(params.get("media_type"))
            digest = hashlib.sha256(data).hexdigest()
            self._counter += 1
            identifier = f"art_test_{self._counter:08d}_{digest[:8]}"
            self._blobs[identifier] = data
            self._metadata[identifier] = {
                "id": identifier,
                "owner_extension_id": context.extension_id,
                "owner_extension_version": context.extension_version,
                "content_hash": digest,
                "media_type": media_type,
                "size_bytes": len(data),
                "sensitivity": str(params.get("sensitivity", "PERSONAL")),
            }
            return dict(self._metadata[identifier])
        identifier = parse_artifact_id(params)
        metadata = self._metadata.get(identifier)
        if metadata is None:
            raise ArtifactAccessError("ARTIFACT_NOT_FOUND", "the artifact is unknown")
        if method == HOST_ARTIFACT_DELETE:
            if metadata["owner_extension_id"] != context.extension_id:
                raise ArtifactAccessError(
                    "ARTIFACT_FORBIDDEN", "this artifact belongs to another extension"
                )
            self._metadata.pop(identifier, None)
            self._blobs.pop(identifier, None)
            return {"deleted": identifier}
        if metadata["owner_extension_id"] != context.extension_id:
            raise ArtifactAccessError(
                "ARTIFACT_FORBIDDEN", "this artifact belongs to another extension"
            )
        data = self._blobs[identifier]
        return {
            "id": identifier,
            "content_hash": metadata["content_hash"],
            "media_type": metadata["media_type"],
            "size_bytes": metadata["size_bytes"],
            "sensitivity": metadata["sensitivity"],
            "data_base64": base64.b64encode(data).decode("ascii"),
        }

    async def read_as_host(self, artifact_id: str) -> bytes:
        data = self._blobs.get(artifact_id)
        if data is None:
            raise ArtifactAccessError("ARTIFACT_NOT_FOUND", "the artifact is unknown")
        return data

    async def aclose(self) -> None:
        return None


__all__ = ["InMemoryExtensionArtifactAccess", "InMemoryMailDeliveryLedger"]
