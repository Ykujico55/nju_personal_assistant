"""Bridge enabled extension manifests into domain tool descriptors.

Only manifests already published in a lifecycle/registry snapshot are
converted; this module does not import extension code.  The declared
``capabilities`` list becomes ``required_capabilities`` so the Tool Gateway and
the routing executor can enforce per-tool host grants (for example
``mail.send``).
"""

from __future__ import annotations

import json
from pathlib import Path

from personal_assistant.domain.enums import RiskLevel
from personal_assistant.domain.models import ToolDescriptor

from .manifest import ExtensionManifest


def manifest_tool_descriptors(
    manifest: ExtensionManifest,
) -> tuple[ToolDescriptor, ...]:
    descriptors: list[ToolDescriptor] = []
    for tool in manifest.tools:
        descriptors.append(
            ToolDescriptor(
                id=tool.id,
                version=manifest.version,
                extension_id=manifest.id,
                extension_version=manifest.version,
                input_schema=_schema(manifest.root, tool.input_schema),
                output_schema=_schema(manifest.root, tool.output_schema),
                risk=RiskLevel(tool.risk),
                required_capabilities=frozenset(tool.capabilities),
            )
        )
    return tuple(descriptors)


def _schema(root: Path, reference: str) -> dict[str, object]:
    data = json.loads((root / reference).read_text("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{reference} must contain a JSON object")
    return data


__all__ = ["manifest_tool_descriptors"]
