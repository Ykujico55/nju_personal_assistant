"""Loopback-only extension and recovery control plane.

The local Admin API hosts the Extension Supervisor.  It must listen on loopback
(see ``Settings.validate``) and must never be published through Cloudflare Tunnel
or Tailscale.  Code-level changes require an exact, preview-bound confirmation.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, FastAPI, Request
from pydantic import BaseModel, Field, ValidationError

from personal_assistant.api.dependencies import get_container
from personal_assistant.api.errors import ApplicationError, install_error_handlers
from personal_assistant.api.middleware import IdempotencyKeyMiddleware, RequestIdMiddleware
from personal_assistant.bootstrap import Container, build_container
from personal_assistant.core.extensions import (
    ExtensionManifest,
    ExtensionRecord,
    ManifestValidationError,
)
from personal_assistant.core.extensions.config import (
    ExtensionConfigError,
    validate_extension_config,
)
from personal_assistant.core.extensions.errors import (
    ConfirmationRequiredError,
    ExtensionError,
    InvalidLifecycleTransition,
)
from personal_assistant.core.extensions.lifecycle import (
    InstallationConfirmation,
    InstallationPreview,
)
from personal_assistant.core.extensions.operations import ExtensionOperation
from personal_assistant.core.mail import MailAccountRecord
from personal_assistant.infrastructure.extensions.config_store import (
    load_extension_config_schema,
)
from personal_assistant.settings import Settings


class InspectRequest(BaseModel):
    source: str = Field(min_length=1, max_length=4096)


class InstallConfirmationRequest(BaseModel):
    plan_id: str = Field(min_length=1, max_length=128)
    confirmation_nonce: str = Field(min_length=1, max_length=128)
    preview_hash: str = Field(min_length=1, max_length=128)
    accepted_warning: bool = False


class ConfigUpdateRequest(BaseModel):
    config: dict[str, Any]


class MailAccountRequest(BaseModel):
    model_config = {"extra": "forbid"}

    account_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    address: str = Field(min_length=3, max_length=320)
    imap_host: str = Field(min_length=1, max_length=255)
    smtp_host: str = Field(min_length=1, max_length=255)
    secret_handle_id: str = Field(min_length=1, max_length=128)
    imap_port: int = Field(default=993, ge=1, le=65535)
    smtp_port: int = Field(default=465, ge=1, le=65535)
    display_name: str = Field(default="", max_length=120)
    read_enabled: bool = True
    send_enabled: bool = False
    tls_mode: str = Field(default="auto", pattern=r"^(auto|implicit|starttls)$")


class MailAccountsUpdateRequest(BaseModel):
    accounts: list[MailAccountRequest] = Field(default_factory=list, max_length=32)


def _manifest_view(
    manifest: ExtensionManifest, state: str = "DISCOVERED"
) -> dict[str, object]:
    return {
        "id": manifest.id,
        "name": manifest.name,
        "version": manifest.version,
        "state": state,
        "entrypoint": manifest.entrypoint,
        "slots": {key: list(value) for key, value in manifest.slots.items()},
        "tool_risks": {tool.id: tool.risk for tool in manifest.tools},
        "capabilities": {
            "required": list(manifest.capabilities.required),
            "optional": list(manifest.capabilities.optional),
        },
    }


def _record_view(record: ExtensionRecord) -> dict[str, object]:
    return {
        **_manifest_view(record.manifest, record.state.value),
        "artifact_hash": record.artifact_hash,
        "install_path": record.install_path,
        "data_retained": record.data_retained,
        "tombstone": record.tombstone,
    }


def _mail_account_view(record: MailAccountRecord) -> dict[str, object]:
    return {
        "account_id": record.account_id,
        "address": record.address,
        "imap_host": record.imap_host,
        "imap_port": record.imap_port,
        "smtp_host": record.smtp_host,
        "smtp_port": record.smtp_port,
        "secret_handle_id": record.secret_handle_id,
        "display_name": record.display_name,
        "read_enabled": record.read_enabled,
        "send_enabled": record.send_enabled,
        "tls_mode": record.tls_mode,
        "generation": record.generation,
        "fingerprint": record.fingerprint(),
    }


def _preview_view(preview: InstallationPreview) -> dict[str, object]:
    return {
        "plan_id": preview.plan_id,
        "confirmation_nonce": preview.confirmation_nonce,
        "preview_hash": preview.preview_hash,
        "expires_at": preview.expires_at.isoformat(),
        "mode": preview.mode,
        "replaces_version": preview.replaces_version,
        "source": preview.source,
        "artifact_hash": preview.artifact_hash,
        "extension_id": preview.extension_id,
        "extension_name": preview.extension_name,
        "extension_version": preview.extension_version,
        "entrypoint": preview.entrypoint,
        "slots": {key: list(value) for key, value in preview.slots.items()},
        "tool_risks": dict(preview.tool_risks),
        "required_capabilities": list(preview.required_capabilities),
        "optional_capabilities": list(preview.optional_capabilities),
        "warning": preview.warning,
        "executed_code": False,
    }


def _operation_view(operation: ExtensionOperation) -> dict[str, object]:
    return {
        "id": operation.id,
        "extension_id": operation.extension_id,
        "operation": operation.operation,
        "status": operation.status.value,
        "diagnostic_code": operation.diagnostic_code,
        "created_at": operation.created_at.isoformat(),
        "updated_at": operation.updated_at.isoformat(),
    }


def _config_schema(manifest: ExtensionManifest) -> dict[str, Any] | None:
    try:
        return load_extension_config_schema(manifest)
    except ExtensionConfigError as exc:
        raise ApplicationError(
            "EXTENSION_CONFIG_SCHEMA_UNREADABLE",
            "The extension config schema could not be read.",
            500,
        ) from exc


async def _resolve_manifest(
    selected: Container, extension_id: str
) -> ExtensionManifest:
    record = await selected.extension_supervisor.record(extension_id)
    if record is not None:
        return record.manifest
    manifests = selected.extension_registry.discover(
        str(selected.bundled_extensions_root)
    )
    manifest = next((item for item in manifests if item.id == extension_id), None)
    if manifest is None:
        raise ApplicationError("EXTENSION_NOT_FOUND", "Extension was not discovered.", 404)
    return manifest


def _application_error(exc: Exception) -> ApplicationError:
    if isinstance(exc, ConfirmationRequiredError):
        return ApplicationError("CONFIRMATION_REQUIRED", str(exc), 409)
    if isinstance(exc, ManifestValidationError):
        return ApplicationError("INVALID_EXTENSION_MANIFEST", str(exc), 422)
    if isinstance(exc, InvalidLifecycleTransition):
        return ApplicationError("INVALID_LIFECYCLE_TRANSITION", str(exc), 409)
    code = getattr(exc, "code", "EXTENSION_ERROR")
    status = 400
    if code in {"EXTENSION_NOT_FOUND", "OPERATION_NOT_FOUND", "PLAN_NOT_FOUND"}:
        status = 404
    elif code in {
        "PLAN_MODE_MISMATCH",
        "PLAN_BASELINE_CHANGED",
        "STATE_CONFLICT",
        "INSTALL_CONFLICT",
        "OPERATION_IN_PROGRESS",
        "IDEMPOTENCY_CONFLICT",
    }:
        status = 409
    elif code == "PURGE_NOT_IMPLEMENTED":
        status = 501
    return ApplicationError(code, str(exc), status)


def _command_key(request: Request) -> str | None:
    key = getattr(request.state, "idempotency_key", None)
    return key if isinstance(key, str) and key else None


def _confirmation(payload: InstallConfirmationRequest) -> InstallationConfirmation:
    return InstallationConfirmation(
        plan_id=payload.plan_id,
        confirmation_nonce=payload.confirmation_nonce,
        preview_hash=payload.preview_hash,
        actor="local-admin",
        confirmed_at=datetime.now(UTC),
        accepted_warning=payload.accepted_warning,
    )


def create_app(
    *, settings: Settings | None = None, container: Container | None = None
) -> FastAPI:
    settings = settings or Settings.from_env()
    container = container or build_container(settings)
    supervisor = container.extension_supervisor

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        await container.storage.startup()
        try:
            # A persisted ENABLED extension is restarted once; failures quarantine.
            await supervisor.recover()
            await container.refresh_tool_registry()
            await container.mail_ledger.recover_stale_executions(
                active_owners=container.mail_execution_owners.active()
            )
            yield
        finally:
            await supervisor.stop_all()
            await container.storage.close()

    application = FastAPI(
        title="Personal Assistant Local Admin API",
        version="0.1.0",
        description="Must listen on loopback; never publish through Tunnel or Tailscale.",
        lifespan=lifespan,
    )
    application.state.container = container
    application.add_middleware(IdempotencyKeyMiddleware)
    application.add_middleware(RequestIdMiddleware)
    install_error_handlers(application)

    @application.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok", "scope": "local-admin"}

    @application.get("/admin/v1/extensions")
    async def extensions(
        selected: Container = Depends(get_container),
    ) -> dict[str, object]:
        manifests = selected.extension_registry.discover(
            str(selected.bundled_extensions_root)
        )
        records = {
            record.manifest.id: record
            for record in await selected.extension_supervisor.records()
        }
        items = []
        for manifest in manifests:
            record = records.pop(manifest.id, None)
            items.append(
                _record_view(record) if record is not None else _manifest_view(manifest)
            )
        items.extend(_record_view(record) for _, record in sorted(records.items()))
        return {"items": items}

    @application.get("/admin/v1/extensions/{extension_id}")
    async def extension_status(
        extension_id: str,
        selected: Container = Depends(get_container),
    ) -> dict[str, object]:
        record = await selected.extension_supervisor.record(extension_id)
        if record is not None:
            return _record_view(record)
        manifests = selected.extension_registry.discover(
            str(selected.bundled_extensions_root)
        )
        manifest = next((item for item in manifests if item.id == extension_id), None)
        if manifest is None:
            raise ApplicationError("EXTENSION_NOT_FOUND", "Extension was not discovered.", 404)
        return _manifest_view(manifest)

    @application.get("/admin/v1/extensions/{extension_id}/config")
    async def extension_config(
        extension_id: str,
        selected: Container = Depends(get_container),
    ) -> dict[str, object]:
        manifest = await _resolve_manifest(selected, extension_id)
        return {
            "extension_id": extension_id,
            "config": dict(await selected.extension_config_store.get(extension_id)),
            "config_schema": _config_schema(manifest),
        }

    @application.put("/admin/v1/extensions/{extension_id}/config")
    async def update_extension_config(
        extension_id: str,
        payload: ConfigUpdateRequest,
        selected: Container = Depends(get_container),
    ) -> dict[str, object]:
        manifest = await _resolve_manifest(selected, extension_id)
        try:
            validated = validate_extension_config(
                _config_schema(manifest), payload.config, field_name="config"
            )
        except ExtensionConfigError as exc:
            raise ApplicationError("INVALID_EXTENSION_CONFIG", str(exc), 422) from exc
        await selected.extension_config_store.save(extension_id, validated)
        return {
            "extension_id": extension_id,
            "config": validated,
            "restart_required": True,
        }

    @application.get("/admin/v1/mail/accounts")
    async def mail_accounts(
        selected: Container = Depends(get_container),
    ) -> dict[str, object]:
        records = await selected.mail_accounts.list()
        return {"accounts": [_mail_account_view(record) for record in records]}

    @application.put("/admin/v1/mail/accounts")
    async def update_mail_accounts(
        payload: MailAccountsUpdateRequest,
        selected: Container = Depends(get_container),
    ) -> dict[str, object]:
        # Host-owned registry: extensions can only reference account ids, never
        # submit endpoints or credential handles.  The local user registers the
        # non-secret metadata and the SecretHandle id here (loopback only).
        try:
            records = tuple(
                MailAccountRecord(
                    account_id=item.account_id,
                    address=item.address,
                    imap_host=item.imap_host,
                    imap_port=item.imap_port,
                    smtp_host=item.smtp_host,
                    smtp_port=item.smtp_port,
                    secret_handle_id=item.secret_handle_id,
                    display_name=item.display_name,
                    read_enabled=item.read_enabled,
                    send_enabled=item.send_enabled,
                    tls_mode=item.tls_mode,
                )
                for item in payload.accounts
            )
        except ValueError as exc:
            raise ApplicationError("INVALID_MAIL_ACCOUNT", str(exc), 422) from exc
        try:
            await selected.mail_accounts.replace_all(records)
        except ValueError as exc:
            raise ApplicationError("INVALID_MAIL_ACCOUNT", str(exc), 422) from exc
        # The registry bumps each existing account's generation, so return the
        # stored records rather than the pre-write request objects.
        stored = await selected.mail_accounts.list()
        return {"accounts": [_mail_account_view(record) for record in stored]}

    @application.post("/admin/v1/extensions/inspect")
    async def inspect_extension(
        payload: InspectRequest,
        selected: Container = Depends(get_container),
    ) -> dict[str, object]:
        try:
            preview = await selected.extension_supervisor.inspect(payload.source)
        except (ExtensionError, OSError) as exc:
            raise _application_error(exc) from exc
        return _preview_view(preview)

    @application.post("/admin/v1/extensions/install", status_code=202)
    async def install_extension(
        payload: InstallConfirmationRequest,
        request: Request,
        selected: Container = Depends(get_container),
    ) -> dict[str, object]:
        try:
            operation = await selected.extension_supervisor.begin_install(
                payload.plan_id,
                _confirmation(payload),
                idempotency_key=_command_key(request),
            )
        except (ExtensionError, OSError) as exc:
            raise _application_error(exc) from exc
        return _operation_view(operation)

    @application.post("/admin/v1/extension-plans/{plan_id}/reject")
    async def reject_plan(
        plan_id: str,
        selected: Container = Depends(get_container),
    ) -> dict[str, object]:
        try:
            record = await selected.extension_supervisor.reject_plan(plan_id)
        except (ExtensionError, OSError) as exc:
            raise _application_error(exc) from exc
        return _record_view(record)

    @application.post("/admin/v1/extensions/{extension_id}/{operation}", status_code=202)
    async def extension_operation(
        extension_id: str,
        operation: str,
        request: Request,
        selected: Container = Depends(get_container),
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        idempotency_key = _command_key(request)
        try:
            if operation == "enable":
                result = await selected.extension_supervisor.begin_enable(
                    extension_id, idempotency_key=idempotency_key
                )
            elif operation == "disable":
                result = await selected.extension_supervisor.begin_disable(
                    extension_id, idempotency_key=idempotency_key
                )
            elif operation == "rollback":
                result = await selected.extension_supervisor.begin_rollback(
                    extension_id, idempotency_key=idempotency_key
                )
            elif operation == "uninstall":
                result = await selected.extension_supervisor.begin_uninstall(
                    extension_id, idempotency_key=idempotency_key
                )
            elif operation == "upgrade":
                if not payload:
                    raise ApplicationError(
                        "CONFIRMATION_REQUIRED",
                        "upgrade requires an exact preview confirmation",
                        409,
                    )
                try:
                    confirmation_request = InstallConfirmationRequest.model_validate(payload)
                except ValidationError as exc:
                    raise ApplicationError(
                        "CONFIRMATION_REQUIRED",
                        "upgrade requires an exact preview confirmation",
                        409,
                    ) from exc
                result = await selected.extension_supervisor.begin_upgrade(
                    extension_id,
                    confirmation_request.plan_id,
                    _confirmation(confirmation_request),
                    idempotency_key=idempotency_key,
                )
            elif operation == "purge-data":
                raise ApplicationError(
                    "EXTENSION_DATA_PURGE_NOT_IMPLEMENTED",
                    "Permanent data purge has no independent confirmation flow yet.",
                    501,
                )
            else:
                raise ApplicationError("UNKNOWN_EXTENSION_OPERATION", operation, 404)
        except (ExtensionError, OSError) as exc:
            raise _application_error(exc) from exc
        return _operation_view(result)

    @application.get("/admin/v1/extension-operations/{operation_id}")
    async def operation_status(
        operation_id: str,
        selected: Container = Depends(get_container),
    ) -> dict[str, object]:
        operation = await selected.extension_supervisor.operation(operation_id)
        if operation is None:
            raise ApplicationError(
                "EXTENSION_OPERATION_NOT_FOUND",
                f"No operation exists: {operation_id}",
                404,
            )
        return _operation_view(operation)

    return application


app = create_app()
