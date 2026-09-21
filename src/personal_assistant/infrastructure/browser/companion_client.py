"""Loopback client implementing :class:`DesktopBrowserPort`.

The host keeps the companion capability in memory only.  A connection failure
is a typed ``BrowserUnavailableError``; HTTP error envelopes are mapped back to
typed core errors without echoing message bodies into logs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

from personal_assistant.core.browser import (
    BrowserPolicyError,
    BrowserUnavailableError,
)

COMMAND_TIMEOUT_SECONDS = 30.0


def _loopback_base_url(raw: str) -> str:
    parts = urlsplit(raw)
    if parts.scheme != "http":
        raise BrowserUnavailableError("the companion URL must use http over loopback")
    host = (parts.hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise BrowserUnavailableError("the companion URL must be loopback")
    if not parts.port:
        raise BrowserUnavailableError("the companion URL requires a port")
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
        raise BrowserUnavailableError("the companion URL must be a bare origin")
    return f"http://{host}:{parts.port}"


class LoopbackCompanionClient:
    def __init__(
        self,
        *,
        base_url: str,
        root_capability: str,
        http_client: httpx.AsyncClient | None = None,
        command_timeout_seconds: float = COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        if not root_capability:
            raise BrowserUnavailableError("the companion capability is missing")
        self._base_url = _loopback_base_url(base_url)
        self._root = root_capability
        self._client = http_client
        self._owns_client = http_client is None
        self._timeout = command_timeout_seconds
        self._session_tokens: dict[str, str] = {}

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout),
                trust_env=False,
            )
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        payload: Mapping[str, Any] | None = None,
        deadline: float | None = None,
    ) -> Mapping[str, Any]:
        # The capability is only ever used to build one header value inside this
        # frame.  Errors are raised from a scrubbed frame with no exception
        # chain, so a logged traceback can never expose the bearer token.
        headers = {"Authorization": f"Bearer {token}"}
        body = dict(payload) if payload is not None else None
        response: httpx.Response | None = None
        failure = ""
        try:
            response = await asyncio.wait_for(
                self._http().request(method, path, json=body, headers=headers),
                timeout=deadline or self._timeout,
            )
        except asyncio.CancelledError:
            del token, headers, body, response
            raise
        except TimeoutError:
            failure = "timeout"
        except httpx.HTTPError:
            failure = "transport"
        if failure:
            del token, headers, body, response
            if failure == "timeout":
                raise BrowserUnavailableError("the desktop companion timed out")
            raise BrowserUnavailableError("the desktop companion is unreachable")
        assert response is not None
        if response.status_code >= 500:
            del token, headers, body, response
            raise BrowserUnavailableError("the desktop companion failed")
        if response.status_code >= 400:
            code, message = _error_envelope(response)
            del token, headers, body, response
            if code.startswith("CAPABILITY") or code == "SESSION_NOT_FOUND":
                raise BrowserUnavailableError(message)
            raise BrowserPolicyError(code, message)
        try:
            data = response.json()
        except ValueError:
            del token, headers, body, response
            raise BrowserUnavailableError("the companion returned invalid JSON") from None
        del token, headers, body, response
        if not isinstance(data, Mapping):
            raise BrowserUnavailableError("the companion returned an invalid response")
        return data

    # ------------------------------------------------- DesktopBrowserPort

    async def create_session(
        self,
        *,
        session_id: str,
        purpose: str,
        allowed_origins: tuple[str, ...],
        task_id: str,
        extension_id: str,
    ) -> Mapping[str, Any]:
        data = await self._request(
            "POST",
            "/v1/sessions",
            token=self._root,
            payload={
                "session_id": session_id,
                "purpose": purpose,
                "allowed_origins": list(allowed_origins),
                "task_id": task_id,
                "extension_id": extension_id,
            },
        )
        capability = data.get("capability")
        if not isinstance(capability, str) or not capability:
            raise BrowserUnavailableError("the companion did not issue a session capability")
        self._session_tokens[session_id] = capability
        return data

    async def close_session(self, session_id: str) -> None:
        token = self._session_tokens.pop(session_id, None)
        if token is None:
            return
        await self._request("POST", f"/v1/sessions/{session_id}/close", token=token)

    async def cancel_session(self, session_id: str) -> None:
        token = self._session_tokens.pop(session_id, None)
        if token is None:
            return
        await self._request("POST", f"/v1/sessions/{session_id}/cancel", token=token)

    async def status(self, session_id: str) -> Mapping[str, Any]:
        return await self._request(
            "GET", f"/v1/sessions/{session_id}", token=self._token(session_id)
        )

    async def navigate(self, session_id: str, url: str) -> Mapping[str, Any]:
        return await self._request(
            "POST",
            f"/v1/sessions/{session_id}/navigate",
            token=self._token(session_id),
            payload={"url": url},
        )

    async def snapshot(
        self,
        session_id: str,
        *,
        prohibited_terms: tuple[str, ...] = (),
        scan_text: bool = False,
    ) -> Mapping[str, Any]:
        return await self._request(
            "POST",
            f"/v1/sessions/{session_id}/snapshot",
            token=self._token(session_id),
            payload={
                "prohibited_terms": list(prohibited_terms),
                "scan_text": scan_text,
            },
        )

    async def fill(
        self, session_id: str, fields: tuple[tuple[str, str], ...]
    ) -> Mapping[str, Any]:
        return await self._request(
            "POST",
            f"/v1/sessions/{session_id}/fill",
            token=self._token(session_id),
            payload={"fields": [{"locator": a, "value": b} for a, b in fields]},
        )

    async def find_text(self, session_id: str, query: str) -> Mapping[str, Any]:
        return await self._request(
            "POST",
            f"/v1/sessions/{session_id}/find_text",
            token=self._token(session_id),
            payload={"query": query},
        )

    async def collect_matches(
        self, session_id: str, *, pattern: str, limit: int, url: str = ""
    ) -> Mapping[str, Any]:
        return await self._request(
            "POST",
            f"/v1/sessions/{session_id}/collect",
            token=self._token(session_id),
            payload={"pattern": pattern, "limit": limit, "url": url},
        )

    async def activate(
        self, session_id: str, *, locator: str, expected_path: str
    ) -> Mapping[str, Any]:
        return await self._request(
            "POST",
            f"/v1/sessions/{session_id}/activate",
            token=self._token(session_id),
            payload={"locator": locator, "expected_path": expected_path},
        )

    async def click(
        self,
        session_id: str,
        *,
        action_id: str,
        locator: str,
        receipt_locator: str,
        expected_method: str,
        expected_origin: str,
        expected_path: str,
    ) -> Mapping[str, Any]:
        return await self._request(
            "POST",
            f"/v1/sessions/{session_id}/click",
            token=self._token(session_id),
            payload={
                "action_id": action_id,
                "locator": locator,
                "receipt_locator": receipt_locator,
                "expected_method": expected_method,
                "expected_origin": expected_origin,
                "expected_path": expected_path,
            },
        )

    async def diagnostics(self) -> Mapping[str, Any]:
        """Test/ops diagnostics; the root capability is required."""

        return await self._request("GET", "/v1/diagnostics", token=self._root)

    async def aclose(self) -> None:
        self._session_tokens.clear()
        if self._owns_client and self._client is not None:
            client = self._client
            self._client = None
            try:
                await client.aclose()
            except Exception:
                return None
        return None

    def _token(self, session_id: str) -> str:
        token = self._session_tokens.get(session_id)
        if token is None:
            raise BrowserUnavailableError("the session capability is no longer held")
        return token


def _error_envelope(response: httpx.Response) -> tuple[str, str]:
    try:
        payload = response.json()
    except ValueError:
        return "COMPANION_ERROR", "the companion rejected the request"
    error = payload.get("error") if isinstance(payload, Mapping) else None
    if not isinstance(error, Mapping):
        return "COMPANION_ERROR", "the companion rejected the request"
    code = str(error.get("code", "COMPANION_ERROR"))
    message = str(error.get("message", "the companion rejected the request"))
    return code, message[:500]


__all__ = ["COMMAND_TIMEOUT_SECONDS", "LoopbackCompanionClient"]
