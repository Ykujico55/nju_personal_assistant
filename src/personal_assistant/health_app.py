"""Minimal health-only surface intended for optional Tailscale exposure."""

from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    application = FastAPI(
        title="Personal Assistant Health",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @application.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()

