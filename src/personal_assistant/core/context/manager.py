from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol

from .composer import ContextComposer
from .models import ComposedContext, ContextRequest, Evidence, EvidenceState


class ContextProviderPort(Protocol):
    @property
    def provider_id(self) -> str: ...

    async def retrieve(self, request: ContextRequest) -> Sequence[Evidence]: ...


class ContextManager:
    def __init__(
        self,
        providers: Sequence[ContextProviderPort],
        *,
        composer: ContextComposer | None = None,
    ) -> None:
        self._providers = tuple(providers)
        self._composer = composer or ContextComposer()

    async def compose(
        self,
        request: ContextRequest,
        *,
        policy: Sequence[str] = (),
        workflow: Sequence[str] = (),
        approved_rules: Sequence[str] = (),
        working_set: Sequence[str] = (),
        episodic_summary: Sequence[str] = (),
        tool_observations: Sequence[str] = (),
    ) -> ComposedContext:
        results = await asyncio.gather(
            *(provider.retrieve(request) for provider in self._providers),
            return_exceptions=True,
        )
        warnings: list[str] = []
        by_identity: dict[tuple[str, str, str], Evidence] = {}
        for provider, result in zip(self._providers, results, strict=True):
            if isinstance(result, BaseException):
                warnings.append(f"context provider unavailable: {provider.provider_id}")
                continue
            for item in result:
                if item.state is not EvidenceState.CURRENT:
                    continue
                if item.sensitivity not in request.allowed_sensitivity:
                    continue
                identity = (item.source_uri, item.source_version, item.content_hash)
                by_identity.setdefault(identity, item)
        return self._composer.compose(
            request,
            evidence=tuple(by_identity.values()),
            policy=policy,
            workflow=workflow,
            approved_rules=approved_rules,
            working_set=working_set,
            episodic_summary=episodic_summary,
            tool_observations=tool_observations,
            warnings=warnings,
        )

