from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TaskCreate(BaseModel):
    objective: str = Field(min_length=1, max_length=10_000)


class TaskMutation(BaseModel):
    version: int = Field(ge=0)


class TaskMessageCreate(TaskMutation):
    content: str = Field(min_length=1, max_length=100_000)


class TaskView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    objective: str
    state: str
    version: int
    created_at: datetime


class MessageView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    actor: str
    content: str
    created_at: datetime


class TaskDetail(BaseModel):
    task: TaskView
    messages: list[MessageView]


class ApprovalDecision(BaseModel):
    nonce: str = Field(min_length=1, max_length=512)


class RejectDecision(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class ApprovalView(BaseModel):
    id: str
    state: str
    action: dict[str, Any]
    nonce: str | None
    expires_at: datetime
    version: int
