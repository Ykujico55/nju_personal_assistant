from __future__ import annotations

import sysconfig
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from personal_assistant import __version__
from personal_assistant.api.errors import install_error_handlers
from personal_assistant.api.middleware import (
    CloudflareAccessBoundaryMiddleware,
    CsrfOriginMiddleware,
    IdempotencyKeyMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
)
from personal_assistant.api.v1 import router
from personal_assistant.bootstrap import Container, build_container
from personal_assistant.infrastructure.auth import (
    AccessTokenVerifier,
    cloudflare_access_verifier_from_settings,
)
from personal_assistant.settings import Settings


def _pwa_root() -> Path:
    source_tree = Path(__file__).resolve().parents[2] / "web" / "pwa"
    if source_tree.is_dir():
        return source_tree
    installed = (
        Path(sysconfig.get_path("data")) / "share" / "personal-assistant" / "pwa"
    )
    if installed.is_dir():
        return installed
    raise RuntimeError("PWA static assets are missing from this installation")


def create_app(
    *,
    settings: Settings | None = None,
    container: Container | None = None,
    access_verifier: AccessTokenVerifier | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()
    container = container or build_container(settings)
    verifier: AccessTokenVerifier | None = None
    if settings.trust_cloudflare_access:
        verifier = access_verifier or cloudflare_access_verifier_from_settings(settings)

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        # Fail closed: a database that is unreachable or fails migration stops
        # startup instead of silently falling back to in-memory state.
        await container.storage.startup()
        try:
            yield
        finally:
            # Closing the verifier must never prevent storage from closing.
            try:
                if verifier is not None:
                    await verifier.aclose()
            finally:
                await container.storage.close()

    application = FastAPI(
        title="Personal Assistant API",
        version=__version__,
        description="User control plane; extension code management is not exposed here.",
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    application.state.container = container
    application.state.access_verifier = verifier
    application.add_middleware(IdempotencyKeyMiddleware)
    application.add_middleware(CsrfOriginMiddleware, settings=settings)
    application.add_middleware(
        CloudflareAccessBoundaryMiddleware, settings=settings, verifier=verifier
    )
    application.add_middleware(RequestIdMiddleware)
    application.add_middleware(SecurityHeadersMiddleware, settings=settings)
    install_error_handlers(application)
    application.include_router(router)
    application.mount("/ui", StaticFiles(directory=_pwa_root(), html=True), name="pwa")

    @application.get("/healthz", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "storage": settings.storage_backend}

    @application.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"service": "personal-assistant", "api": "/api/v1", "docs": "/docs"}

    return application


app = create_app()
