from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from personal_assistant.core.approvals import ApprovalExpiredError
from personal_assistant.domain import (
    ConcurrentModificationError,
    DomainError,
    NotFoundError,
    ValidationError,
)


@dataclass(slots=True)
class ApplicationError(Exception):
    code: str
    message: str
    status_code: int = 400
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)


def _body(
    request: Request,
    *,
    code: str,
    message: str,
    retryable: bool,
    details: dict[str, Any] | None = None,
) -> dict[str, object]:
    return {
        "error": {
            "code": code.upper(),
            "message": message,
            "request_id": getattr(request.state, "request_id", "unknown"),
            "retryable": retryable,
            "details": details or {},
        }
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def request_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = {
            "fields": [
                {"location": list(item["loc"]), "type": item["type"]}
                for item in exc.errors()
            ]
        }
        return JSONResponse(
            status_code=422,
            content=_body(
                request,
                code="REQUEST_VALIDATION_ERROR",
                message="Request validation failed.",
                retryable=False,
                details=details,
            ),
        )

    @app.exception_handler(ApplicationError)
    async def application_error(request: Request, exc: ApplicationError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(
                request,
                code=exc.code,
                message=exc.message,
                retryable=exc.retryable,
                details=exc.details,
            ),
        )

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        status = 400
        if isinstance(exc, NotFoundError):
            status = 404
        elif isinstance(exc, ConcurrentModificationError):
            status = 409
        elif isinstance(exc, ApprovalExpiredError):
            status = 410
        elif isinstance(exc, ValidationError):
            status = 422
        return JSONResponse(
            status_code=status,
            content=_body(
                request,
                code=getattr(exc, "code", "domain_error"),
                message=str(exc),
                retryable=False,
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(
                request,
                code="HTTP_ERROR",
                message=str(exc.detail),
                retryable=False,
            ),
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        del exc
        return JSONResponse(
            status_code=500,
            content=_body(
                request,
                code="INTERNAL_ERROR",
                message="The request failed. Use request_id for local diagnostics.",
                retryable=False,
            ),
        )
