"""F05: generic extension data capability guard and fail-closed dev adapter."""

from __future__ import annotations

import hashlib
import math
import unittest
from pathlib import Path

from personal_assistant.core.extensions.data_access import (
    HOST_DATA_EXECUTE,
    DataAccessError,
    ExtensionDataContext,
    parse_migrations,
    validate_migration_sql,
    validate_parameters,
    validate_statement,
    validate_transaction_items,
)
from personal_assistant.infrastructure.memory.extension_data import (
    UnavailableExtensionDataAccess,
)

NAMESPACE = "ext_org_2e_example_2e_knowledge"


def _reject(statement: str) -> None:
    with unittest.TestCase().assertRaises(DataAccessError):
        validate_statement(statement, namespace=NAMESPACE)


class StatementGuardTests(unittest.TestCase):
    def test_simple_select_is_allowed(self) -> None:
        statement = "SELECT text FROM chunks WHERE source_id = $1"
        self.assertEqual(statement, validate_statement(statement, namespace=NAMESPACE))

    def test_trailing_semicolon_is_stripped(self) -> None:
        self.assertEqual(
            "SELECT 1",
            validate_statement("SELECT 1;  ", namespace=NAMESPACE),
        )

    def test_multiple_statements_are_rejected(self) -> None:
        _reject("SELECT 1; DROP TABLE chunks")

    def test_semicolon_inside_string_literal_is_allowed(self) -> None:
        statement = "SELECT 'a;b' AS value"
        self.assertEqual(statement, validate_statement(statement, namespace=NAMESPACE))

    def test_dollar_quoted_body_with_semicolon_is_allowed(self) -> None:
        statement = "SELECT $tag$a;b$tag$ AS value"
        self.assertEqual(statement, validate_statement(statement, namespace=NAMESPACE))

    def test_unterminated_quote_is_rejected(self) -> None:
        _reject("SELECT 'oops")

    def test_core_schema_is_rejected(self) -> None:
        _reject("SELECT id FROM public.tasks")
        _reject('SELECT id FROM "public".tasks')
        _reject("SELECT * FROM information_schema.tables")
        _reject("SELECT * FROM pg_catalog.pg_class")

    def test_other_extension_namespace_is_rejected(self) -> None:
        _reject("SELECT * FROM ext_other_2e_thing.chunks")

    def test_own_namespace_qualifier_is_allowed(self) -> None:
        statement = f"SELECT * FROM {NAMESPACE}.chunks"
        self.assertEqual(statement, validate_statement(statement, namespace=NAMESPACE))

    def test_dangerous_statements_are_rejected(self) -> None:
        for statement in (
            "COPY chunks TO '/tmp/x'",
            "GRANT ALL ON chunks TO PUBLIC",
            "SET search_path TO public",
            "SELECT pg_read_file('/etc/passwd')",
            "SELECT set_config('search_path', 'public', false)",
            "DO $$ BEGIN NULL; END $$",
            "CREATE ROLE attacker",
            "CREATE SCHEMA attacker",
            "DROP DATABASE postgres",
            "ALTER SYSTEM SET logging_collector = on",
        ):
            _reject(statement)

    def test_ddl_for_own_schema_is_allowed(self) -> None:
        statement = (
            "CREATE TABLE IF NOT EXISTS chunks ("
            "id text PRIMARY KEY, search_vector tsvector, embedding vector)"
        )
        self.assertEqual(statement, validate_statement(statement, namespace=NAMESPACE))

    def test_update_with_set_clause_is_allowed(self) -> None:
        statement = "UPDATE sources SET active_version = $1 WHERE source_id = $2"
        self.assertEqual(statement, validate_statement(statement, namespace=NAMESPACE))

    def test_statement_size_limit(self) -> None:
        _reject("SELECT '" + "x" * 70_000 + "'")

    def test_core_relations_are_rejected_even_on_the_search_path(self) -> None:
        forbidden = frozenset({"tasks", "agent_runs", "approvals"})
        statement = "SELECT id FROM tasks"
        self.assertEqual(
            statement,
            validate_statement(statement, namespace=NAMESPACE, forbidden_relations=()),
        )
        with self.assertRaises(DataAccessError) as captured:
            validate_statement(statement, namespace=NAMESPACE, forbidden_relations=forbidden)
        self.assertEqual("DATA_STATEMENT_REJECTED", captured.exception.code)
        validate_statement(
            f"SELECT id FROM {NAMESPACE}.knowledge_sources",
            namespace=NAMESPACE,
            forbidden_relations=forbidden,
        )

    def test_migration_sql_cannot_touch_core_relations(self) -> None:
        with self.assertRaises(DataAccessError):
            validate_migration_sql(
                "CREATE TABLE chunks (id text); DROP TABLE tasks;",
                namespace=NAMESPACE,
                forbidden_relations=frozenset({"tasks"}),
            )


