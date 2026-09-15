"""Generate a dependency-free extension skeleton from the public SDK contract."""

from __future__ import annotations

import json
import re
from pathlib import Path

_EXTENSION_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$")


def scaffold_extension(extension_id: str, destination: Path) -> tuple[Path, ...]:
    if not _EXTENSION_ID.fullmatch(extension_id):
        raise ValueError("extension id must be a namespaced lowercase identifier")
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"destination is not empty: {destination}")
    package = re.sub(r"[^a-z0-9_]", "_", extension_id)
    tool_id = f"{extension_id}.ping"
    package_root = destination / "src" / package
    schema_root = destination / "schemas"
    package_root.mkdir(parents=True, exist_ok=True)
    schema_root.mkdir(parents=True, exist_ok=True)

    files: dict[Path, str] = {
        destination / "extension.toml": _manifest(extension_id, package, tool_id),
        destination / "requirements.lock": (
            "# Add fully pinned third-party dependencies. The host installs its trusted SDK.\n"
        ),
        destination / "pyproject.toml": _pyproject(extension_id, package),
        schema_root / "ping-input.json": _json_schema(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            }
        ),
        schema_root / "ping-output.json": _json_schema(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"status": {"type": "string", "const": "ok"}},
                "required": ["status"],
                "additionalProperties": False,
            }
        ),
        package_root / "__init__.py": (
            "from .worker import ExtensionImplementation, create_extension\n\n"
            '__all__ = ["ExtensionImplementation", "create_extension"]\n'
        ),
        package_root / "worker.py": _worker(extension_id, tool_id),
        destination / "README.md": _readme(extension_id),
    }
    for path, content in files.items():
        path.write_text(content, encoding="utf-8", newline="\n")
    return tuple(files)


def _manifest(extension_id: str, package: str, tool_id: str) -> str:
    return f'''manifest_version = "1"
id = "{extension_id}"
name = "{extension_id}"
version = "0.1.0"
core_api = ">=1,<2"
python = ">=3.12,<3.14"
entrypoint = "{package}.worker:create_extension"
dependency_lock = "requirements.lock"
state_schema_version = 1
healthcheck = "system.health"

[[tools]]
id = "{tool_id}"
risk = "READ"
input_schema = "schemas/ping-input.json"
output_schema = "schemas/ping-output.json"

[capabilities]
required = []
optional = []
'''


def _pyproject(extension_id: str, package: str) -> str:
    distribution = extension_id.replace(".", "-").replace("_", "-")
    return f'''[build-system]
requires = ["setuptools>=75", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{distribution}"
version = "0.1.0"
requires-python = ">=3.12,<3.14"
dependencies = ["personal-assistant-extension-sdk==1.0.0"]

[tool.setuptools.packages.find]
where = ["src"]
include = ["{package}*"]
'''


def _worker(extension_id: str, tool_id: str) -> str:
    return f'''from __future__ import annotations

from personal_assistant_sdk import (
    PROTOCOL_VERSION,
    DrainReport,
    ExtensionInfo,
    HealthReport,
    InvocationContext,
    Outcome,
    RiskLevel,
    RuntimeContext,
    ToolDescriptor,
    ToolResult,
)
from personal_assistant_sdk.worker import run_stdio_worker


class ExtensionImplementation:
    def __init__(self) -> None:
        self.runtime: RuntimeContext | None = None

    async def initialize(self, runtime: RuntimeContext) -> ExtensionInfo:
        self.runtime = runtime
        return ExtensionInfo(
            id=runtime.extension_id,
            version=runtime.extension_version,
            protocol_version=PROTOCOL_VERSION,
            slots=("ToolProvider",),
            schema_hash=runtime.manifest_schema_hash,
        )

    async def health(self) -> HealthReport:
        return HealthReport(self.runtime is not None, "ready")

    async def drain(self, deadline: float) -> DrainReport:
        del deadline
        return DrainReport(True, 0)

    async def shutdown(self) -> None:
        self.runtime = None

    def tools(self) -> tuple[ToolDescriptor, ...]:
        return (
            ToolDescriptor(
                id="{tool_id}",
                risk=RiskLevel.READ,
                input_schema={{"type": "object", "additionalProperties": False}},
                output_schema={{
                    "type": "object",
                    "properties": {{"status": {{"type": "string", "const": "ok"}}}},
                    "required": ["status"],
                    "additionalProperties": False,
                }},
            ),
        )

    async def invoke(
        self, tool_id: str, arguments: dict, context: InvocationContext
    ) -> ToolResult:
        del arguments, context
        if tool_id != "{tool_id}":
            raise ValueError("unknown tool")
        return ToolResult(Outcome.SUCCEEDED, {{"status": "ok"}})


def create_extension() -> ExtensionImplementation:
    return ExtensionImplementation()


if __name__ == "__main__":
    run_stdio_worker(create_extension)
'''


def _readme(extension_id: str) -> str:
    return f'''# {extension_id}

由 `assistantctl extension scaffold` 生成。只依赖公开 SDK；不得导入宿主 core。

在实现真实能力前：更新 Manifest 风险与 Schema，添加契约测试。
所有副作用仍须由宿主 Tool Gateway 管理。
'''


def _json_schema(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"
