"""Data-only workflow provider; never invokes mail or browser capabilities."""

from __future__ import annotations

import json
from pathlib import Path

from personal_assistant_sdk import (
    PROTOCOL_VERSION,
    DrainReport,
    ExtensionInfo,
    HealthReport,
    RuntimeContext,
    WorkflowDefinition,
)
from personal_assistant_sdk.worker import run_stdio_worker


class WorkflowExtension:
    def __init__(self) -> None:
        self.ready = False

    async def initialize(self, runtime: RuntimeContext) -> ExtensionInfo:
        self.ready = True
        return ExtensionInfo(
            id=runtime.extension_id,
            version=runtime.extension_version,
            protocol_version=PROTOCOL_VERSION,
            slots=("WorkflowProvider",),
            schema_hash=runtime.manifest_schema_hash,
        )

    async def health(self) -> HealthReport:
        return HealthReport(healthy=self.ready, status="ready" if self.ready else "stopped")

    async def drain(self, deadline: float) -> DrainReport:
        self.ready = False
        return DrainReport(drained=True, active_calls=0)

    async def shutdown(self) -> None:
        self.ready = False

    def workflows(self) -> tuple[WorkflowDefinition, ...]:
        result = []
        for path in sorted((Path(__file__).parent / "data").glob("*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            result.append(
                WorkflowDefinition(
                    id=value["id"], version=value["version"], steps=tuple(value["steps"])
                )
            )
        return tuple(result)


def create_extension() -> WorkflowExtension:
    return WorkflowExtension()


if __name__ == "__main__":
    run_stdio_worker(create_extension)
