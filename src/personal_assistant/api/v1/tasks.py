from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status

from personal_assistant.api.dependencies import get_actor, get_container
from personal_assistant.bootstrap import Container
from personal_assistant.domain import NotFoundError, Task

from .schemas import (
    FormDraftCreate,
    FormDraftUpdate,
    FormDraftView,
    MessageView,
    TaskCreate,
    TaskDetail,
    TaskMessageCreate,
    TaskMutation,
    TaskPage,
    TaskView,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{task_id}/form-draft", response_model=FormDraftView)
async def get_form_draft(
    task_id: str,
    container: Container = Depends(get_container),
) -> FormDraftView:
    return FormDraftView.model_validate(await container.form_drafts.get(task_id))


@router.post("/{task_id}/form-draft", response_model=FormDraftView, status_code=201)
async def create_form_draft(
    task_id: str,
    payload: FormDraftCreate,
    request: Request,
    container: Container = Depends(get_container),
) -> FormDraftView:
    replay = await container.form_drafts.replay_create(
        task_id, payload.extension_id, payload.form_id, key=request.state.idempotency_key
    )
    if replay is not None:
        return FormDraftView.model_validate(replay)
    forms = await container.extension_supervisor.list_forms()
    form = next(
        (
            item for item in forms
            if item["extension_id"] == payload.extension_id and item["id"] == payload.form_id
        ),
        None,
    )
    if form is None:
        replay = await container.form_drafts.replay_create(
            task_id, payload.extension_id, payload.form_id, key=request.state.idempotency_key
        )
        if replay is not None:
            return FormDraftView.model_validate(replay)
        raise NotFoundError("form is not available from an enabled extension")
    saved = await container.form_drafts.create(
        task_id, form, key=request.state.idempotency_key
    )
    return FormDraftView.model_validate(saved)


@router.put("/{task_id}/form-draft", response_model=FormDraftView)
async def update_form_draft(
    task_id: str,
    payload: FormDraftUpdate,
    request: Request,
    container: Container = Depends(get_container),
) -> FormDraftView:
    saved = await container.form_drafts.replace(
        task_id, payload.values, version=payload.version, key=request.state.idempotency_key
    )
    return FormDraftView.model_validate(saved)


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
