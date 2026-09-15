"""Loopback-only extension and recovery control plane."""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field

from personal_assistant.api.dependencies import get_container
from personal_assistant.api.errors import ApplicationError, install_error_handlers
from personal_assistant.api.middleware import IdempotencyKeyMiddleware, RequestIdMiddleware
from personal_assistant.bootstrap import Container, build_container
from personal_assistant.core.extensions import (
    ExtensionManifest,
    ManifestParser,
    ManifestValidationError,
    compute_artifact_hash,
)
from personal_assistant.core.extensions.lifecycle import TRUST_WARNING
from personal_assistant.settings import Settings


class InspectRequest(BaseModel):
    source: str = Field(min_length=1, max_length=4096)


class InstallRequest(BaseModel):
    source: str = Field(min_length=1, max_length=4096)


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


def create_app(
    *, settings: Settings | None = None, container: Container | None = None
) -> FastAPI:
    settings = settings or Settings.from_env()
    container = container or build_container(settings)
    application = FastAPI(
        title="Personal Assistant Local Admin API",
        version="0.1.0",
        description="Must listen on loopback; never publish through Tunnel or Tailscale.",
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
        active = selected.extension_registry.snapshot.extensions
        return {
            "items": [
                _manifest_view(
                    manifest,
                    active[manifest.id].state.value
                    if manifest.id in active
                    else "DISCOVERED",
                )
                for manifest in manifests
            ]
        }

    @application.get("/admin/v1/extensions/{extension_id}")
    async def extension_status(
        extension_id: str,
        selected: Container = Depends(get_container),
    ) -> dict[str, object]:
        manifests = selected.extension_registry.discover(
            str(selected.bundled_extensions_root)
        )
        manifest = next((item for item in manifests if item.id == extension_id), None)
        if manifest is None:
            raise ApplicationError("EXTENSION_NOT_FOUND", "Extension was not discovered.", 404)
        active = selected.extension_registry.snapshot.extensions
        return _manifest_view(
            manifest,
            active[manifest.id].state.value if manifest.id in active else "DISCOVERED",
        )

    @application.post("/admin/v1/extensions/inspect")
    async def inspect_extension(payload: InspectRequest) -> dict[str, object]:
        root = Path(payload.source).resolve()
        try:
            manifest = ManifestParser().parse(root)
        except ManifestValidationError as exc:
            raise ApplicationError("INVALID_EXTENSION_MANIFEST", str(exc), 422) from exc
        return {
            **_manifest_view(manifest, "STAGED_PREVIEW_ONLY"),
            "source": str(root),
            "artifact_hash": compute_artifact_hash(root),
            "warning": TRUST_WARNING,
            "executed_code": False,
        }

    @application.post("/admin/v1/extensions/install")
    async def install_extension(payload: InstallRequest) -> None:
        del payload
        raise ApplicationError(
            "EXTENSION_SUPERVISOR_NOT_IMPLEMENTED",
            "Install execution is intentionally locked until task F02 is completed.",
            501,
        )

    @application.post("/admin/v1/extensions/{extension_id}/{operation}")
    async def extension_operation(extension_id: str, operation: str) -> None:
        allowed = {"enable", "disable", "upgrade", "rollback", "uninstall", "purge"}
        if operation not in allowed:
            raise ApplicationError("UNKNOWN_EXTENSION_OPERATION", operation, 404)
        raise ApplicationError(
            "EXTENSION_SUPERVISOR_NOT_IMPLEMENTED",
            f"{operation} for {extension_id} is intentionally locked until task F02.",
            501,
        )

    @application.get("/admin/v1/extension-operations/{operation_id}")
    async def operation_status(operation_id: str) -> None:
        raise ApplicationError(
            "EXTENSION_OPERATION_NOT_FOUND",
            f"No operation exists: {operation_id}",
            404,
        )

    return application


app = create_app()
