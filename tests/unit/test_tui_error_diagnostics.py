import httpx
import pytest

from personal_assistant.cli.tui.service import WorkbenchError, WorkbenchService


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_server_failure_keeps_safe_correlation_without_error_body(stream):
    identifier = "req_" + "a" * 32

    async def response(request):
        return httpx.Response(
            503,
            headers={"X-Request-ID": identifier},
            json={
                "error": {
                    "code": "MODEL_CREDENTIAL_UNAVAILABLE",
                    "request_id": identifier,
                    "message": "synthetic-private-secret must not be displayed",
                    "details": {"raw": "secret"},
                }
            },
        )

    service = WorkbenchService(transport=httpx.MockTransport(response))
    try:
        with pytest.raises(WorkbenchError) as failure:
            if stream:
                await service.conversation_stream("public", [], "key")
            else:
                await service.status()
        assert failure.value.correlation_id == identifier
        assert "synthetic-private-secret" not in failure.value.message
        assert "凭据" in failure.value.message
    finally:
        await service.aclose()


def test_error_rejects_untrusted_correlation_text():
    error = WorkbenchError("FAILED", correlation_id="private/secret/path")
    assert error.correlation_id is None
