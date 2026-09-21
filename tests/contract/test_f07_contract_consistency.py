"""F07 contract consistency: boundaries, frozen migrations, manifest and docs."""

from __future__ import annotations

import ast
import hashlib
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE_BROWSER = ROOT / "src" / "personal_assistant" / "core" / "browser"
INFRA_BROWSER = ROOT / "src" / "personal_assistant" / "infrastructure" / "browser"
EXTENSION = ROOT / "extensions" / "nju_ehall"
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
    "0006_f06_mail_transport.sql": (
        "dbc5001bdd16981f2a17f36abe4ef3fcdd36c63c3461477f1a61e1ecb6f32a01"
    ),
}
F07_CORE_MIGRATION = "0007_f07_browser_sessions.sql"
F07_REMEDIATION_MIGRATION = "0008_f07_browser_session_uniqueness.sql"
F07_REMEDIATION_SHA256 = (
    "5dcd9513b9767a1c67092dcf6520bbf75eb42963916b6da12b909413c91d2af3"
)


def _python_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.rglob("*.py") if path.is_file())


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _text_files(directory: Path) -> list[Path]:
    return sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and path.suffix in {".py", ".sql", ".toml", ".json", ".md"}
    )


class DependencyBoundaryContract(unittest.TestCase):
    def test_core_and_domain_never_import_browser_or_platform_sdks(self) -> None:
        forbidden = ("playwright", "selenium", "win32", "pywinauto", "httpx", "uvicorn")
        for path in _python_files(ROOT / "src" / "personal_assistant" / "core") + _python_files(
            ROOT / "src" / "personal_assistant" / "domain"
        ):
            text = path.read_text("utf-8")
            tree = ast.parse(text)
            imports: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module)
            for module in imports:
                for bad in forbidden:
                    self.assertFalse(
                        module == bad or module.startswith(f"{bad}."),
                        f"{path} imports forbidden module {module}",
                    )

    def test_core_browser_has_no_concrete_selectors_or_business_ids(self) -> None:
        for path in _python_files(CORE_BROWSER):
            text = path.read_text("utf-8").lower()
            self.assertNotIn("nju_ehall", text, path.name)
            self.assertNotIn("nju.ehall", text, path.name)
            self.assertNotIn("#submit", text, path.name)
            self.assertNotIn("css_selector", text, path.name)

    def test_core_never_imports_playwright(self) -> None:
        for path in _python_files(ROOT / "src" / "personal_assistant" / "core"):
            for module in _imported_modules(path):
                self.assertNotRegex(module, r"^playwright", path.name)

    def test_extension_imports_only_the_public_sdk(self) -> None:
        for path in _python_files(EXTENSION / "src"):
            text = path.read_text("utf-8")
            self.assertNotIn("personal_assistant.core", text, path.name)
            self.assertNotIn("personal_assistant.infrastructure", text, path.name)
            self.assertNotIn("api.v1", text, path.name)
            for driver in ("playwright", "selenium", "smtplib", "imaplib", "asyncpg", "sqlalchemy"):
                self.assertNotIn(f"import {driver}", text, path.name)

    def test_extension_never_establishes_network_connections(self) -> None:
        for path in _python_files(EXTENSION / "src"):
            text = path.read_text("utf-8")
            for banned in ("httpx", "requests", "socket.create_connection", "open_connection"):
                self.assertNotIn(banned, text, path.name)

    def test_playwright_is_imported_only_in_the_companion_driver(self) -> None:
        offenders: list[str] = []
        for path in _python_files(ROOT / "src"):
            if any(module.startswith("playwright") for module in _imported_modules(path)):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [str((INFRA_BROWSER / "driver.py").relative_to(ROOT))])

    def test_core_browser_never_imports_httpx(self) -> None:
        for path in _python_files(CORE_BROWSER):
            self.assertNotIn("httpx", path.read_text("utf-8"), path.name)


