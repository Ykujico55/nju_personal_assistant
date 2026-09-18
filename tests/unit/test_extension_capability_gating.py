"""F05: manifest capabilities gate the generic host data handler."""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from personal_assistant.core.extensions.config import ExtensionConfigError
from personal_assistant.core.extensions.data_access import (
    CAPABILITY_EXTENSION_DATA,
    ExtensionDataContext,
)
from personal_assistant.core.extensions.errors import ExtensionOperationError
from personal_assistant.core.extensions.manifest import (
    DeclaredCapabilities,
    ManifestParser,
)
from personal_assistant.core.extensions.models import ExtensionRecord, ExtensionState
from personal_assistant.infrastructure.extensions.processes import ProcessRuntimeSupervisor
from personal_assistant.infrastructure.memory.extension_data import (
    UnavailableExtensionDataAccess,
)

ROOT = Path(__file__).resolve().parents[2]
EXTENSION_DIR = ROOT / "extensions" / "personal_knowledge"


class _StubDataAccess:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, Any], ExtensionDataContext]] = []

    @property
    def available(self) -> bool:
        return True

    async def handle(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        context: ExtensionDataContext,
    ) -> Any:
        self.calls.append((method, params, context))
        return {"rows": [], "rowcount": 0}


class _InvalidConfigStore:
    async def get(self, extension_id: str) -> Mapping[str, Any]:
        del extension_id
        return {"include_hidden": "yes"}

    async def save(self, extension_id: str, config: Mapping[str, Any]) -> None:
        del extension_id, config


def _manifest() -> Any:
    return ManifestParser().parse(EXTENSION_DIR)


def _record(manifest: Any) -> ExtensionRecord:
    return ExtensionRecord(
        manifest=manifest,
        artifact_hash="sha256:" + "a" * 64,
        state=ExtensionState.ENABLED,
        install_path=str(EXTENSION_DIR),
    )


class CapabilityGatingTests(unittest.IsolatedAsyncioTestCase):
    def test_manifest_requires_the_data_capability(self) -> None:
        manifest = _manifest()
        self.assertIn(CAPABILITY_EXTENSION_DATA, manifest.capabilities.required)

    async def test_missing_required_capability_refuses_to_start(self) -> None:
        for data_access in (None, UnavailableExtensionDataAccess()):
            runtime = ProcessRuntimeSupervisor(data_access=data_access)
            with self.assertRaises(ExtensionOperationError) as captured:
                await runtime.start(_record(_manifest()))
            self.assertEqual("REQUIRED_CAPABILITY_UNAVAILABLE", captured.exception.code)

    async def test_handler_is_registered_only_when_declared_and_available(self) -> None:
        stub = _StubDataAccess()
        runtime = ProcessRuntimeSupervisor(data_access=stub)
        handler = runtime._host_handler(_record(_manifest()))  # noqa: SLF001
        self.assertIsNotNone(handler)
        result = await handler(  # type: ignore[misc]
            "host.data.execute", {"statement": "SELECT 1"}
        )
        self.assertEqual({"rows": [], "rowcount": 0}, result)
        self.assertEqual(CAPABILITY_EXTENSION_DATA in _manifest().capabilities.required, True)
        self.assertEqual(1, len(stub.calls))
        self.assertEqual("personal.knowledge", stub.calls[0][2].extension_id)

    async def test_undeclared_capability_gets_no_handler(self) -> None:
        stub = _StubDataAccess()
        runtime = ProcessRuntimeSupervisor(data_access=stub)
        manifest = replace(
            _manifest(), capabilities=DeclaredCapabilities(required=(), optional=())
        )
        self.assertIsNone(runtime._host_handler(_record(manifest)))  # noqa: SLF001
        optional_only = replace(
            manifest, capabilities=DeclaredCapabilities(optional=("other.cap",))
        )
        self.assertIsNone(runtime._host_handler(_record(optional_only)))  # noqa: SLF001

    async def test_unavailable_data_access_gets_no_handler(self) -> None:
        runtime = ProcessRuntimeSupervisor(data_access=UnavailableExtensionDataAccess())
        self.assertIsNone(runtime._host_handler(_record(_manifest())))  # noqa: SLF001

    async def test_persisted_nonempty_config_is_revalidated_before_worker_start(self) -> None:
        runtime = ProcessRuntimeSupervisor(
            data_access=_StubDataAccess(), config_store=_InvalidConfigStore()
        )
        try:
            with self.assertRaises(ExtensionConfigError):
                await runtime.start(_record(_manifest()))
            self.assertEqual({}, runtime._workers)  # noqa: SLF001
        finally:
            await runtime.stop_all()


if __name__ == "__main__":
    unittest.main()
