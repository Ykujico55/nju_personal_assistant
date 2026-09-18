"""F05 contract consistency: slots, risks, boundaries, migrations and docs."""

from __future__ import annotations

import hashlib
import re
import tomllib
import unittest
from pathlib import Path

from personal_assistant.core.extensions.manifest import ManifestParser

ROOT = Path(__file__).resolve().parents[2]
EXTENSION = ROOT / "extensions" / "personal_knowledge"
FROZEN_MIGRATIONS = {
    "0001_core.sql": "836878d68c83edac5b70671e3aa5da12e7f949c89c72cae8d300edc7fba8cd59",
    "0002_f01_persistence.sql": "5fdc67aaff07dbcd486fc62774ad5745cbef871332433dfb8535790a0a18a580",
    "0003_f02_operations.sql": "28cbda227918bfcd80366208b59713eb0dbb0b0cbdaa582cb5e55a91ce2e1e03",
    "0004_f02_operation_request_scope.sql": (
        "6b6bb9f5cea83b443c9ca345f7a9d134f6ebca69969f01e23557ecff706af6fc"
    ),
    "0005_f04_model_disclosure.sql": (
        "012532834b281040d0031b48744ec7298e3c9b960bb247f51fd22c050f7534f0"
    ),
}

REQUIRED_SLOTS = {
    "EventSource": ("knowledge.file_changes",),
    "ContextProvider": ("knowledge.retrieve",),
    "ScheduleProvider": ("knowledge.reconcile",),
    "MigrationProvider": ("knowledge.index_schema",),
    "FormSchemaProvider": ("knowledge.roots",),
}


class ExtensionBoundaryContract(unittest.TestCase):
    def test_manifest_registers_the_standard_slots(self) -> None:
        manifest = ManifestParser().parse(EXTENSION)
        self.assertEqual("personal.knowledge", manifest.id)
        self.assertEqual("0.1.0", manifest.version)
        slots = manifest.slots
        for slot, capability in REQUIRED_SLOTS.items():
            self.assertEqual(capability, slots.get(slot), f"slot {slot} drifted")
        tools = {tool.id: tool.risk for tool in manifest.tools}
        self.assertEqual(
            {"knowledge.search": "READ", "knowledge.reindex": "INTERNAL_WRITE"},
            tools,
        )
        self.assertNotIn("EXTERNAL_WRITE", tools.values())
        self.assertNotIn("PROHIBITED", tools.values())

    def test_extension_never_imports_core_or_infrastructure(self) -> None:
        offenders: list[str] = []
        for path in EXTENSION.rglob("*.py"):
            text = path.read_text("utf-8")
            if "personal_assistant.core" in text or "personal_assistant.infrastructure" in text:
                offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([], offenders)

    def test_extension_never_handles_database_credentials_or_drivers(self) -> None:
        forbidden = (
            "asyncpg",
            "psycopg",
            "sqlalchemy",
            "PA_DATABASE_URL",
            "postgresql://",
            "password=",
            "password:",
            "api_key",
            "client_secret",
        )
        offenders: list[str] = []
        for path in EXTENSION.rglob("*.py"):
            text = path.read_text("utf-8")
            for token in forbidden:
                if token in text:
                    offenders.append(f"{path.relative_to(ROOT).as_posix()}: {token}")
        self.assertEqual([], offenders)

    def test_extension_reads_the_environment_nowhere(self) -> None:
        offenders: list[str] = []
        for path in EXTENSION.rglob("*.py"):
            text = path.read_text("utf-8")
            if "os.environ" in text or "getenv(" in text:
                offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([], offenders)

    def test_embedding_is_local_only_and_defaults_to_disabled(self) -> None:
        source = (EXTENSION / "src" / "personal_knowledge" / "embedding.py").read_text("utf-8")
        self.assertNotIn("api.openai.com", source)
        self.assertIn("_LOOPBACK_HOSTS", source)
        self.assertIn('provider = config.get("provider", "none")', source)
        self.assertIn("EmbeddingIdentity", source)

    def test_core_contains_no_knowledge_special_case(self) -> None:
        offenders: list[str] = []
        for path in (ROOT / "src" / "personal_assistant").rglob("*.py"):
            text = path.read_text("utf-8")
            if "personal.knowledge" in text or "personal_knowledge" in text:
                offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([], offenders)

    def test_public_api_has_no_knowledge_route(self) -> None:
        offenders: list[str] = []
        for path in (ROOT / "src" / "personal_assistant" / "api").rglob("*.py"):
            text = path.read_text("utf-8")
            if "knowledge" in text.lower():
                offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([], offenders)

    def test_extension_is_not_packaged_into_the_framework_wheel(self) -> None:
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
        packages = pyproject["tool"]["setuptools"]["packages"]["find"]
        self.assertEqual(["personal_assistant*"], packages["include"])
        data_files = pyproject["tool"]["setuptools"].get("data-files", {})
        for values in data_files.values():
            for pattern in values:
                self.assertNotIn("extensions/", pattern)
                self.assertNotIn("personal_knowledge", pattern)

    def test_configuration_is_generic_and_validated_by_host(self) -> None:
        admin = (ROOT / "src" / "personal_assistant" / "admin_app.py").read_text("utf-8")
        self.assertIn("validate_extension_config", admin)
        self.assertIn("/admin/v1/extensions/{extension_id}/config", admin)
        package = (
            ROOT / "src" / "personal_assistant" / "core" / "extensions" / "config.py"
        ).read_text("utf-8")
        self.assertIn("validate_extension_config", package)


