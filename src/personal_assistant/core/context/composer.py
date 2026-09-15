"""Deterministic context packing with hard separation of instructions and data."""

from __future__ import annotations

from collections.abc import Sequence

from personal_assistant.domain import ContextLayer

from .models import ComposedContext, ContextRequest, ContextSection, Evidence

_OPEN_DATA = "<UNTRUSTED_DATA>"
_CLOSE_DATA = "</UNTRUSTED_DATA>"


def _escape_delimiters(value: str) -> str:
    return value.replace(_OPEN_DATA, "[ESCAPED_OPEN_DATA]").replace(
        _CLOSE_DATA, "[ESCAPED_CLOSE_DATA]"
    )


def _wrap_untrusted(value: str, max_chars: int) -> str:
    overhead = len(_OPEN_DATA) + len(_CLOSE_DATA) + 2
    if max_chars <= overhead:
        return ""
    safe = _escape_delimiters(value)[: max_chars - overhead]
    return f"{_OPEN_DATA}\n{safe}\n{_CLOSE_DATA}"


class ContextComposer:
    def compose(
        self,
        request: ContextRequest,
        *,
        evidence: Sequence[Evidence],
        policy: Sequence[str] = (),
        workflow: Sequence[str] = (),
        approved_rules: Sequence[str] = (),
        working_set: Sequence[str] = (),
        episodic_summary: Sequence[str] = (),
        tool_observations: Sequence[str] = (),
        warnings: Sequence[str] = (),
    ) -> ComposedContext:
        remaining = request.max_chars
        sections: list[ContextSection] = []

        def add(layer: ContextLayer, values: Sequence[str], *, untrusted: bool = False) -> None:
            nonlocal remaining
            if not values or remaining <= 0:
                return
            raw = "\n".join(value.strip() for value in values if value.strip())
            if not raw:
                return
            content = _wrap_untrusted(raw, remaining) if untrusted else raw[:remaining]
            if not content:
                return
            sections.append(ContextSection(layer=layer, content=content, untrusted_data=untrusted))
            remaining -= len(content)

        # Stable authority order: lower layers can add data, never override policy.
        add(ContextLayer.POLICY, policy)
        add(ContextLayer.WORKFLOW, workflow)
        add(ContextLayer.APPROVED_RULE, approved_rules)
        add(ContextLayer.TASK_WORKING_SET, working_set)

        selected: list[Evidence] = []
        omitted: list[str] = []
        for item in evidence:
            rendered = (
                f"source={item.source_uri}; locator={dict(item.locator)}; "
                f"hash={item.content_hash}; trust={item.trust.value}\n{item.text}"
            )
            wrapped = _wrap_untrusted(rendered, remaining)
            if not wrapped or len(wrapped) > remaining:
                omitted.append(item.id)
                continue
            sections.append(
                ContextSection(
                    layer=ContextLayer.RETRIEVED_EVIDENCE,
                    content=wrapped,
                    source_ids=(item.id,),
                    untrusted_data=True,
                )
            )
            selected.append(item)
            remaining -= len(wrapped)

        add(ContextLayer.EPISODIC_SUMMARY, episodic_summary, untrusted=True)
        add(ContextLayer.TOOL_OBSERVATION, tool_observations, untrusted=True)
        return ComposedContext(
            sections=tuple(sections),
            evidence=tuple(selected),
            omitted_evidence_ids=tuple(omitted),
            warnings=tuple(warnings),
        )
