from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status

from personal_assistant.api.dependencies import get_actor, get_container
from personal_assistant.bootstrap import Container
from personal_assistant.domain import Task

from .schemas import (
    MessageView,
    TaskCreate,
    TaskDetail,
    TaskMessageCreate,
    TaskMutation,
    TaskPage,
    TaskView,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _task_view(task: Task) -> TaskView:
    return TaskView(
        id=task.id,
        objective=task.objective,
        state=task.state.value,
        version=task.version,
        created_at=task.created_at,
    )


@router.get("", response_model=TaskPage)
async def list_tasks(
    limit: int = Query(default=20, ge=1, le=100),
    before: str | None = Query(default=None, min_length=1, max_length=200),
    container: Container = Depends(get_container),
) -> TaskPage:
    tasks = await container.tasks.list_recent(limit=limit + 1, before=before)
    items = tasks[:limit]
    return TaskPage(
        items=[_task_view(task) for task in items],
        next_before=items[-1].id if len(tasks) > limit else None,
    )


@router.post("", response_model=TaskView, status_code=status.HTTP_202_ACCEPTED)
async def create_task(
    payload: TaskCreate,
    request: Request,
    container: Container = Depends(get_container),
    actor: str = Depends(get_actor),
) -> TaskView:
    task = await container.tasks.create(
        objective=payload.objective,
        idempotency_key=request.state.idempotency_key,
        actor=actor,
    )
    return _task_view(task)


@router.get("/{task_id}", response_model=TaskDetail)
async def get_task(
    task_id: str,
    container: Container = Depends(get_container),
) -> TaskDetail:
    task = await container.tasks.get(task_id)
    messages = await container.tasks.messages(task_id)
    return TaskDetail(
        task=_task_view(task),
        messages=[MessageView.model_validate(message) for message in messages],
    )


@router.post("/{task_id}/messages", response_model=TaskDetail)
async def add_message(
    task_id: str,
    payload: TaskMessageCreate,
    request: Request,
    container: Container = Depends(get_container),
    actor: str = Depends(get_actor),
) -> TaskDetail:
    task, message = await container.tasks.add_message(
        task_id=task_id,
        content=payload.content,
        expected_version=payload.version,
        actor=actor,
        idempotency_key=request.state.idempotency_key,
    )
    messages = await container.tasks.messages(task_id)
    if not messages:
        messages = (message,)
    return TaskDetail(
        task=_task_view(task),
        messages=[MessageView.model_validate(item) for item in messages],
    )


@router.post("/{task_id}/cancel", response_model=TaskView)
async def cancel_task(
    task_id: str,
    payload: TaskMutation,
    request: Request,
    container: Container = Depends(get_container),
    actor: str = Depends(get_actor),
) -> TaskView:
    task = await container.tasks.cancel(
        task_id=task_id,
        expected_version=payload.version,
        actor=actor,
        idempotency_key=request.state.idempotency_key,
    )
    return _task_view(task)
