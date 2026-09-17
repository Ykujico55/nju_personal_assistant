from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from personal_assistant.core.models import ContextField, DataClassification, ModelRequest


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


class DisclosureFieldInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    value: str = Field(max_length=200_000)
    classification: Literal["PUBLIC", "PERSONAL", "SENSITIVE", "SECRET"]
    source: str = Field(min_length=1, max_length=500)


class DisclosureRequest(BaseModel):
    provider_id: str = Field(min_length=1, max_length=64)
    purpose: str = Field(min_length=1, max_length=128)
    instruction: str = Field(default="", max_length=10_000)
    fields: list[DisclosureFieldInput] = Field(min_length=1, max_length=200)
    ttl_seconds: int = Field(default=3600, ge=60, le=604_800)

    def to_model_request(self) -> ModelRequest:
        return ModelRequest(
            purpose=self.purpose,
            instruction=self.instruction,
            fields=tuple(
                ContextField(
                    name=field.name,
                    value=field.value,
                    classification=DataClassification(field.classification),
                    source=field.source,
                )
                for field in self.fields
            ),
        )


class DisclosureConfirmRequest(DisclosureRequest):
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class DisclosureMutation(BaseModel):
    version: int = Field(ge=0)


class DisclosureFieldPreviewView(BaseModel):
    name: str
    classification: str
    source: str
    value_sha256: str
    value_length: int
    redacted_preview: str


class DisclosureRecipientView(BaseModel):
    provider_id: str
    adapter: str
    endpoint: str
    model_id: str
    fingerprint: str


class DisclosurePreviewView(BaseModel):
    provider_id: str
    purpose: str
    policy_version: str
    field_digest: str
    recipient_fingerprint: str
    recipient: DisclosureRecipientView
    fields: list[DisclosureFieldPreviewView]
    protected_field_count: int
    ttl_seconds: int
    suggested_expires_at: datetime
    preview_hash: str


class DisclosureConsentView(BaseModel):
    id: str
    state: str
    provider_id: str
    purpose: str
    field_digest: str
    recipient_fingerprint: str
    field_count: int
    policy_version: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    version: int
