from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from personal_assistant.settings import Settings


class CsrfOriginMiddleware(BaseHTTPMiddleware):
    """Origin/custom-header gate for browser commands behind Access cookies."""

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        command = request.method in {"POST", "PUT", "PATCH", "DELETE"}
        if (
            self._settings.trust_cloudflare_access
            and command
            and request.url.path.startswith("/api/v1/")
        ):
            origin_ok = request.headers.get("Origin") == self._settings.public_origin
            fetch_site_ok = request.headers.get("Sec-Fetch-Site") == "same-origin"
            custom_header_ok = (
                request.headers.get("X-Requested-With") == "personal-assistant-pwa"
            )
            if not (origin_ok and fetch_site_ok and custom_header_ok):
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": {
                            "code": "CSRF_CHECK_FAILED",
                            "message": "Origin or browser command headers did not match.",
                            "request_id": getattr(request.state, "request_id", "unknown"),
                            "retryable": False,
                            "details": {},
                        }
                    },
                )
        return await call_next(request)

