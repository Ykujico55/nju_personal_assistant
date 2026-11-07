from __future__ import annotations

import copy

import httpx
import pytest

from personal_assistant.cli.tui.service import WorkbenchError, WorkbenchService

FEATURES = ["task_queue", "task_trace", "toolset_projection", "configuration_apply",
            "conversation_stream", "conversation_discard", "chat_stream"]
CAPS = {"protocol_version": 1, "server_version": "0.1.0", "features": FEATURES}
PATH = "/admin/v1/workbench/capabilities"


async def test_negotiate_accepts_supported_protocol_and_extra_features_without_writes():
    calls = []
    declaration = {**CAPS, "features": [*FEATURES, "future_read_feature"]}

    async def respond(request):
        calls.append(request)
        return httpx.Response(200, json=declaration)

    service = WorkbenchService(transport=httpx.MockTransport(respond))
    try:
        assert await service.negotiate() == declaration
        assert len(calls) == 1 and calls[0].method == "GET" and calls[0].url.path == PATH
        assert "Idempotency-Key" not in calls[0].headers
        assert service.pending is None
    finally:
        await service.aclose()


@pytest.mark.parametrize("declaration", [
    {**CAPS, "protocol_version": True}, {**CAPS, "protocol_version": "1"},
    {**CAPS, "protocol_version": 2}, {**CAPS, "protocol_version": 0},
    {**CAPS, "features": FEATURES[:-1]}, {**CAPS, "features": None},
    {**CAPS, "features": [*FEATURES, 1]}, {**CAPS, "server_version": None},
    {**CAPS, "server_version": ""}, {}, [],
])
async def test_negotiate_rejects_mismatched_or_malformed_declarations(declaration):
    service = WorkbenchService(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=declaration)
    ))
    try:
        with pytest.raises(WorkbenchError) as failure:
            await service.negotiate()
        assert failure.value.code == "WORKBENCH_INCOMPATIBLE"
        assert failure.value.uncertain is False
        assert "重启" in failure.value.message
        assert service.pending is None
    finally:
        await service.aclose()


async def test_missing_capabilities_is_incompatible_even_with_non_json_404():
    service = WorkbenchService(transport=httpx.MockTransport(
        lambda request: httpx.Response(404, text="old server page")
    ))
    try:
        with pytest.raises(WorkbenchError) as failure:
            await service.negotiate()
        assert failure.value.code == "WORKBENCH_INCOMPATIBLE"
        assert failure.value.uncertain is False
    finally:
        await service.aclose()


async def test_regular_404_is_not_reclassified_as_protocol_incompatibility():
    service = WorkbenchService(transport=httpx.MockTransport(
        lambda request: httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
    ))
    try:
        with pytest.raises(WorkbenchError) as failure:
            await service.task("missing")
        assert failure.value.code == "NOT_FOUND"
    finally:
        await service.aclose()


@pytest.mark.parametrize("outcome", ["compatible", "incompatible", "network"])
async def test_negotiate_preserves_pending_original_command_and_never_replays_it(outcome):
    writes = []

    async def respond(request):
        if request.method == "POST":
            writes.append(request)
            raise httpx.ReadTimeout("synthetic unknown command", request=request)
        assert request.method == "GET" and request.url.path == PATH
        if outcome == "network":
            raise httpx.ConnectError("synthetic disconnected", request=request)
        return httpx.Response(200, json=CAPS if outcome == "compatible" else {})

    service = WorkbenchService(transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(WorkbenchError):
            await service.create_task("original fixture goal", "original-key")
        pending = service._pending
        public_pending = copy.deepcopy(service.pending)
        if outcome == "compatible":
            await service.negotiate()
        else:
            with pytest.raises(WorkbenchError) as failure:
                await service.negotiate()
            assert failure.value.code == (
                "CONNECTION_UNAVAILABLE" if outcome == "network" else "WORKBENCH_INCOMPATIBLE"
            )
            assert failure.value.uncertain is False
        assert service._pending is pending
        assert service.pending == public_pending
        assert len(writes) == 1
        assert writes[0].headers["Idempotency-Key"] == "original-key"
    finally:
        await service.aclose()