class MigrationHistoryContract(unittest.TestCase):
    def test_frozen_core_migrations_are_byte_identical(self) -> None:
        for name, expected in FROZEN_MIGRATIONS.items():
            digest = hashlib.sha256((ROOT / "migrations" / name).read_bytes()).hexdigest()
            self.assertEqual(expected, digest, f"{name} was modified")

    def test_no_new_core_migration_was_required_for_f05(self) -> None:
        extra = [
            path.name
            for path in sorted((ROOT / "migrations").glob("*.sql"))
            if path.name not in FROZEN_MIGRATIONS
        ]
        self.assertEqual([], extra)

    def test_f05_migration_lives_inside_the_extension(self) -> None:
        files = sorted((EXTENSION / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql"))
        self.assertEqual(["0001_knowledge_index.sql"], [path.name for path in files])
        text = files[0].read_text("utf-8")
        self.assertIn("knowledge_sources", text)
        self.assertIn("to_tsvector('simple'", text)
        self.assertIn("embedding vector", text)
        self.assertNotIn("public.", text)

    def test_extension_migration_descriptor_hashes_match_the_file(self) -> None:
        from personal_knowledge.worker import MIGRATIONS

        for descriptor in MIGRATIONS:
            path = EXTENSION / str(descriptor["path"])
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(), descriptor["checksum"]
            )


class SdkContractTests(unittest.TestCase):
    def test_public_sdk_exposes_the_host_data_client(self) -> None:
        from personal_assistant_sdk import HostDataClient, MigrationDescriptor, RuntimeContext

        self.assertIn("execute", dir(HostDataClient))
        self.assertIn("transaction", dir(HostDataClient))
        self.assertIn("migrate", dir(HostDataClient))
        self.assertIsNone(RuntimeContext.__dataclass_fields__["host_data"].default)
        self.assertIsNone(
            MigrationDescriptor(version=1, checksum="a" * 64, description="x").path
        )

    def test_host_capability_methods_are_generic(self) -> None:
        from personal_assistant.core.extensions.data_access import (
            HOST_DATA_METHODS,
        )

        self.assertEqual(
            {"host.data.execute", "host.data.transaction", "host.data.migrate"},
            set(HOST_DATA_METHODS),
        )
        text = (
            ROOT / "src" / "personal_assistant" / "core" / "extensions" / "data_access.py"
        ).read_text("utf-8")
        self.assertNotIn("personal.knowledge", text)
        self.assertNotIn("personal_knowledge", text)


class EvidenceContractTests(unittest.TestCase):
    def test_result_and_evidence_metadata_keys_are_locked(self) -> None:
        source = (EXTENSION / "src" / "personal_knowledge" / "worker.py").read_text("utf-8")
        for key in (
            "source_uri",
            "source_version",
            "content_hash",
            "locator",
            "sensitivity",
            "trust",
            "status",
            "heading_path",
        ):
            self.assertIn(key, source)
        self.assertIn("STATUS", source.upper())

    def test_evidence_status_enum_matches_contract(self) -> None:
        from personal_knowledge.models import EvidenceStatus

        self.assertEqual(
            {"CURRENT", "STALE", "DELETED"}, {item.value for item in EvidenceStatus}
        )


class DocumentationContractTests(unittest.TestCase):
    def test_contracts_document_the_host_data_capability(self) -> None:
        text = (ROOT / "docs" / "CONTRACTS_AND_INTERFACES.md").read_text("utf-8")
        self.assertIn("host.data.execute", text)
        self.assertIn("host.data.migrate", text)
        self.assertIn("ExtensionConfigStore", text)

    def test_next_steps_and_todo_record_completed_f05(self) -> None:
        next_steps = (ROOT / "docs" / "NEXT_STEPS.md").read_text("utf-8")
        todo = (ROOT / "TODO.md").read_text("utf-8")
        self.assertIn("F05 — 个人知识扩展（DONE", next_steps)
        self.assertIn("F05 — 个人知识扩展（DONE", todo)
        self.assertNotIn("F05 — 个人知识扩展（IN_PROGRESS", todo)
        self.assertRegex(re.sub(r"\s+", " ", todo), r"F06.*NEXT")

    def test_readme_documents_the_generic_config_endpoint(self) -> None:
        readme = (ROOT / "README.md").read_text("utf-8")
        self.assertIn("/admin/v1/extensions/{extension_id}/config", readme)


if __name__ == "__main__":
    unittest.main()
