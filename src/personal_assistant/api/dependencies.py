from __future__ import annotations

from typing import cast

from fastapi import Request

from personal_assistant.bootstrap import Container


def get_container(request: Request) -> Container:
    return cast(Container, request.app.state.container)


def get_actor(request: Request) -> str:
    return getattr(request.state, "actor_id", "development-owner")