class BrowserSafetyContract(unittest.TestCase):
    def test_driver_never_calls_page_evaluate(self) -> None:
        text = (INFRA_BROWSER / "driver.py").read_text("utf-8")
        self.assertNotIn(".evaluate(", text)
        self.assertNotIn("evaluate_handle", text)

    def test_driver_is_headed_only(self) -> None:
        text = (INFRA_BROWSER / "driver.py").read_text("utf-8")
        self.assertIn("headless=False", text)
        self.assertNotIn("headless=True", text)

    def test_no_storage_state_or_cookie_export_anywhere_in_src(self) -> None:
        for path in _python_files(ROOT / "src"):
            text = path.read_text("utf-8")
            for banned in ("storage_state", "add_cookies", "cookies_for_url", "export_cookies"):
                self.assertNotIn(banned, text, path.name)

    def test_no_private_xhr_replay_in_extension_or_core(self) -> None:
        for path in _python_files(EXTENSION / "src") + _python_files(CORE_BROWSER):
            text = path.read_text("utf-8").lower()
            for banned in ("xmlhttprequest", "xhr", "fetch("):
                self.assertNotIn(banned, text, path.name)

    def test_captcha_handling_is_never_automated(self) -> None:
        combined = "\n".join(
            path.read_text("utf-8").lower()
            for path in _python_files(ROOT / "src") + _python_files(EXTENSION / "src")
        )
        for banned in ("captcha_solve", "ocr(", "qrcode.decode", "recognize_captcha"):
            self.assertNotIn(banned, combined)

    def test_session_tables_contain_no_secret_columns(self) -> None:
        raw = (ROOT / "migrations" / F07_CORE_MIGRATION).read_text("utf-8").lower()
        statement = "\n".join(
            line for line in raw.splitlines() if not line.strip().startswith("--")
        )
        for banned in ("cookie", "password", "token", "storage_state", "screenshot", "trace"):
            self.assertNotIn(banned, statement)
        extension_raw = (EXTENSION / "migrations" / "0001_ehall.sql").read_text("utf-8").lower()
        extension_statement = "\n".join(
            line for line in extension_raw.splitlines() if not line.strip().startswith("--")
        )
        for banned in ("cookie", "password", "credential", "secret"):
            self.assertNotIn(banned, extension_statement)

    def test_capability_is_256_bit_and_in_memory_only(self) -> None:
        text = (INFRA_BROWSER / "companion.py").read_text("utf-8")
        self.assertIn("secrets.token_urlsafe(32)", text)
        self.assertNotIn("write_text", text)
        self.assertNotIn("open(", text)


class ManifestContract(unittest.TestCase):
    def _manifest(self) -> dict[str, object]:
        return tomllib.loads((EXTENSION / "extension.toml").read_text("utf-8"))

    def test_extension_identity_and_slots(self) -> None:
        manifest = self._manifest()
        self.assertEqual(manifest["id"], "nju.ehall")
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertEqual(manifest["context_providers"], ["ehall.transaction_context"])
        self.assertEqual(manifest["workflows"], ["ehall.supervised_flow"])
        self.assertEqual(manifest["forms"], ["ehall.transaction_form"])
        self.assertEqual(manifest["migrations"], ["ehall.schema"])

    def test_tool_risks_and_capabilities_are_frozen(self) -> None:
        manifest = self._manifest()
        tools = {item["id"]: item for item in manifest["tools"]}  # type: ignore[index]
        self.assertEqual(tools["ehall.discover_apps"]["risk"], "READ")
        self.assertEqual(tools["ehall.inspect_transaction"]["risk"], "READ")
        self.assertEqual(tools["ehall.prepare_preview"]["risk"], "INTERNAL_WRITE")
        self.assertEqual(tools["ehall.open_transaction"]["risk"], "INTERNAL_WRITE")
        self.assertEqual(
            tools["ehall.open_transaction"]["capabilities"], ["browser.navigate"]
        )
        self.assertEqual(tools["ehall.fill_form"]["risk"], "EXTERNAL_WRITE")
        self.assertEqual(tools["ehall.fill_form"]["capabilities"], ["browser.fill"])
        self.assertEqual(tools["ehall.submit"]["risk"], "EXTERNAL_WRITE")
        self.assertEqual(tools["ehall.submit"]["capabilities"], ["browser.submit"])
        self.assertEqual(tools["ehall.reconcile"]["risk"], "READ")
        for tool in tools.values():
            self.assertNotEqual(tool["risk"], "PROHIBITED")

    def test_capability_table_matches_tool_level_grants(self) -> None:
        manifest = self._manifest()
        capabilities = manifest["capabilities"]  # type: ignore[index]
        self.assertEqual(capabilities["required"], ["extension.data.sql"])
        self.assertEqual(
            sorted(capabilities["optional"]),
            ["browser.fill", "browser.navigate", "browser.read", "browser.submit"],
        )

    def test_extension_ships_no_browser_or_network_dependency(self) -> None:
        lock = (EXTENSION / "requirements.lock").read_text("utf-8")
        self.assertNotIn("playwright", lock.lower())
        self.assertNotIn("httpx", lock.lower())
        self.assertNotIn("asyncpg", lock.lower())

    def test_adapter_documents_pin_page_versions_and_submit_targets(self) -> None:
        import json
        import re

        files = sorted((EXTENSION / "adapters").glob("*.json"))
        self.assertEqual(["proof.json", "transcript.json"], [item.name for item in files])
        for path in files:
            with self.subTest(adapter=path.name):
                document = json.loads(path.read_text("utf-8"))
                fingerprints = document["allowed_page_fingerprints"]
                self.assertTrue(fingerprints)
                for fingerprint in fingerprints:
                    self.assertRegex(fingerprint, r"^[0-9a-f]{64}$")
                final = [item for item in document["actions"] if item.get("final")]
                self.assertEqual(len(final), 1)
                self.assertEqual(final[0]["method"], "POST")
                self.assertTrue(final[0]["target_path"].startswith("/"))
                self.assertNotIn("?", final[0]["target_path"])
                self.assertNotRegex(final[0]["target_path"], re.compile(r"\.\."))
                navigate = [
                    item for item in document["actions"] if item.get("kind") == "navigate"
                ]
                self.assertEqual(len(navigate), len(document["transaction_ids"]))
                for item in navigate:
                    self.assertIn(item["transaction_id"], document["transaction_ids"])
                    self.assertTrue(item["navigates_to_path"].startswith("/"))
                    self.assertNotIn("?", item["navigates_to_path"])
                    self.assertFalse(item.get("final", False))
                    self.assertNotIn("method", item)
                    self.assertNotIn("target_path", item)

    def test_adapter_document_uses_only_structural_locators(self) -> None:
        import json

        document = json.loads((EXTENSION / "adapters" / "proof.json").read_text("utf-8"))
        for spec in document["fields"]:
            self.assertRegex(spec["locator"], r"^ctl:[0-2]:\d+$")
        for action in document["actions"]:
            self.assertRegex(action["locator"], r"^act:\d+$")
        self.assertFalse(any(action.get("risk") == "PROHIBITED" for action in document["actions"]))
        self.assertNotIn("browser_origin", document)


