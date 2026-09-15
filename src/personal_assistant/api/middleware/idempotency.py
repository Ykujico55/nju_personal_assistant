from __future__ import annotations

import re

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")


class IdempotencyKeyMiddleware(BaseHTTPMiddleware):
    """Require a stable key for commands; storage adapters enforce actual replay."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        is_command = request.method in {"POST", "PUT", "PATCH", "DELETE"}
        in_control_plane = request.url.path.startswith(("/api/v1/", "/admin/v1/"))
        if is_command and in_control_plane:
            key = request.headers.get("Idempotency-Key", "")
            if not _KEY.fullmatch(key):
                request_id = getattr(request.state, "request_id", "unknown")
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": {
                            "code": "IDEMPOTENCY_KEY_REQUIRED",
                            "message": "A 1-200 character Idempotency-Key is required.",
                            "request_id": request_id,
                            "retryable": False,
                            "details": {},
                        }
                    },
                )
            request.state.idempotency_key = key
        return await call_next(request)

