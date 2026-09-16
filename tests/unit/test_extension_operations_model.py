"""F02: operation model validation and collision-free data namespaces."""

from __future__ import annotations

import unittest

from personal_assistant.core.extensions.models import data_namespace
from personal_assistant.core.extensions.operations import (
    DIAGNOSTIC_CODES,
    ExtensionOperation,
    OperationState,
)
from personal_assistant.infrastructure.memory.operations import (
    InMemoryExtensionOperationStore,
)


class DataNamespaceTests(unittest.TestCase):
    def test_similar_extension_ids_map_to_distinct_namespaces(self) -> None:
        namespaces = {
            data_namespace("a.b"),
            data_namespace("a_b"),
            data_namespace("a-b"),
            data_namespace("ab"),
        }
        self.assertEqual(4, len(namespaces))
        for namespace in namespaces:
            self.assertTrue(namespace.startswith("ext_"))
            self.assertRegex(namespace, r"^[a-z0-9_]+$")

    def test_long_extension_ids_stay_within_the_postgres_identifier_limit(self) -> None:
        namespace = data_namespace("x" * 200 + "." + "y" * 200)
        self.assertLessEqual(len(namespace), 63)
        self.assertNotEqual(namespace, data_namespace("x" * 200 + "." + "z" * 200))

    def test_namespace_is_deterministic(self) -> None:
        self.assertEqual(data_namespace("example.echo"), data_namespace("example.echo"))


class OperationModelTests(unittest.IsolatedAsyncioTestCase):
    def test_unknown_diagnostic_text_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ExtensionOperation(
                id="op",
                extension_id="example.echo",
                operation="enable",
                status=OperationState.FAILED,
                diagnostic_code="secret-token-leaked-into-diagnostics",
            )

    def test_unknown_operation_kind_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ExtensionOperation(
                id="op",
                extension_id="example.echo",
                operation="delete-everything",
                status=OperationState.PENDING,
            )

    async def test_stores_enforce_the_diagnostic_allowlist(self) -> None:
        store = InMemoryExtensionOperationStore()
        operation = ExtensionOperation(
            id="op",
            extension_id="example.echo",
            operation="enable",
            status=OperationState.PENDING,
        )
        await store.create(operation)
        with self.assertRaises(ValueError):
            await store.update(
                "op",
                status=OperationState.FAILED,
                diagnostic_code="raw traceback text",
            )
        stored = await store.get("op")
        assert stored is not None
        self.assertEqual(OperationState.PENDING, stored.status)
        self.assertIsNone(stored.diagnostic_code)

    async def test_idempotency_key_uniqueness_is_enforced(self) -> None:
        store = InMemoryExtensionOperationStore()
        first = ExtensionOperation(
            id="op-1",
            extension_id="example.echo",
            operation="enable",
            status=OperationState.PENDING,
            idempotency_key="k",
            command_fingerprint="a" * 64,
            request_scope="admin:extensions:example.echo:enable",
        )
        await store.create(first)
        replay = await store.find_by_request_scope(
            "admin:extensions:example.echo:enable", "k"
        )
        assert replay is not None
        self.assertEqual("op-1", replay.id)
        duplicate = ExtensionOperation(
            id="op-2",
            extension_id="example.echo",
            operation="enable",
            status=OperationState.PENDING,
            idempotency_key="k",
            command_fingerprint="b" * 64,
            request_scope="admin:extensions:example.echo:enable",
        )
        from personal_assistant.core.extensions.errors import ExtensionOperationError

        with self.assertRaises(ExtensionOperationError) as captured:
            await store.create(duplicate)
        self.assertEqual("IDEMPOTENCY_CONFLICT", captured.exception.code)
        self.assertIn("OPERATION_FAILED", DIAGNOSTIC_CODES)

    async def test_interrupt_running_rejects_arbitrary_diagnostics(self) -> None:
        store = InMemoryExtensionOperationStore()
        operation = ExtensionOperation(
            id="op",
            extension_id="example.echo",
            operation="install",
            status=OperationState.RUNNING,
        )
        await store.create(operation)
        with self.assertRaises(ValueError):
            await store.interrupt_running(diagnostic_code="raw database text")
        stored = await store.get("op")
        assert stored is not None
        self.assertEqual(OperationState.RUNNING, stored.status)
        self.assertIsNone(stored.diagnostic_code)


if __name__ == "__main__":
    unittest.main()
