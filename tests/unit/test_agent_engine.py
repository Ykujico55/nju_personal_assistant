from __future__ import annotations

import unittest

from personal_assistant.core.agent.checkpoint import (
    InMemoryCheckpointStore,
    InMemoryObservationStore,
    InMemoryRunRepository,
)
from personal_assistant.core.agent.engine import AgentEngine, DecisionKind, ValidatedDecision
from personal_assistant.core.approvals import ApprovalService, InMemoryApprovalRepository
from personal_assistant.core.context import ContextManager
from personal_assistant.core.tools import ToolGateway, ToolPolicy, ToolRegistry
from personal_assistant.domain import RiskLevel, TaskRun, TaskState, ToolCall, ToolDescriptor


class Planner:
    def __init__(self) -> None:
        self.calls = 0

    async def propose(self, **kwargs):
        del kwargs
        self.calls += 1
        return "call" if self.calls == 1 else "complete"


class Validator:
    async def validate(self, proposal, run):
        if proposal == "complete":
            return ValidatedDecision(DecisionKind.COMPLETE, result={"done": True})
        return ValidatedDecision(
            DecisionKind.CALL_TOOL,
            tool_call=ToolCall(
                tool_id="example.read",
                tool_version="1",
                arguments={},
                task_id=run.task_id,
                workflow_allowed_tools=frozenset({"example.read"}),
            ),
        )


class Executor:
    async def execute(self, descriptor, arguments, context):
        del descriptor, arguments, context
        return {"value": "new evidence"}


class AgentEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_plan_act_observe_reaches_terminal_state(self) -> None:
        run = TaskRun(id="run-1", task_id="task-1", objective="test objective")
        runs = InMemoryRunRepository((run,))
        checkpoints = InMemoryCheckpointStore()
        observations = InMemoryObservationStore()
        registry = ToolRegistry()
        registry.publish(
            (
                ToolDescriptor(
                    id="example.read",
                    version="1",
                    extension_id="example.echo",
                    risk=RiskLevel.READ,
                    input_schema={"type": "object", "additionalProperties": False},
                    output_schema={"type": "object", "additionalProperties": True},
                ),
            )
        )
        gateway = ToolGateway(
            registry=registry,
            policy=ToolPolicy(),
            approvals=ApprovalService(InMemoryApprovalRepository()),
            executor=Executor(),
        )
        engine = AgentEngine(
            runs=runs,
            checkpoints=checkpoints,
            observations=observations,
            context=ContextManager(()),
            planner=Planner(),
            validator=Validator(),
            tools=gateway,
            registry=registry,
        )
        result = await engine.run(run.id)
        self.assertEqual(TaskState.SUCCEEDED, result.state)
        self.assertEqual(2, len(observations.items))


if __name__ == "__main__":
    unittest.main()

