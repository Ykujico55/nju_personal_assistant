"""Authenticated PWA device subscription control plane."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from personal_assistant.api.dependencies import get_container
from personal_assistant.api.errors import ApplicationError
from personal_assistant.bootstrap import Container
from personal_assistant.core.notifications import PushUnavailableError

router = APIRouter(prefix="/push", tags=["push"])


class PushKeys(BaseModel):
    model_config = ConfigDict(extra="forbid")

    p256dh: str = Field(min_length=1, max_length=100)
    auth: str = Field(min_length=1, max_length=32)


class PushSubscriptionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(min_length=1, max_length=2048)
    keys: PushKeys


class PushConfig(BaseModel):
    enabled: bool
    public_key: str | None


class PushSubscriptionReceipt(BaseModel):
    id: str
    created_at: datetime


class PushSubscriptionStatus(BaseModel):
    id: str
    active: bool
    reconfigure_required: bool


@router.get("/config", response_model=PushConfig)
async def get_config(
    response: Response, container: Container = Depends(get_container)
) -> PushConfig:
    response.headers["Cache-Control"] = "no-store"
    key = await container.push.configured_public_key()
    return PushConfig(enabled=key is not None, public_key=key)


@router.post(
    "/subscriptions", response_model=PushSubscriptionReceipt,
    status_code=status.HTTP_201_CREATED,
)
async def subscribe(
    payload: PushSubscriptionCreate,
    request: Request,
    response: Response,
    container: Container = Depends(get_container),
) -> PushSubscriptionReceipt:
    response.headers["Cache-Control"] = "no-store"
    try:
        saved = await container.push.subscribe(
            payload.endpoint, payload.keys.p256dh, payload.keys.auth,
            key=request.state.idempotency_key,
        )
    except PushUnavailableError as exc:
        raise ApplicationError(
            "PUSH_UNAVAILABLE", "Web Push host credential is unavailable", 503
        ) from exc
    return PushSubscriptionReceipt(id=saved.id, created_at=saved.created_at)


@router.get("/subscriptions/{subscription_id}", response_model=PushSubscriptionStatus)
async def get_subscription(
    subscription_id: str,
    response: Response,
    container: Container = Depends(get_container),
) -> PushSubscriptionStatus:
    response.headers["Cache-Control"] = "no-store"
    health = await container.push.inspect(subscription_id)
    return PushSubscriptionStatus(
        id=subscription_id, active=health.active,
        reconfigure_required=health.reconfigure_required,
    )


@router.delete("/subscriptions/{subscription_id}", status_code=204)
async def revoke(
    subscription_id: str,
    response: Response,
    container: Container = Depends(get_container),
) -> None:
    response.headers["Cache-Control"] = "no-store"
    await container.push.revoke(subscription_id)
