"""No-GMS pending hints to a user-controlled, loopback-only ntfy relay."""
from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import urlsplit

import httpx

from personal_assistant.core.notifications.push import PushDeliveryReport
from personal_assistant.core.secrets import SecretHandle, SecretStorePort, SecretUnavailableError
from personal_assistant.domain import ValidationError
from personal_assistant.settings import (
    normalize_ntfy_publish_url,
    normalize_public_origin,
    normalize_secret_handle_id,
)

_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_RISK = frozenset({"R0", "R1", "R2", "R3"})
_MAX_RECEIPT_BYTES = 8192
_TITLE = "Personal Assistant"


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate notification receipt field")
        result[name] = value
    return result


class LocalNtfyNotifier:
    """Relay acceptance confirms no phone delivery; an ambiguous POST is never retried."""

    def __init__(
        self, *, publish_url: str, public_origin: str,
        secret_handle_id: str, secrets: SecretStorePort,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._publish_url = normalize_ntfy_publish_url(publish_url)
        self._public_origin = normalize_public_origin(public_origin)
        self._topic = urlsplit(self._publish_url).path[1:]
        self._secret_handle_id = normalize_secret_handle_id(
            secret_handle_id, field_name="PA_NTFY_SECRET_HANDLE",
        )
        self._secrets = secrets
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(5.0), follow_redirects=False, trust_env=False,
        )

    async def notify_pending(self, *, task_id: str, risk: str) -> PushDeliveryReport:
        if _TASK_ID.fullmatch(task_id) is None or risk not in _RISK:
            raise ValidationError("notification metadata is invalid")
        try:
            token = await self._secrets.resolve_for_broker(
                SecretHandle(self._secret_handle_id, "ntfy_publish_token"),
                purpose="notification_publish",
            )
        except (SecretUnavailableError, KeyError, ValueError):
            return PushDeliveryReport(reconfigure_required=1)
        if not re.fullmatch(r"[\x21-\x7e]{1,512}", token):
            return PushDeliveryReport(reconfigure_required=1)
        message = json.dumps(
            {"kind": "pending", "risk": risk, "task_id": task_id}, separators=(",", ":"),
        )
        click = f"{self._public_origin}/ui/?task={task_id}"
        try:
            async with asyncio.timeout(5.0), self._client.stream(
                "POST", self._publish_url, content=message.encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {token}", "Content-Type": "text/plain",
                    "Title": _TITLE, "Click": click, "Cache": "no",
                }, timeout=5.0, follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    return PushDeliveryReport(unknown=1)
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > _MAX_RECEIPT_BYTES:
                        return PushDeliveryReport(unknown=1)
                    body.extend(chunk)
                receipt = json.loads(body, object_pairs_hook=_unique_fields)
                if self._confirms(receipt, message=message, click=click):
                    return PushDeliveryReport(accepted=1)
        except (httpx.HTTPError, TimeoutError, ValueError, UnicodeError):
            # No exception string or request is exposed; it could contain a bearer token.
            return PushDeliveryReport(unknown=1)
        finally:
            del token
        return PushDeliveryReport(unknown=1)

    def _confirms(self, value: object, *, message: str, click: str) -> bool:
        if not isinstance(value, dict):
            return False
        return (
            isinstance(value.get("id"), str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value["id"]) is not None
            and type(value.get("time")) is int and value["time"] > 0
            and value.get("event") == "message"
            and value.get("topic") == self._topic
            and value.get("message") == message
            and value.get("title") == _TITLE and value.get("click") == click
            and not any(field in value for field in ("actions", "attachment", "email"))
        )

    async def aclose(self) -> None:
        await self._client.aclose()
