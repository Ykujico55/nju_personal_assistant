"""F06 contract consistency: slots, risks, boundaries, migrations and docs."""

from __future__ import annotations

import hashlib
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXTENSION = ROOT / "extensions" / "nju_smail"
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
F06_CORE_MIGRATION = "0006_f06_mail_transport.sql"
REQUIRED_SLOTS = {
    "event_sources": ("smail.poll_inbox",),
    "context_providers": ("smail.thread_history",),
    "workflows": ("smail.reply_flow",),
    "schedules": ("smail.poll_every_5m",),
    "forms": ("smail.account_settings",),
    "migrations": ("smail.mail_schema",),
}
REQUIRED_TOOL_RISKS = {
    "smail.search": "READ",
    "smail.prepare_reply": "INTERNAL_WRITE",
    "smail.send": "EXTERNAL_WRITE",
    "smail.sync": "INTERNAL_WRITE",
    "smail.send_status": "INTERNAL_WRITE",
    "smail.reconcile_send": "INTERNAL_WRITE",
}


class ManifestContract(unittest.TestCase):
    def setUp(self) -> None:
        with (EXTENSION / "extension.toml").open("rb") as stream:
            self.manifest = tomllib.load(stream)

    def test_manifest_registers_the_required_slots(self) -> None:
        self.assertEqual("nju.smail", self.manifest["id"])
        for key, expected in REQUIRED_SLOTS.items():
            self.assertEqual(list(expected), self.manifest[key], key)

    def test_tool_risks_are_frozen(self) -> None:
        risks = {
            item["id"]: item["risk"]
            for item in self.manifest["tools"]
        }
        self.assertEqual(REQUIRED_TOOL_RISKS, risks)

    def test_send_tool_declares_the_host_capability(self) -> None:
        tools = {item["id"]: item for item in self.manifest["tools"]}
        self.assertEqual(["mail.send"], tools["smail.send"].get("capabilities"))
        self.assertNotIn("capabilities", tools["smail.prepare_reply"])

    def test_capabilities_separate_read_from_send(self) -> None:
        capabilities = self.manifest["capabilities"]
        self.assertIn("mail.read", capabilities["required"])
        self.assertIn("extension.data.sql", capabilities["required"])
        self.assertIn("artifact.read", capabilities["required"])
        self.assertIn("artifact.write", capabilities["required"])
        self.assertEqual(["mail.send"], capabilities["optional"])
        self.assertNotIn("mail.send", capabilities["required"])


class AccountRegistryContract(unittest.TestCase):
    def test_host_owns_the_account_registry(self) -> None:
        ports = (ROOT / "src" / "personal_assistant" / "core" / "mail" / "ports.py").read_text(
            "utf-8"
        )
        self.assertIn("class MailAccountRegistry", ports)
        self.assertIn("class MailAccountRecord", ports)
        host = (
            ROOT
            / "src"
            / "personal_assistant"
            / "infrastructure"
            / "mail"
            / "host.py"
        ).read_text("utf-8")
        self.assertIn("resolve_account", host)
        self.assertNotIn("params.get(\"imap_host\")", host)
        self.assertNotIn("params.get(\"secret_handle", host)
        executor = (
            ROOT
            / "src"
            / "personal_assistant"
            / "infrastructure"
            / "mail"
            / "executor.py"
        ).read_text("utf-8")
        self.assertIn("account_fingerprint", executor)
        self.assertNotIn("secret_handle_id", executor)

    def test_capability_router_exists_for_production_send(self) -> None:
        router = (
            ROOT
            / "src"
            / "personal_assistant"
            / "infrastructure"
            / "tools"
            / "capability_router.py"
        ).read_text("utf-8")
        self.assertIn("MailSendExecutor", router)
        bootstrap = (ROOT / "src" / "personal_assistant" / "bootstrap.py").read_text(
            "utf-8"
        )
        self.assertIn("CapabilityRoutingExecutor", bootstrap)
        self.assertIn("tool_gateway=", bootstrap)
        self.assertIn("refresh_tool_registry", bootstrap)


