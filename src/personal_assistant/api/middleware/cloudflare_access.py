from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from personal_assistant.settings import Settings


class CloudflareAccessBoundaryMiddleware(BaseHTTPMiddleware):
    """Fail-closed seam for F03; never trusts identity headers without JWT verification."""

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if self._settings.trust_cloudflare_access:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "CLOUDFLARE_ACCESS_VERIFIER_NOT_IMPLEMENTED",
                        "message": "Public remote access is locked until task F03 is completed.",
                        "request_id": getattr(request.state, "request_id", "unknown"),
                        "retryable": False,
                        "details": {},
                    }
                },
            )
        request.state.actor_id = "development-owner"
        return await call_next(request)
