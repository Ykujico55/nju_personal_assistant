"""Durable task dispatcher. Interrupted tools require review; jobs never replay R2."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict, replace
from typing import Any
from uuid import uuid4

from personal_assistant.core.agent.checkpoint import CheckpointStorePort, RunRepositoryPort
from personal_assistant.core.approvals import ApprovalService
from personal_assistant.core.jobs import JobQueuePort, LeaseKeepalive
from personal_assistant.core.jobs.queue import JobState, LeaseConflict
from personal_assistant.core.models.router import DisclosureDenied
from personal_assistant.core.records import RecordStore, fingerprint
from personal_assistant.core.tasks import TaskService
from personal_assistant.core.tools import ToolGateway, ToolRegistry
from personal_assistant.core.unit_of_work import UnitOfWorkPort
from personal_assistant.core.workflows import WorkflowBindingConflict
from personal_assistant.domain import (
    ApprovalState,
    ConcurrentModificationError,
    NotFoundError,
    RiskLevel,
    TaskRun,
    TaskState,
    ToolCall,
    ToolOutcome,
    ToolOutcomeKind,
    ValidationError,
)

from .decisions import DecisionValidator
from .engine import DecisionKind
from .planner import StructuredPlanner, plain
from .progress_detector import ProgressDetector
from .safety_fuse import EmergencyFuse
from .state_machine import resume_as_new_segment, transition_run

Preparer = Callable[[ToolCall], Awaitable[ToolCall]]


class TaskRuntime:
    def __init__(
        self,
        *,
        tasks: TaskService,
        jobs: JobQueuePort,
        runs: RunRepositoryPort,
        checkpoints: CheckpointStorePort,
        records: RecordStore,
        tools: ToolGateway,
        registry: ToolRegistry,
        planner: StructuredPlanner,
        approvals: ApprovalService,
        grants: frozenset[str] | Mapping[str, frozenset[str]] = frozenset(),
        prepare: Preparer | None = None,
        paused_probe: Callable[[], bool] | None = None,
        unit_of_work: UnitOfWorkPort | None = None,
        poll_job: Callable[[dict[str, Any], str], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self.tasks, self.jobs, self.runs, self.checkpoints = tasks, jobs, runs, checkpoints
        self.records, self.tools, self.registry, self.planner = records, tools, registry, planner
        self.approvals, self.grants, self.prepare = approvals, grants, prepare
        self.paused_probe = paused_probe
        self.uow = unit_of_work
        self.poll_job = poll_job
        self.worker_id = "agent-" + uuid4().hex
        self.fuse, self.progress = EmergencyFuse(), ProgressDetector()

    @asynccontextmanager
    async def scope(self) -> AsyncIterator[None]:
        if self.uow is None:
            yield
        else:
            async with self.uow.transaction():
                yield

    async def request(
        self,
        task_id: str,
        *,
        version: int,
        mode: str,
        key: str,
        workflow: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        consent_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        request = {
            "task_id": task_id,
            "version": version,
            "mode": mode,
            "workflow": workflow,
            "inputs": inputs or {},
            "consent_id": consent_id,
            "user_id": user_id,
        }

        async def apply() -> dict[str, Any]:
            task = await self.tasks.get(task_id)
            if type(version) is not int or task.version != version:
                raise ConcurrentModificationError("task version changed; keep local inputs")
            if task.state not in {
                TaskState.QUEUED,
                TaskState.WAITING_USER,
                TaskState.PAUSED_EXTENSION,
                TaskState.PAUSED_SAFETY,
            }:
                raise ValidationError(
                    "task cannot be resumed before its pending action is resolved"
                )
            if mode not in {"agent", "workflow"} or (mode == "workflow" and not workflow):
                raise ValidationError("select a valid execution mode and workflow")
            snapshot = await self.checkpoints.load("run_" + task_id)
            if await self.requires_reconciliation(snapshot):
                raise ValidationError("外部动作结果未决，禁止普通继续；请先核对原动作结果。")
            old = await self.records.get("requests", task_id)
            previous = old["value"] if old else {}
            if mode == "workflow":
                from personal_assistant.core.workflows import validate_workflow

                request["workflow"] = validate_workflow(workflow)
                if previous.get("workflow") == request["workflow"]:
                    request["inputs"] = {**previous.get("inputs", {}), **(inputs or {})}
            self.ensure_workflow_binding(snapshot, request)
            await self.records.put(
                "requests", task_id, request, expected_version=old["version"] if old else 0
            )
            async with self.scope():
                await self.tasks.project(task_id, TaskState.QUEUED)
                if task.state is TaskState.QUEUED:
                    job = await self.jobs.enqueue(
                        kind="agent.start",
                        payload={"task_id": task_id},
                        idempotency_key="task-start:" + task_id,
                    )
                else:
                    job = None
                if job is None or job.state not in {JobState.READY, JobState.LEASED}:
                    job = await self.jobs.enqueue(
                        kind="agent.resume",
                        payload={"task_id": task_id},
                        idempotency_key="resume:" + key,
                    )
            return {"task_id": task_id, "job_id": job.id, "accepted": True}

        return await self.records.command(key, request, apply)

    async def work_once(self) -> bool:
        job = await self.jobs.claim(worker_id=self.worker_id)
        if job is None:
            return False
        async with LeaseKeepalive(self.jobs, job_id=job.id, worker_id=self.worker_id) as lease:
            if job.kind not in {"agent.start", "agent.resume", "agent.approval", "event.poll"}:
                await self.jobs.fail(
                    job_id=job.id,
                    worker_id=self.worker_id,
                    error_code="UNSUPPORTED_JOB",
                    retryable=False,
                )
                return True
            try:
                if job.kind == "event.poll":
                    if self.poll_job is None:
                        raise ValidationError("this worker has no event source grant")
                    result = await self.poll_job(job.payload, job.id)
                    lease.ensure_owned()
                    await self.jobs.complete(job_id=job.id, worker_id=self.worker_id, result=result)
                    return True
                task_id = job.payload.get("task_id")
                if not isinstance(task_id, str):
                    raise ValidationError("job has no task identity")
                await self.advance(
                    task_id,
                    resume=job.kind != "agent.start",
                    approval_id=job.payload.get("approval_id"),
                    owned=lease.ensure_owned,
                )
                lease.ensure_owned()
                await self.jobs.complete(
                    job_id=job.id, worker_id=self.worker_id, result={"task_id": task_id}
                )
            except asyncio.CancelledError:
                # Release makes the job visible; the persisted dispatch marker prevents replay.
                raise
            except LeaseConflict:
                raise
            except WorkflowBindingConflict:
                run = await self.runs.get("run_" + job.payload["task_id"])
                if run.state is TaskState.RUNNING:
                    await self.finish(
                        run,
                        TaskState.WAITING_USER,
                        await self.checkpoints.load(run.id),
                        "工作流定义与原运行不兼容，原检查点已保留；请创建新任务。",
                    )
                await self.jobs.fail(
                    job_id=job.id,
                    worker_id=self.worker_id,
                    error_code="WORKFLOW_BINDING_CONFLICT",
                    retryable=False,
                )
            except ConcurrentModificationError:
                await self.jobs.complete(
                    job_id=job.id, worker_id=self.worker_id, result={"superseded": True}
                )
            except Exception:
                task_id = job.payload.get("task_id")
                if isinstance(task_id, str):
                    run = await self.runs.get("run_" + task_id)
                    if run.state is TaskState.RUNNING:
                        snapshot = await self.checkpoints.load(run.id)
                        state = (
                            TaskState.WAITING_RECONCILIATION
                            if await self.requires_reconciliation(snapshot)
                            else TaskState.WAITING_USER
                        )
                        await self.finish(
                            run,
                            state,
                            snapshot,
                            "执行暂时失败，原检查点已保留；请检查配置后人工继续，外部动作不会自动重试。",
                        )
                await self.jobs.fail(
                    job_id=job.id,
                    worker_id=self.worker_id,
                    error_code="TASK_DISPATCH_FAILED",
                    retryable=False,
                )
                # Existing checkpoints remain authoritative; no automatic retry is scheduled.
        return True

    async def advance(
        self,
        task_id: str,
        *,
        resume: bool = False,
        approval_id: str | None = None,
        owned: Callable[[], None] = lambda: None,
    ) -> None:
        task = await self.tasks.get(task_id)
        if task.state in {TaskState.CANCELLED, TaskState.SUCCEEDED, TaskState.FAILED}:
            return
        run_id = "run_" + task.id
        try:
            run = await self.runs.get(run_id)
        except (KeyError, NotFoundError):
            run = TaskRun(id=run_id, task_id=task.id, objective=task.objective)
            await self.runs.add(run)
        snapshot = await self.checkpoints.load(run_id)
        unresolved = await self.requires_reconciliation(snapshot, run.waiting_reference)
        if unresolved or snapshot.get("phase") == "dispatching":
            state = TaskState.WAITING_RECONCILIATION if unresolved else TaskState.WAITING_USER
            if run.state is TaskState.RUNNING:
                await self.finish(
                    run, state, snapshot, "执行中断，需核对原操作；不会自动重复调用。"
                )
            return
        if run.state is not TaskState.RUNNING:
            if not resume:
                return
            if run.state is TaskState.WAITING_APPROVAL:
                if approval_id != run.waiting_reference:
                    return
                record = await self.approvals.get(approval_id or "")
                if record is None or record.state is not ApprovalState.APPROVED:
                    return
                snapshot["approved_id"] = approval_id
            elif run.state not in {
                TaskState.WAITING_USER,
                TaskState.PAUSED_EXTENSION,
                TaskState.PAUSED_SAFETY,
            }:
                return
            queued = resume_as_new_segment(run)
            await self.runs.save(queued, expected_version=run.version)
            run = transition_run(queued, TaskState.RUNNING)
            await self.runs.save(run, expected_version=queued.version)
        await self.tasks.project(task_id, TaskState.RUNNING)
        request_record = await self.records.get("requests", task_id)
        request: dict[str, Any] = request_record["value"] if request_record else {"mode": "agent"}
        self.ensure_workflow_binding(snapshot, request)
        if "rules" not in snapshot:
            from personal_assistant.core.governance import RuleService

            flow = request.get("workflow") or {}
            metadata = {
                "category": "general",
                "workflow_id": flow.get("id"),
                "extension_id": flow.get("extension_id"),
            }
            snapshot["rules"] = plain(await RuleService(self.records).snapshot(metadata))
        policy = self.planner.policy_identity
        if "model_policy" not in snapshot:
            snapshot["model_policy"] = policy
        elif snapshot["model_policy"] != policy:
            await self.finish(
                run,
                TaskState.WAITING_USER,
                snapshot,
                "本次运行绑定的模型配置已改变；请建立新任务并重新核对披露。",
            )
            return
        current_descriptors = self.registry.snapshot().descriptors()
        pinned = snapshot.get("tools")
        identities = {
            d.id: {
                "version": d.version,
                "extension_version": d.extension_version,
                "grants": sorted(
                    self.grants.get(d.id, frozenset())
                    if isinstance(self.grants, Mapping)
                    else self.grants
                ),
            }
            for d in current_descriptors
        }
        if pinned is None:
            snapshot["tools"] = identities
        elif any(identities.get(key) != identity for key, identity in pinned.items()):
            await self.finish(
                run,
                TaskState.PAUSED_EXTENSION,
                snapshot,
                "运行绑定的扩展或工具版本已改变，请核对后选择新的工作流运行。",
            )
            return
        current_descriptors = tuple(d for d in current_descriptors if d.id in snapshot["tools"])
        validator = DecisionValidator(current_descriptors, grants=self.grants)
        while run.state is TaskState.RUNNING:
            owned()
            if self.paused_probe is not None and self.paused_probe():
                await self.finish(
                    run,
                    TaskState.PAUSED_SAFETY,
                    snapshot,
                    "本机安全开关已冻结执行；解除后仍需明确继续。",
                )
                return
            if (await self.tasks.get(task_id)).state is TaskState.CANCELLED:
                cancelled = transition_run(run, TaskState.CANCELLED)
                await self.runs.save(cancelled, expected_version=run.version)
                return
            reason = self.fuse.pause_reason(run)
            if reason:
                await self.finish(run, TaskState.PAUSED_SAFETY, snapshot, reason)
                return
            try:
                if snapshot.get("approved_id") and snapshot.get("pending_call"):
                    call = self.restore_call(snapshot["pending_call"])
                    decision_kind = DecisionKind.CALL_TOOL
                    call = replace(call, approval_id=snapshot.pop("approved_id"))
                else:
                    if request.get("mode") == "workflow":
                        proposal = await self.workflow_proposal(run, snapshot, request)
                    else:
                        messages = [
                            m.content
                            for m in await self.tasks.messages(task_id)
                            if m.actor != "assistant"
                        ]
                        proposal = await self.planner.propose(
                            objective=run.objective,
                            messages=messages,
                            tools=current_descriptors,
                            observations=snapshot.get("observations", []),
                            consent_id=request.get("consent_id"),
                            rules=snapshot["rules"],
                            user_id=request.get("user_id"),
                        )
                    snapshot.pop("model_disclosure", None)
                    decision = await validator.validate(proposal, run)
                    decision_kind = decision.kind
                    if decision_kind is not DecisionKind.CALL_TOOL:
                        state = (
                            TaskState.SUCCEEDED
                            if decision_kind is DecisionKind.COMPLETE
                            else TaskState.WAITING_USER
                        )
                        snapshot["result"] = plain(decision.result)
                        await self.finish(run, state, snapshot, decision.message or "等待输入")
                        return
                    assert decision.tool_call is not None
                    call = replace(
                        decision.tool_call, idempotency_key=f"run:{run.id}:round:{run.version}"
                    )
                    descriptor = self.registry.snapshot().resolve(call.tool_id, call.tool_version)
                    if descriptor.risk is RiskLevel.EXTERNAL_WRITE:
                        if self.prepare is None:
                            raise ValidationError("host authority preview is unavailable")
                        call = await self.prepare(call)
                owned()
                descriptor = self.registry.snapshot().resolve(call.tool_id, call.tool_version)
                snapshot["phase"] = "dispatching"
                snapshot["risk"] = descriptor.risk.value
                snapshot["pending_call"] = self.serialize_call(call)
                dispatch = replace(run, version=run.version + 1)
                async with self.scope():
                    await self.runs.save(dispatch, expected_version=run.version)
                    await self.checkpoints.save(dispatch, snapshot)
                run = dispatch
                if (await self.tasks.get(task_id)).state is TaskState.CANCELLED:
                    return
                outcome = await self.invoke_until_cancelled(call, descriptor.risk, owned)
                owned()
                snapshot["phase"] = "observed"
                if outcome.kind is ToolOutcomeKind.APPROVAL_REQUIRED:
                    snapshot["pending_approval_id"] = outcome.approval_id
                waiting = {
                    ToolOutcomeKind.APPROVAL_REQUIRED: TaskState.WAITING_APPROVAL,
                    ToolOutcomeKind.USER_ACTION_REQUIRED: TaskState.WAITING_USER,
                    ToolOutcomeKind.EXTENSION_UNAVAILABLE: TaskState.PAUSED_EXTENSION,
                    ToolOutcomeKind.OUTCOME_UNKNOWN: TaskState.WAITING_RECONCILIATION,
                    ToolOutcomeKind.SAFETY_PAUSE: TaskState.PAUSED_SAFETY,
                }.get(outcome.kind)
                if waiting:
                    await self.finish(
                        run,
                        waiting,
                        snapshot,
                        outcome.message or waiting.value,
                        reference=outcome.approval_id or outcome.reference_id,
                    )
                    return
                snapshot.pop("pending_call", None)
                results = snapshot.setdefault("observations", [])
                results.append(plain(outcome.result))
                snapshot["observations"] = results[-16:]
                if request.get("mode") == "workflow" and outcome.kind is ToolOutcomeKind.SUCCESS:
                    snapshot["cursor"] = int(snapshot.get("cursor", 0)) + 1
                    snapshot.setdefault("outputs", {})[snapshot.pop("step_id", "result")] = plain(
                        outcome.result
                    )
                updated = self.progress.record(run, call, outcome)
                async with self.scope():
                    await self.runs.save(updated, expected_version=run.version)
                    await self.checkpoints.save(updated, snapshot)
                run = updated
            except DisclosureDenied:
                if self.planner.pending_disclosure is not None:
                    snapshot["model_disclosure"] = self.planner.pending_disclosure
                await self.finish(
                    run,
                    TaskState.WAITING_USER,
                    snapshot,
                    "模型披露未获许可。材料已保留，请在披露预览中明确授权或选择本地模型。",
                )
                return
            except (ValidationError, NotFoundError):
                await self.finish(
                    run,
                    TaskState.WAITING_USER,
                    snapshot,
                    "提案、材料或能力未通过校验；请补充输入或检查配置后继续。",
                )
                return

    @staticmethod
    def ensure_workflow_binding(snapshot: dict[str, Any], request: dict[str, Any]) -> None:
        pinned = snapshot.get("workflow_digest")
        if pinned is not None and (
            request.get("mode") != "workflow" or fingerprint(request.get("workflow")) != pinned
        ):
            raise WorkflowBindingConflict(
                "workflow is already bound; create a new task to change its definition or mode"
            )

    async def requires_reconciliation(
        self, snapshot: dict[str, Any], approval_reference: str | None = None
    ) -> bool:
        """A lost acknowledgement cannot turn unresolved execution into a fresh plan."""
        if snapshot.get("unresolved_external"):
            return True
        if snapshot.get("risk") not in {
            RiskLevel.EXTERNAL_WRITE.value,
            RiskLevel.EXTERNAL_WRITE.code,
        }:
            return False
        pending = snapshot.get("pending_call")
        approval_id = (
            (pending or {}).get("approval_id")
            or snapshot.get("pending_approval_id")
            or approval_reference
        )
        record = None
        if approval_id:
            try:
                record = await self.approvals.get(approval_id)
            except (KeyError, NotFoundError):
                return True
            if record.state is ApprovalState.FAILED:
                # Durable proof of a definitive failure allows an explicit fresh decision.
                return False
        if snapshot.get("phase") == "dispatching":
            return True
        if not pending:
            return False
        if record is None:
            # Older checkpoints lost the approval id; lack of proof never permits replanning.
            return True
        return record.state in {ApprovalState.EXECUTING, ApprovalState.UNKNOWN}

    async def invoke_until_cancelled(
        self, call: ToolCall, risk: RiskLevel, owned: Callable[[], None]
    ) -> ToolOutcome:
        operation = asyncio.create_task(self.tools.invoke(call))
        try:
            while not operation.done():
                await asyncio.wait({operation}, timeout=0.1)
                if operation.done():
                    break
                owned()
                if (await self.tasks.get(call.task_id)).state is TaskState.CANCELLED or (
                    self.paused_probe is not None and self.paused_probe()
                ):
                    operation.cancel()
                    with suppress(asyncio.CancelledError):
                        await operation
                    if risk is RiskLevel.EXTERNAL_WRITE:
                        return ToolOutcome(
                            ToolOutcomeKind.OUTCOME_UNKNOWN,
                            reference_id="interrupted:" + (call.idempotency_key or call.task_id),
                            message="外部调用已中止，结果需对账；不会自动重试。",
                        )
                    return ToolOutcome(
                        ToolOutcomeKind.SAFETY_PAUSE, message="任务取消或本机冻结已中止调用。"
                    )
            return await operation
        finally:
            if not operation.done():
                operation.cancel()
                with suppress(asyncio.CancelledError):
                    await operation

    async def workflow_proposal(
        self, run: TaskRun, snapshot: dict[str, Any], request: dict[str, Any]
    ) -> dict[str, Any]:
        from personal_assistant.core.workflows import next_proposal

        return next_proposal(request["workflow"], snapshot, request.get("inputs", {}))

    async def finish(
        self,
        run: TaskRun,
        state: TaskState,
        snapshot: dict[str, Any],
        message: str,
        *,
        reference: str | None = None,
    ) -> None:
        updated = transition_run(run, state, reference=reference, reason=message)
        if state is TaskState.WAITING_RECONCILIATION:
            snapshot["unresolved_external"] = True
        snapshot["phase"] = "waiting" if state is not TaskState.SUCCEEDED else "complete"
        async with self.scope():
            await self.runs.save(updated, expected_version=run.version)
            await self.checkpoints.save(updated, snapshot)
            await self.tasks.project(run.task_id, state)
            await self.tasks.assistant_message(
                run.task_id, message, key=f"run-message:{run.id}:{updated.version}"
            )
            old = await self.records.get("executions", run.task_id)
            await self.records.put(
                "executions",
                run.task_id,
                {
                    "run_id": run.id,
                    "state": state.value,
                    "reference": reference,
                    "reason": message,
                    "workflow": snapshot.get("workflow"),
                    "cursor": snapshot.get("cursor"),
                    "outputs": snapshot.get("outputs", {}),
                    "result": snapshot.get("result"),
                },
                expected_version=old["version"] if old else 0,
            )

    @staticmethod
    def serialize_call(call: ToolCall) -> dict[str, Any]:
        return {
            "tool_id": call.tool_id,
            "tool_version": call.tool_version,
            "arguments": plain(call.arguments),
            "task_id": call.task_id,
            "target": plain(call.target),
            "attachments": [asdict(a) for a in call.attachments],
            "granted_capabilities": sorted(call.granted_capabilities),
            "workflow_allowed_tools": sorted(call.workflow_allowed_tools),
            "idempotency_key": call.idempotency_key,
            "form_version": call.form_version,
            "approval_id": call.approval_id,
        }

    @staticmethod
    def restore_call(value: dict[str, Any]) -> ToolCall:
        from personal_assistant.domain import AttachmentDigest

        return ToolCall(
            **{
                **value,
                "attachments": tuple(AttachmentDigest(**a) for a in value["attachments"]),
                "granted_capabilities": frozenset(value["granted_capabilities"]),
                "workflow_allowed_tools": frozenset(value["workflow_allowed_tools"]),
            }
        )