class BoundaryContract(unittest.TestCase):
    def test_extension_never_imports_host_core_or_infrastructure(self) -> None:
        for path in sorted(EXTENSION.rglob("*.py")):
            text = path.read_text("utf-8")
            self.assertNotIn("import personal_assistant.", text, path)
            self.assertNotIn("from personal_assistant import", text, path)

    def test_extension_never_opens_smtp_or_imap_sockets(self) -> None:
        forbidden = ("smtplib", "imaplib", "poplib")
        for path in sorted(EXTENSION.rglob("*.py")):
            text = path.read_text("utf-8")
            for module in forbidden:
                self.assertNotIn(module, text, f"{module} in {path}")

    def test_core_has_no_business_extension_branch(self) -> None:
        for path in sorted((ROOT / "src").rglob("*.py")):
            text = path.read_text("utf-8").lower()
            self.assertNotIn("nju_smail", text, path)
            self.assertNotIn("nju.smail", text, path)

    def test_schedule_polls_every_five_minutes(self) -> None:
        worker = (EXTENSION / "src" / "nju_smail" / "worker.py").read_text("utf-8")
        self.assertIn("interval_seconds=interval", worker)
        models = (EXTENSION / "src" / "nju_smail" / "models.py").read_text("utf-8")
        self.assertIn("poll_interval_seconds: int = 300", models)


class MigrationHistoryContract(unittest.TestCase):
    def test_frozen_core_migrations_are_byte_identical(self) -> None:
        for name, expected in FROZEN_MIGRATIONS.items():
            digest = hashlib.sha256((ROOT / "migrations" / name).read_bytes()).hexdigest()
            self.assertEqual(expected, digest, f"{name} was modified")

    def test_f06_added_only_the_generic_mail_ledger(self) -> None:
        migration = (ROOT / "migrations" / F06_CORE_MIGRATION).read_text("utf-8")
        self.assertIn("mail_delivery_actions", migration)
        self.assertNotIn("smail", migration.lower())
        self.assertNotIn("nju", migration.lower())

    def test_f06_migration_lives_inside_the_extension(self) -> None:
        files = sorted((EXTENSION / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql"))
        self.assertEqual(["0001_mail.sql"], [path.name for path in files])
        text = files[0].read_text("utf-8")
        for table in (
            "mail_messages",
            "mail_message_locations",
            "mail_drafts",
            "mail_draft_versions",
            "mail_send_actions",
            "mail_events",
        ):
            self.assertIn(table, text)
        self.assertNotRegex(text.lower(), r"\bpassword\s+(text|char|varchar|bytea)\b")
        self.assertNotIn("credential_value", text.lower())
        self.assertNotIn("secret_value", text.lower())
        # The extension never stores endpoints or credential handles: only the
        # host-owned registry knows them, keyed by account_id.
        self.assertNotIn("imap_host", text.lower())
        self.assertNotIn("smtp_host", text.lower())
        self.assertNotIn("secret_handle", text.lower())

    def test_wheel_packaging_lists_the_new_migration(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text("utf-8")
        self.assertIn(F06_CORE_MIGRATION, pyproject)

    def test_documentation_keeps_f06_in_progress(self) -> None:
        next_steps = (ROOT / "docs" / "NEXT_STEPS.md").read_text("utf-8")
        self.assertIn("F06", next_steps)
        self.assertRegex(next_steps, r"F06[^\n]*IN_PROGRESS")
        todo = (ROOT / "TODO.md").read_text("utf-8")
        self.assertRegex(todo, r"F06[^\n]*IN_PROGRESS")


class PromptInjectionBoundary(unittest.TestCase):
    def test_extension_never_treats_message_text_as_instructions(self) -> None:
        worker = (EXTENSION / "src" / "nju_smail" / "worker.py").read_text("utf-8")
        # Mail content only ever reaches data fields: no eval/exec, no dynamic
        # tool registration and no send call from parsed content.
        for forbidden in ("eval(", "exec(", "compile(", "__import__"):
            self.assertNotIn(forbidden, worker)


if __name__ == "__main__":
    unittest.main()