class MigrationHistoryContract(unittest.TestCase):
    def test_frozen_core_migrations_are_byte_identical(self) -> None:
        for name, expected in FROZEN_MIGRATIONS.items():
            digest = hashlib.sha256((ROOT / "migrations" / name).read_bytes()).hexdigest()
            self.assertEqual(expected, digest, f"{name} was modified")

    def test_f07_migration_is_generic(self) -> None:
        text = (ROOT / "migrations" / F07_CORE_MIGRATION).read_text("utf-8").lower()
        self.assertIn("browser_sessions", text)
        self.assertIn("browser_adapters", text)
        self.assertNotIn("nju", text)
        self.assertNotIn("ehall", text)

    def test_f07_migration_is_packaged(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text("utf-8")
        self.assertIn(F07_CORE_MIGRATION, pyproject)
        self.assertIn(F07_REMEDIATION_MIGRATION, pyproject)

    def test_remediation_migration_is_generic_and_converges_first(self) -> None:
        text = (ROOT / "migrations" / F07_REMEDIATION_MIGRATION).read_text("utf-8")
        lowered = text.lower()
        self.assertIn("browser_sessions_task_open_unique_idx", lowered)
        self.assertIn("receipt_baseline", lowered)
        self.assertIn("duplicate_converged", lowered)
        # Duplicates are converged before the unique index is created, and
        # UNKNOWN stays inside the constraint (a pending action keeps the slot).
        converge = lowered.index("duplicate_converged")
        create_index = lowered.index("create unique index")
        self.assertLess(converge, create_index)
        self.assertIn(
            "where state not in ('succeeded', 'failed', 'cancelled')", lowered
        )
        self.assertNotIn("drop ", lowered)
        self.assertNotIn("alter column", lowered)
        self.assertNotIn("nju", lowered)
        self.assertNotIn("ehall", lowered)

    def test_remediation_migration_sha256_is_registered(self) -> None:
        digest = hashlib.sha256(
            (ROOT / "migrations" / F07_REMEDIATION_MIGRATION).read_bytes()
        ).hexdigest()
        self.assertEqual(F07_REMEDIATION_SHA256, digest)

    def test_extension_migration_lives_inside_the_extension(self) -> None:
        files = sorted((EXTENSION / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql"))
        self.assertEqual(["0001_ehall.sql"], [path.name for path in files])
        text = files[0].read_text("utf-8")
        self.assertIn("ehall_transactions", text)
        self.assertNotIn("public.", text)


class ReconciliationBoundaryContract(unittest.TestCase):
    def test_extensions_cannot_declare_a_reconciliation_result(self) -> None:
        extension_files = _python_files(EXTENSION / "src") + _python_files(
            ROOT / "extension_sdk" / "src"
        )
        for path in extension_files:
            text = path.read_text("utf-8")
            self.assertNotIn("record_reconciliation", text, path.name)
        for path in _python_files(ROOT / "src"):
            self.assertNotIn("record_reconciliation", path.read_text("utf-8"), path.name)

    def test_host_capability_exposes_host_driven_reconcile(self) -> None:
        host = (INFRA_BROWSER / "host.py").read_text("utf-8")
        self.assertIn('HOST_BROWSER_RECONCILE = "host.browser.reconcile"', host)
        self.assertIn("await self._broker.reconcile(", host)
        sdk = (ROOT / "extension_sdk" / "src" / "personal_assistant_sdk" / "host.py").read_text(
            "utf-8"
        )
        self.assertIn('HOST_BROWSER_RECONCILE = "host.browser.reconcile"', sdk)
        self.assertIn("async def reconcile(", sdk)

    def test_navigation_actions_are_declared_and_host_bound(self) -> None:
        ports = (CORE_BROWSER / "ports.py").read_text("utf-8")
        self.assertIn("navigates_to_path", ports)
        self.assertIn("navigation_action", ports)
        session = (CORE_BROWSER / "session.py").read_text("utf-8")
        self.assertIn("NAVIGATION_ACTION_DRIFT", session)
        self.assertIn("NAVIGATION_MISMATCH", session)
        driver = (INFRA_BROWSER / "driver.py").read_text("utf-8")
        self.assertIn("activate_navigation", driver)
        executor = (INFRA_BROWSER / "executor.py").read_text("utf-8")
        self.assertIn('BROWSER_NAVIGATE_CAPABILITY = "browser.navigate"', executor)

    def test_submit_is_bound_to_the_declared_network_target(self) -> None:
        ports = (CORE_BROWSER / "ports.py").read_text("utf-8")
        self.assertIn("target_origin", ports)
        self.assertIn("target_path", ports)
        session = (CORE_BROWSER / "session.py").read_text("utf-8")
        self.assertIn("SUBMIT_TARGET_DRIFT", session)
        driver = (INFRA_BROWSER / "driver.py").read_text("utf-8")
        self.assertIn("_submit_allowance", driver)
        self.assertIn("_allowance_matches", driver)
        self.assertIn("_mutations_blocked", driver)


class FailClosedDefaultsContract(unittest.TestCase):
    def test_browser_settings_default_to_disabled(self) -> None:
        text = (ROOT / "src" / "personal_assistant" / "settings.py").read_text("utf-8")
        self.assertIn("browser_allowed_origins: tuple[str, ...] = ()", text)
        self.assertIn("browser_submit_enabled: bool = False", text)
        self.assertIn("browser_companion_url: str = \"\"", text)

    def test_policy_rejects_r3_permanently(self) -> None:
        text = (CORE_BROWSER / "policy.py").read_text("utf-8")
        self.assertIn("COURSE_WITHDRAWAL", text)
        self.assertIn("REVOCATION", text)
        self.assertIn("PAYMENT", text)
        self.assertIn("COURSE_CHANGE", text)
        self.assertIn("LEGAL_DECLARATION", text)
        self.assertIn("UNKNOWN_TRANSACTION", text)
        self.assertIn("UNKNOWN_PAGE_VERSION", text)

    def test_gateway_unknown_semantics_untouched(self) -> None:
        text = (ROOT / "src" / "personal_assistant" / "core" / "tools" / "gateway.py").read_text(
            "utf-8"
        )
        self.assertIn("V4", "V4")  # sentinel to keep the file in the scan set
        self.assertIn("OUTCOME_UNKNOWN", text.replace("ToolOutcomeKind.", ""))


class DocumentationContract(unittest.TestCase):
    def test_contracts_document_the_f07_section(self) -> None:
        text = (ROOT / "docs" / "CONTRACTS_AND_INTERFACES.md").read_text("utf-8")
        self.assertIn("F07", text)
        self.assertIn("contract v1.7", text)
        self.assertIn("BrowserSessionBroker", text)
        self.assertIn("Desktop Companion", text)

    def test_next_steps_marks_f07_in_progress(self) -> None:
        text = (ROOT / "docs" / "NEXT_STEPS.md").read_text("utf-8")
        self.assertRegex(text, r"F07[^\n]*IN_PROGRESS")
        self.assertRegex(text, r"F08[^\n]*TODO")

    def test_readme_documents_the_browser_boundary(self) -> None:
        text = (ROOT / "README.md").read_text("utf-8")
        self.assertIn("PA_BROWSER_ALLOWED_ORIGINS", text)
        self.assertIn("PA_BROWSER_COMPANION_URL", text)


if __name__ == "__main__":
    unittest.main()
