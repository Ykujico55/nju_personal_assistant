"""Conversation API through real runtime/Gateway using synthetic model and executor.

All state is isolated under tmp_path, with no original services, model, or external actions.
"""
from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import patch

import httpx
import pytest

from personal_assistant.admin_app import create_app
from personal_assistant.bootstrap import build_container
from personal_assistant.core.models import ModelOutput, RecipientIdentity
from personal_assistant.core.tools import ToolGateway, ToolPolicy
from personal_assistant.domain import ApprovalState, RiskLevel, TaskState, ToolDescriptor
from personal_assistant.runtime import build_runtime
from personal_assistant.settings import Settings


class SyntheticProvider:
    provider_id = "remote.openai"
    is_remote = True
    recipient = RecipientIdentity(provider_id=provider_id, adapter="synthetic",
        endpoint="https://synthetic.invalid", model_id="fixture")

    def __init__(self, arguments):
        self.arguments, self.calls = arguments, []
        self.plans = 0

    async def complete(self, request):
        self.calls.append(request.purpose)
        if request.purpose == "workbench.conversation.route":
            response = {"kind": "TASK", "query": "", "providers": []}
        else:
            self.plans += 1
            response = ({"kind": "CALL_TOOL", "tool_id": "fixture.action",
                         "tool_version": "1", "arguments": self.arguments}
                        if self.plans == 1 else
                        {"kind": "COMPLETE", "message": "合成只读检查已完成"})
        return ModelOutput(text=json.dumps(response), provider_id=self.provider_id,
                           model_id="fixture", usage={"total_tokens": 7})


class SyntheticExecutor:
    def __init__(self):
        self.calls = []

    async def execute(self, descriptor, arguments, context):
        self.calls.append((descriptor.id, dict(arguments)))
        return {"verified": True}


class SyntheticPreview:
    def __init__(self):
        self.calls = []

    async def invoke_tool(self, extension_id, tool_id, arguments, **kwargs):
        assert tool_id == "fixture.action.preview"
        self.calls.append(kwargs["task_id"])
        return {"outcome": "SUCCEEDED", "output": {
            "kind": "EXTENSION_ACTION", "payload": dict(arguments),
            "target": dict(arguments["target"]), "consequences": arguments["consequences"],
            "attachments": [],
        }}


def completed(response):
    assert response.status_code == 200, response.text
    frames = [frame for frame in response.text.split("\n\n")
              if frame.startswith("event: completed\n")]
    assert len(frames) == 1, response.text
    return json.loads(frames[0].split("data: ", 1)[1])


@pytest.mark.asyncio
@pytest.mark.parametrize("risk", [RiskLevel.READ, RiskLevel.EXTERNAL_WRITE])
async def test_automatic_conversation_queues_real_runtime_and_preserves_approval(tmp_path, risk):
    arguments = ({"local_action_id": "synthetic_action", "target": {"recipient": "nobody.invalid"},
                  "consequences": "synthetic test only", "attachments": []}
                 if risk is RiskLevel.EXTERNAL_WRITE else {})
    provider = SyntheticProvider(arguments)
    executor, preview = SyntheticExecutor(), SyntheticPreview()
    with patch.dict("os.environ", {}, clear=True):
        settings = replace(Settings.from_env(), extension_root=tmp_path / "extensions",
                           artifact_root=tmp_path / "artifacts", model_disclosure_mode="automatic")
    with patch("personal_assistant.bootstrap._build_model_providers", return_value=[provider]):
        container = build_container(settings)
    descriptor = ToolDescriptor(id="fixture.action", version="1", extension_id="fixture",
        extension_version="1", risk=risk, input_schema={"type": "object"},
        output_schema={"type": "object"})
    descriptors = (descriptor,)
    if risk is RiskLevel.EXTERNAL_WRITE:
        descriptors += (replace(descriptor, id="fixture.action.preview", risk=RiskLevel.READ),)
    container.tool_registry.publish(descriptors)
    container.extension_supervisor.invoke_tool = preview.invoke_tool
    container.tool_gateway = ToolGateway(registry=container.tool_registry, policy=ToolPolicy(),
        approvals=container.approvals, executor=executor, outbox=container.side_effect_outbox)
    app = create_app(settings=settings, container=container)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8001",
                headers={"X-Assistant-Workbench": "1"}) as client:
            body = {"message": "请完成合成动作自检"}
            headers = {"Idempotency-Key": "conversation-action"}
            response = await client.post("/admin/v1/workbench/conversation/stream",
                                         json=body, headers=headers)
            receipt = completed(response)
            task_id = receipt["task"]["id"]
            assert receipt["task"]["state"] == "QUEUED"
            assert receipt["tools_executed"] is False and not executor.calls
            assert receipt["usage"] == {"total_tokens": 7}
            runtime = build_runtime(container)
            assert await runtime.work_once()
            state = (await container.tasks.get(task_id)).state
            run = await container.run_repository.get("run_" + task_id)
            if risk is RiskLevel.READ:
                assert state is TaskState.SUCCEEDED and run.state is TaskState.SUCCEEDED
                assert executor.calls == [("fixture.action", {})]
                assert provider.plans == 2 and not preview.calls
            else:
                assert state is TaskState.WAITING_APPROVAL
                assert run.state is TaskState.WAITING_APPROVAL
                approval = await container.approvals.get(run.waiting_reference)
                assert approval.state is ApprovalState.WAITING_APPROVAL
                assert not executor.calls and provider.plans == 1
                assert preview.calls == [task_id]
            # A replay is the original creation receipt, not a new task/model call.
            calls_before = list(provider.calls)
            again = completed(await client.post("/admin/v1/workbench/conversation/stream",
                                               json=body, headers=headers))
            assert again == receipt and provider.calls == calls_before
            assert len(await container.tasks.list_recent(limit=20)) == 1
            assert not await runtime.work_once()
            assert len(executor.calls) == (1 if risk is RiskLevel.READ else 0)
    finally:
        await container.aclose()
