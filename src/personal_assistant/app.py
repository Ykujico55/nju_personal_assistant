from __future__ import annotations

import sysconfig
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
    *, settings: Settings | None = None, container: Container | None = None
) -> FastAPI:
    settings = settings or Settings.from_env()
    container = container or build_container(settings)
    application = FastAPI(
        title="Personal Assistant API",
        version=__version__,
        description="User control plane; extension code management is not exposed here.",
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
    )
    application.state.container = container
    application.add_middleware(IdempotencyKeyMiddleware)
    application.add_middleware(CsrfOriginMiddleware, settings=settings)
    application.add_middleware(CloudflareAccessBoundaryMiddleware, settings=settings)
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
