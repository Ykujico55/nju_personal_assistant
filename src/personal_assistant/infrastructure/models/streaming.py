"""Bounded, cancellation-safe incremental HTTP lines for model adapters."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from personal_assistant.core.models.cleanup import run_cleanup
from personal_assistant.core.models.errors import (
    ModelProviderProtocolError,
    ModelProviderRejectedError,
    ModelProviderResponseTooLargeError,
    ModelProviderTimeoutError,
    ModelProviderUnavailableError,
)
from personal_assistant.core.models.provider import RecipientIdentity

from .base import HttpJsonModelProvider, rejection_code


async def response_lines(
    provider: HttpJsonModelProvider,
    payload: dict[str, Any],
    headers: dict[str, str],
    recipient: RecipientIdentity,
    *,
    deadline: float,
) -> AsyncGenerator[str, None]:
    """One request and one monotonic deadline, including all chunks, without retry.

    The deadline is applied to individual awaits using the remaining total time,
    so yielding to a consumer never leaves an asyncio timeout cancelling its work.
    """
    response = None
    request = None
    client = None
    chunk = b""
    line = b""
    buffer = bytearray()
    failure = ""
    status = 0
    cancelled = False
    size = 0
    try:
        client = await provider._ensure_client()
        request = client.build_request(
            "POST",
            recipient.endpoint + provider.endpoint_path,
            json=payload,
            headers={**headers, "Content-Type": "application/json"},
        )
        response = await asyncio.wait_for(
            client.send(request, stream=True, follow_redirects=False),
            max(0, deadline - asyncio.get_running_loop().time()),
        )
        status = response.status_code
        if not 200 <= status < 300:
            failure = "rejected"
        else:
            chunks = response.aiter_bytes()
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        anext(chunks), max(0, deadline - asyncio.get_running_loop().time())
                    )
                except StopAsyncIteration:
                    break
                size += len(chunk)
                if size > provider._max_response_bytes:
                    failure = "too_large"
                    break
                buffer.extend(chunk)
                while b"\n" in buffer:
                    line, _, tail = bytes(buffer).partition(b"\n")
                    buffer = bytearray(tail)
                    yield line.rstrip(b"\r").decode("utf-8", errors="strict")
            # NDJSON permits its final record without a newline. SSE still needs
            # a blank separator, which its parser validates separately.
            if not failure and buffer:
                yield bytes(buffer).rstrip(b"\r").decode("utf-8", errors="strict")
    except GeneratorExit:
        # Explicit aclose still reports a failed close; it must not turn an
        # otherwise complete provider response into a success.
        pass
    except asyncio.CancelledError:
        cancelled = True
    except (TimeoutError, httpx.TimeoutException):
        failure = "timeout"
    except UnicodeError:
        failure = "protocol"
    except Exception:
        failure = "unavailable"
    finally:
        if response is not None:
            try:
                await run_cleanup(response.aclose())
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                if not failure:
                    failure = "unavailable"
        # No request body, credential header or vendor error survives into a
        # freshly raised error frame or chained transport exception.
        payload = {}
        headers = {}
        response = request = client = None
        chunk = line = b""
        buffer.clear()
    if cancelled:
        raise asyncio.CancelledError()
    errors = {
        "timeout": ModelProviderTimeoutError,
        "protocol": ModelProviderProtocolError,
        "too_large": ModelProviderResponseTooLargeError,
        "unavailable": ModelProviderUnavailableError,
    }
    if failure == "rejected":
        raise ModelProviderRejectedError(
            "the model endpoint returned a non-success status",
            provider_id=recipient.provider_id,
            status_code=status,
            rejection_code=rejection_code(status),
        )
    if failure:
        raise errors[failure](
            "the model stream could not be completed", provider_id=recipient.provider_id
        )