class ParameterGuardTests(unittest.TestCase):
    def test_nested_json_parameters_are_allowed(self) -> None:
        values = validate_parameters([1, "text", None, True, [1, 2], {"a": [1]}])
        self.assertEqual((1, "text", None, True, [1, 2], {"a": [1]}), values)

    def test_too_many_parameters_are_rejected(self) -> None:
        with self.assertRaises(DataAccessError):
            validate_parameters([0] * 300)

    def test_non_json_parameter_is_rejected(self) -> None:
        with self.assertRaises(DataAccessError):
            validate_parameters([object()])

    def test_non_finite_numeric_parameters_are_rejected_recursively(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value), self.assertRaises(DataAccessError):
                validate_parameters([{"nested": [value]}])

    def test_parameter_nesting_is_bounded(self) -> None:
        value: object = "leaf"
        for _ in range(64):
            value = [value]
        with self.assertRaises(DataAccessError):
            validate_parameters([value])

    def test_transaction_items_require_statement_objects(self) -> None:
        items = validate_transaction_items(
            [{"statement": "SELECT $1", "parameters": [1]}]
        )
        self.assertEqual((("SELECT $1", (1,)),), items)
        with self.assertRaises(DataAccessError):
            validate_transaction_items([])
        with self.assertRaises(DataAccessError):
            validate_transaction_items(["SELECT 1"])


class MigrationGuardTests(unittest.TestCase):
    def test_descriptors_must_be_sorted_and_unique(self) -> None:
        migrations = parse_migrations(
            [
                {"version": 2, "path": "migrations/0002_b.sql", "checksum": "b" * 64},
                {"version": 1, "path": "migrations/0001_a.sql", "checksum": "a" * 64},
            ]
        )
        self.assertEqual([1, 2], [item.version for item in migrations])
        with self.assertRaises(DataAccessError):
            parse_migrations(
                [
                    {"version": 1, "path": "a.sql", "checksum": "a" * 64},
                    {"version": 1, "path": "b.sql", "checksum": "b" * 64},
                ]
            )

    def test_traversal_and_absolute_paths_are_rejected(self) -> None:
        for path in ("../escape.sql", "C:/abs.sql", "/etc/passwd"):
            with self.assertRaises(DataAccessError):
                parse_migrations(
                    [{"version": 1, "path": path, "checksum": "a" * 64}]
                )

    def test_checksum_must_be_sha256_hex(self) -> None:
        with self.assertRaises(DataAccessError):
            parse_migrations([{"version": 1, "path": "a.sql", "checksum": "nope"}])

    def test_migration_sql_guard(self) -> None:
        validate_migration_sql(
            "CREATE TABLE IF NOT EXISTS chunks (id text PRIMARY KEY);",
            namespace=NAMESPACE,
        )
        for sql in (
            "DROP TABLE public.tasks;",
            "SELECT pg_read_file('/etc/passwd');",
            "CREATE TABLE ext_other_2e_x.t (id int);",
        ):
            with self.assertRaises(DataAccessError):
                validate_migration_sql(sql, namespace=NAMESPACE)

    def test_migration_uses_the_same_statement_kind_allowlist(self) -> None:
        for sql in (
            "COPY chunks TO '/tmp/export';",
            "GRANT SELECT ON chunks TO PUBLIC;",
            "DO $$ BEGIN NULL; END $$;",
            "CALL arbitrary_procedure();",
        ):
            with self.subTest(sql=sql), self.assertRaises(DataAccessError):
                validate_migration_sql(sql, namespace=NAMESPACE)


class UnavailableDataAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_dev_backend_fails_closed(self) -> None:
        access = UnavailableExtensionDataAccess()
        context = ExtensionDataContext(
            extension_id="org.example.knowledge",
            extension_version="0.1.0",
            namespace=NAMESPACE,
            payload_root=Path("."),
        )
        with self.assertRaises(DataAccessError) as captured:
            await access.handle(HOST_DATA_EXECUTE, {"statement": "SELECT 1"}, context=context)
        self.assertEqual("DATA_UNAVAILABLE", captured.exception.code)


class ChecksumHelperTests(unittest.TestCase):
    def test_sha256_is_lowercase_hex(self) -> None:
        digest = hashlib.sha256(b"x").hexdigest()
        self.assertEqual(64, len(digest))
        self.assertEqual(digest, digest.lower())


if __name__ == "__main__":
    unittest.main()
