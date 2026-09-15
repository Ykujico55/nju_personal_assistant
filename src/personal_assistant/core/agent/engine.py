"""Recoverable Plan/Act/Observe orchestration.

The model proposes; deterministic components validate, transition and execute.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from personal_assistant.core.context import ComposedContext, ContextManager, ContextRequest
from personal_assistant.core.tools import ToolGateway, ToolRegistry
from personal_assistant.domain import TaskRun, TaskState, ToolCall, ToolOutcomeKind

from .checkpoint import (
    CheckpointStorePort,
    Observation,
    ObservationStorePort,
    RunRepositoryPort,
)
from .progress_detector import ProgressDetector
from .safety_fuse import EmergencyFuse
from .state_machine import transition_run


class DecisionKind(StrEnum):
    CALL_TOOL = "CALL_TOOL"
    NEEDS_USER = "NEEDS_USER"
    COMPLETE = "COMPLETE"


@dataclass(frozen=True, slots=True)
class ValidatedDecision:
    kind: DecisionKind
    tool_call: ToolCall | None = None
    message: str | None = None
    result: Any = None

    def __post_init__(self) -> None:
        if self.kind is DecisionKind.CALL_TOOL and self.tool_call is None:
            raise ValueError("CALL_TOOL requires tool_call")
        if self.kind is not DecisionKind.CALL_TOOL and self.tool_call is not None:
            raise ValueError("only CALL_TOOL may carry tool_call")


class PlannerPort(Protocol):
    async def propose(
        self,
        *,
        objective: str,
        context: ComposedContext,
        allowed_tools: tuple[object, ...],
    ) -> Any: ...


class DecisionValidatorPort(Protocol):
    async def validate(self, proposal: Any, run: TaskRun) -> ValidatedDecision: ...


class AgentEngine:
    def __init__(
        self,
        *,
        runs: RunRepositoryPort,
        checkpoints: CheckpointStorePort,
        observations: ObservationStorePort,
        context: ContextManager,
        planner: PlannerPort,
        validator: DecisionValidatorPort,
        tools: ToolGateway,
        registry: ToolRegistry,
        fuse: EmergencyFuse | None = None,
        progress: ProgressDetector | None = None,
    ) -> None:
        self._runs = runs
        self._checkpoints = checkpoints
        self._observations = observations
        self._context = context
        self._planner = planner
        self._validator = validator
        self._tools = tools
        self._registry = registry
        self._fuse = fuse or EmergencyFuse()
        self._progress = progress or ProgressDetector()

    async def run(self, run_id: str) -> TaskRun:
        run = await self._runs.get(run_id)
        if run.state is not TaskState.RUNNING:
            return run

        while run.state is TaskState.RUNNING:
            reason = self._fuse.pause_reason(run)
            if reason is not None:
                run = await self._transition(run, TaskState.PAUSED_SAFETY, reason=reason)
                break

            snapshot = await self._checkpoints.load(run.id)
            composed = await self._context.compose(
                ContextRequest(task_id=run.task_id, purpose="agent.run", query=run.objective),
                working_set=(run.objective, str(snapshot)),
            )
            proposal = await self._planner.propose(
                objective=run.objective,
                context=composed,
                allowed_tools=tuple(self._registry.snapshot().descriptors()),
            )
            decision = await self._validator.validate(proposal, run)

            if decision.kind is DecisionKind.NEEDS_USER:
                run = await self._transition(
                    run,
                    TaskState.WAITING_USER,
                    reason=decision.message or "USER_INPUT_REQUIRED",
                )
                break
            if decision.kind is DecisionKind.COMPLETE:
                await self._observations.append(Observation(run.id, decision.result))
                run = await self._transition(run, TaskState.SUCCEEDED)
                break

            assert decision.tool_call is not None
            outcome = await self._tools.invoke(decision.tool_call)
            waiting = {
                ToolOutcomeKind.APPROVAL_REQUIRED: TaskState.WAITING_APPROVAL,
                ToolOutcomeKind.USER_ACTION_REQUIRED: TaskState.WAITING_USER,
                ToolOutcomeKind.EXTENSION_UNAVAILABLE: TaskState.PAUSED_EXTENSION,
                ToolOutcomeKind.OUTCOME_UNKNOWN: TaskState.WAITING_RECONCILIATION,
                ToolOutcomeKind.SAFETY_PAUSE: TaskState.PAUSED_SAFETY,
            }.get(outcome.kind)
            if waiting is not None:
                reference = outcome.approval_id or outcome.reference_id or outcome.extension_id
                run = await self._transition(
                    run,
                    waiting,
                    reference=reference,
                    reason=outcome.message,
                )
                break

            await self._observations.append(Observation(run.id, outcome.result))
            previous_version = run.version
            run = self._progress.record(run, decision.tool_call, outcome)
            await self._runs.save(run, expected_version=previous_version)
            await self._checkpoints.save(
                run,
                {"last_outcome": outcome.kind.value, "last_reference": outcome.reference_id},
            )
        return run

    async def _transition(
        self,
        run: TaskRun,
        state: TaskState,
        *,
        reference: str | None = None,
        reason: str | None = None,
    ) -> TaskRun:
        previous_version = run.version
        updated = transition_run(run, state, reference=reference, reason=reason)
        await self._runs.save(updated, expected_version=previous_version)
        await self._checkpoints.save(
            updated,
            {"state": updated.state.value, "reference": reference, "reason": reason},
        )
        return updated

