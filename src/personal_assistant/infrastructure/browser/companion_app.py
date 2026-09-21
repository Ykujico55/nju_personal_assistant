"""Loopback HTTP surface of the Desktop Companion.

The host talks to the companion exclusively over ``127.0.0.1``.  Every request
must carry a capability; request bodies and responses never contain cookies,
storage state, passwords, verification codes or screenshots.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from .companion import CompanionError, DesktopCompanion

MAX_REQUEST_BODY_BYTES = 64 * 1024


def _token(header: str | None) -> str:
    if not header:
        return ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(item) for item in value if isinstance(item, (str, int, float)))


def _pairs(value: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    pairs: list[tuple[str, str]] = []
    for item in value:
        if isinstance(item, Mapping) and "locator" in item and "value" in item:
            pairs.append((str(item["locator"]), str(item["value"])))
    return tuple(pairs)


def create_companion_app(companion: DesktopCompanion) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def _limit_body(request: Request, call_next: Any) -> Any:
        raw_length = request.headers.get("content-length")
        if raw_length is not None:
            try:
                length = int(raw_length)
            except ValueError:
                length = MAX_REQUEST_BODY_BYTES + 1
            if length > MAX_REQUEST_BODY_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "code": "REQUEST_TOO_LARGE",
                            "message": "request body is too large",
                        }
                    },
                )
        return await call_next(request)

    @app.exception_handler(CompanionError)
    async def _handle(_: Request, exc: CompanionError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": str(exc)}},
        )

    @app.post("/v1/sessions")
    async def create_session(
        request: Request, authorization: str | None = Header(default=None)
    ) -> Mapping[str, Any]:
        body = await request.json()
        return await companion.create_session(
            session_id=str(body.get("session_id", "")),
            purpose=str(body.get("purpose", "")),
            allowed_origins=_strings(body.get("allowed_origins", ())),
            task_id=str(body.get("task_id", "")),
            extension_id=str(body.get("extension_id", "")),
            token=_token(authorization),
        )

    @app.get("/v1/sessions/{session_id}")
    async def status(
        session_id: str, authorization: str | None = Header(default=None)
    ) -> Mapping[str, Any]:
        return await companion.status(session_id, token=_token(authorization))

    @app.post("/v1/sessions/{session_id}/navigate")
    async def navigate(
        session_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> Mapping[str, Any]:
        # Authenticate before parsing so an unauthenticated caller cannot force
        # JSON/buffer work on the companion.
        await companion.authorize(session_id, token=_token(authorization))
        body = await request.json()
        return await companion.navigate(
            session_id,
            url=str(body.get("url", "")),
            login_paths=_strings(body.get("login_paths", ())),
            token=_token(authorization),
        )

    @app.post("/v1/sessions/{session_id}/snapshot")
    async def snapshot(
        session_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> Mapping[str, Any]:
        await companion.authorize(session_id, token=_token(authorization))
        body = await request.json()
        return await companion.snapshot(
            session_id,
            prohibited_terms=_strings(body.get("prohibited_terms", ())),
            scan_text=bool(body.get("scan_text", False)),
            login_paths=_strings(body.get("login_paths", ())),
            token=_token(authorization),
        )

    @app.post("/v1/sessions/{session_id}/fill")
    async def fill(
        session_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> Mapping[str, Any]:
        await companion.authorize(session_id, token=_token(authorization))
        body = await request.json()
        return await companion.fill(
            session_id, fields=_pairs(body.get("fields", ())), token=_token(authorization)
        )

    @app.post("/v1/sessions/{session_id}/find_text")
    async def find_text(
        session_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> Mapping[str, Any]:
        await companion.authorize(session_id, token=_token(authorization))
        body = await request.json()
        return await companion.find_text(
            session_id, query=str(body.get("query", "")), token=_token(authorization)
        )

    @app.post("/v1/sessions/{session_id}/collect")
    async def collect(
        session_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> Mapping[str, Any]:
        await companion.authorize(session_id, token=_token(authorization))
        body = await request.json()
        return await companion.collect_matches(
            session_id,
            pattern=str(body.get("pattern", "")),
            limit=int(body.get("limit", 0) or 0),
            url=str(body.get("url", "")),
            token=_token(authorization),
        )

    @app.post("/v1/sessions/{session_id}/activate")
    async def activate(
        session_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> Mapping[str, Any]:
        await companion.authorize(session_id, token=_token(authorization))
        body = await request.json()
        return await companion.activate(
            session_id,
            locator=str(body.get("locator", "")),
            expected_path=str(body.get("expected_path", "")),
            token=_token(authorization),
        )

    @app.post("/v1/sessions/{session_id}/click")
    async def click(
        session_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> Mapping[str, Any]:
        await companion.authorize(session_id, token=_token(authorization))
        body = await request.json()
        return await companion.click(
            session_id,
            action_id=str(body.get("action_id", "")),
            locator=str(body.get("locator", "")),
            receipt_locator=str(body.get("receipt_locator", "")),
            expected_method=str(body.get("expected_method", "")),
            expected_origin=str(body.get("expected_origin", "")),
            expected_path=str(body.get("expected_path", "")),
            token=_token(authorization),
        )

    @app.post("/v1/sessions/{session_id}/close")
    async def close(
        session_id: str, authorization: str | None = Header(default=None)
    ) -> Mapping[str, Any]:
        await companion.close(session_id, token=_token(authorization))
        return {"closed": True}

    @app.post("/v1/sessions/{session_id}/cancel")
    async def cancel(
        session_id: str, authorization: str | None = Header(default=None)
    ) -> Mapping[str, Any]:
        await companion.cancel(session_id, token=_token(authorization))
        return {"cancelled": True}

    @app.post("/v1/revoke")
    async def revoke(
        request: Request, authorization: str | None = Header(default=None)
    ) -> Mapping[str, Any]:
        body = await request.json()
        await companion.revoke(
            session_id=str(body.get("session_id", "")), token=_token(authorization)
        )
        return {"revoked": True}

    @app.get("/v1/diagnostics")
    async def diagnostics(
        authorization: str | None = Header(default=None)
    ) -> Mapping[str, Any]:
        return await companion.diagnostics(token=_token(authorization))

    @app.get("/healthz")
    async def healthz() -> Mapping[str, str]:
        return {"status": "ok"}

    return app


__all__ = ["create_companion_app"]
