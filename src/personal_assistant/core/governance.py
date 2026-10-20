"""Corrections become candidates; activation requires scoped positive/negative tests."""

from __future__ import annotations

import re
from typing import Any, Protocol
from uuid import uuid4

from personal_assistant.core.records import RecordStore, fingerprint
from personal_assistant.domain import ConcurrentModificationError, NotFoundError, ValidationError

_SCOPE = {"workflow_id", "extension_id", "category"}
_PREFERENCES = {"language", "ask_for_missing_material", "answer_style"}


def validate_rule(scope: dict[str, Any], preferences: dict[str, Any]) -> None:
    if not scope or set(scope) - _SCOPE or not preferences or set(preferences) - _PREFERENCES:
        raise ValidationError("rule requires a limited scope and supported preferences")
    if any(
        not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", v)
        for v in scope.values()
    ):
        raise ValidationError("rule scopes contain metadata identifiers only")
    if "language" in preferences and preferences["language"] not in {"zh", "en"}:
        raise ValidationError("unsupported language preference")
    if "answer_style" in preferences and preferences["answer_style"] not in {"brief", "detailed"}:
        raise ValidationError("unsupported answer style")
    if (
        "ask_for_missing_material" in preferences
        and type(preferences["ask_for_missing_material"]) is not bool
    ):
        raise ValidationError("clarification preference must be boolean")


class RuleHistory(Protocol):
    async def record(self, rule: dict[str, Any]) -> str: ...
    async def read(self, revision: str, rule_id: str) -> dict[str, Any]: ...
    async def versions(self, rule_id: str) -> list[str]: ...


class RuleService:
    def __init__(self, records: RecordStore, *, history: RuleHistory | None = None) -> None:
        self.records, self.history = records, history

    async def propose(
        self, *, scope: dict[str, Any], preferences: dict[str, Any], key: str
    ) -> dict[str, Any]:
        validate_rule(scope, preferences)

        async def apply() -> dict[str, Any]:
            return await self.records.put(
                "rules",
                "rule_" + uuid4().hex,
                {"state": "CANDIDATE", "scope": scope, "preferences": preferences},
                expected_version=0,
            )

        return await self.records.command(
            key, {"operation": "propose", "scope": scope, "preferences": preferences}, apply
        )

    async def activate(
        self, rule_id: str, *, version: int, tests: list[dict[str, Any]], key: str
    ) -> dict[str, Any]:
        async def apply() -> dict[str, Any]:
            old = await self.records.get("rules", rule_id)
            if old is None:
                raise NotFoundError("rule is not available")
            if old["version"] != version:
                raise ConcurrentModificationError("rule changed; review the new version")
            value = old["value"]
            validate_rule(value["scope"], value["preferences"])
            if not 2 <= len(tests) <= 32:
                raise ValidationError("positive and negative tests are required")
            results: set[bool] = set()
            for test in tests:
                if (
                    set(test) != {"metadata", "matches"}
                    or type(test["matches"]) is not bool
                    or not isinstance(test["metadata"], dict)
                    or set(test["metadata"]) - _SCOPE
                ):
                    raise ValidationError(
                        "rule tests contain metadata and an expected boolean only"
                    )
                actual = all(test["metadata"].get(k) == v for k, v in value["scope"].items())
                if actual is not test["matches"]:
                    raise ValidationError("a rule counterexample failed")
                results.add(actual)
            if results != {False, True}:
                raise ValidationError("both matching and nonmatching examples are required")
            updated = {
                **value,
                "state": "ACTIVE",
                "tests": tests,
                "tested_digest": fingerprint(value),
            }
            if self.history is not None:
                updated["revision"] = await self.history.record(
                    {"id": rule_id, "version": version + 1, "value": updated}
                )
            return await self.records.put("rules", rule_id, updated, expected_version=version)

        return await self.records.command(
            key, {"operation": "activate", "id": rule_id, "version": version, "tests": tests}, apply
        )

    async def disable(self, rule_id: str, *, version: int, key: str) -> dict[str, Any]:
        async def apply() -> dict[str, Any]:
            old = await self.records.get("rules", rule_id)
            if old is None:
                raise NotFoundError("rule is not available")
            return await self.records.put(
                "rules", rule_id, {**old["value"], "state": "DISABLED"}, expected_version=version
            )

        return await self.records.command(
            key, {"operation": "disable", "id": rule_id, "version": version}, apply
        )

    async def active(self) -> tuple[dict[str, Any], ...]:
        return tuple(r for r in await self.records.list("rules") if r["value"]["state"] == "ACTIVE")

    async def snapshot(self, metadata: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        return tuple(
            r
            for r in await self.active()
            if all(metadata.get(k) == v for k, v in r["value"]["scope"].items())
        )

    async def rollback(
        self, rule_id: str, *, revision: str, version: int, key: str
    ) -> dict[str, Any]:
        history = self.history
        if history is None:
            raise ValidationError("rule history is unavailable")

        async def apply() -> dict[str, Any]:
            old = await self.records.get("rules", rule_id)
            if old is None:
                raise NotFoundError("rule is not available")
            if old["version"] != version:
                raise ConcurrentModificationError("rule changed; review the new version")
            historical = await history.read(revision, rule_id)
            value = {**historical["value"], "state": "CANDIDATE", "rollback_from": revision}
            value["revision"] = await history.record(
                {"id": rule_id, "version": version + 1, "value": value}
            )
            return await self.records.put("rules", rule_id, value, expected_version=version)

        return await self.records.command(
            key,
            {"operation": "rollback", "id": rule_id, "revision": revision, "version": version},
            apply,
        )
