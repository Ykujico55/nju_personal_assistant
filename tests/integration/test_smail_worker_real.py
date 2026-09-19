"""F06: production composition root end-to-end for nju.smail.

Uses ``build_container`` (real PostgreSQL, real Supervisor, real per-version
venv and worker subprocess, real host mail broker against simulated TLS servers)
to install, enable, synchronize, draft, approve and send a single message
through the production ``Container.tool_gateway``, then disable, recover and
uninstall the extension.

Requires ``PA_TEST_DATABASE_URL``.  No real mailbox is contacted.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import tempfile
import unittest
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg

from personal_assistant.bootstrap import Container, build_container
from personal_assistant.core.extensions.lifecycle import InstallationConfirmation
from personal_assistant.core.mail import MailAccountRecord
from personal_assistant.domain import ToolCall, ToolOutcomeKind
from personal_assistant.infrastructure.memory import InMemorySecretStore
from personal_assistant.settings import Settings
from tests.support.mail_servers import (
    ImapMessage,
    ImapState,
    SimulatedImapServer,
    SimulatedSmtpServer,
    SmtpState,
    generate_tls_pair,
)

BASE_URL = os.getenv("PA_TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[2]
EXTENSION_DIR = ROOT / "extensions" / "nju_smail"
EXTENSION_ID = "nju.smail"
NAMESPACE = "ext_nju_2e_smail"
USERNAME = "student@smail.nju.edu.cn"
PASSWORD = "client-password-f06"
GOOD = "controlled@example.test"


def _dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgres://", "postgresql://"
    )


def _with_db(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + database, "", ""))


def _safe_rmtree(path: Path) -> None:
    resolved = path.resolve()
    if not resolved.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise AssertionError(f"refusing to delete {resolved}")
    shutil.rmtree(resolved, ignore_errors=True)


def _raw(message_id: str, subject: str, body: str) -> bytes:
    return (
        f"From: sender@example.test\r\n"
        f"To: {USERNAME}\r\n"
        f"Subject: {subject}\r\n"
        f"Message-ID: {message_id}\r\n"
        f"Date: Fri, 18 Sep 2026 09:00:00 +0000\r\n"
        f"MIME-Version: 1.0\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n\r\n{body}"
    ).encode()


@unittest.skipUnless(BASE_URL, "set PA_TEST_DATABASE_URL to run real PostgreSQL tests")
class SmailCompositionRootTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        base = _dsn(BASE_URL or "")
        if not urlsplit(base).path.lstrip("/").endswith("_test"):
            raise RuntimeError("PA_TEST_DATABASE_URL must name a dedicated *_test database")
        self._admin_dsn = _with_db(base, "postgres")
        self._db_name = f"pa_f06_root_{uuid.uuid4().hex}"
        admin = await asyncpg.connect(self._admin_dsn)
        try:
            await admin.execute(f'CREATE DATABASE "{self._db_name}"')
        finally:
            await admin.close()
        self.database_url = _with_db(base, self._db_name)
        self.tmp = Path(tempfile.mkdtemp(prefix="pa_f06_root_"))
        self.tls = generate_tls_pair()
        self.imap_state = ImapState(username=USERNAME, password=PASSWORD)
        self.imap_state.folders["INBOX"] = [
            ImapMessage(uid=1, raw=_raw("<one@example.test>", "Welcome", "Hello"))
        ]
        self.imap_state.uidvalidity["INBOX"] = 3
        self.imap_state.folders["Sent"] = []
        self.imap_state.uidvalidity["Sent"] = 21
        self.imap = SimulatedImapServer(self.imap_state, self.tls.server_context)
        await self.imap.start()
        self.smtp_state = SmtpState(username=USERNAME, password=PASSWORD)
        self.smtp = SimulatedSmtpServer(self.smtp_state, self.tls.server_context)
        await self.smtp.start()
        self.secrets = InMemorySecretStore()
        self.handle = await self.secrets.put(
            name="smail", kind="mail_client_password", value=PASSWORD
        )
        self.account = MailAccountRecord(
            account_id="nju",
            address=USERNAME,
            imap_host="localhost",
            imap_port=self.imap.port,
            smtp_host="localhost",
            smtp_port=self.smtp.port,
            secret_handle_id=self.handle.id,
            send_enabled=True,
            tls_mode="implicit",
        )
        self.settings = Settings(
            environment="development",
            log_level="INFO",
            public_host="127.0.0.1",
            public_port=8000,
            admin_host="127.0.0.1",
            admin_port=8001,
            health_host="127.0.0.1",
            health_port=8010,
            storage_backend="postgres",
            database_url=self.database_url,
            extension_root=self.tmp,
            artifact_root=self.tmp / "artifacts",
            trust_cloudflare_access=False,
            public_origin=None,
            cf_access_team_domain=None,
            cf_access_aud=None,
            mail_send_enabled=True,
            mail_test_recipients=(GOOD,),
        )
        self.container: Container = build_container(
            self.settings,
            secret_store=self.secrets,
            mail_ssl_context=self.tls.client_context,
        )
        await self.container.storage.startup()
        await self.container.mail_accounts.replace_all((self.account,))
        await self.container.extension_config_store.save(
            EXTENSION_ID,
            {"accounts": [{"account_id": "nju"}], "folders": ["INBOX"]},
        )

    async def asyncTearDown(self) -> None:
        with contextlib.suppress(Exception):
            await self.container.extension_supervisor.stop_all()
        with contextlib.suppress(Exception):
            await self.container.aclose()
        with contextlib.suppress(Exception):
            await self.container.storage.close()
        with contextlib.suppress(Exception):
            await self.imap.stop()
        with contextlib.suppress(Exception):
            await self.smtp.stop()
        admin = await asyncpg.connect(self._admin_dsn)
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                self._db_name,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{self._db_name}"')
        finally:
            await admin.close()
        _safe_rmtree(self.tmp)

    def _confirmation(self, preview: object) -> InstallationConfirmation:
        return InstallationConfirmation(
            plan_id=preview.plan_id,  # type: ignore[attr-defined]
            confirmation_nonce=preview.confirmation_nonce,  # type: ignore[attr-defined]
            preview_hash=preview.preview_hash,  # type: ignore[attr-defined]
            actor="f06-root",
            confirmed_at=datetime.now(UTC),
            accepted_warning=True,
        )

    async def _run(self, operation: object) -> object:
        return await self.container.extension_supervisor.wait_operation(
            operation.id, timeout_seconds=300.0  # type: ignore[attr-defined]
        )

    async def _sql(self, statement: str, *parameters: object) -> list[dict]:
        connection = await asyncpg.connect(self.database_url)
        try:
            rows = await connection.fetch(statement, *parameters)
        finally:
            await connection.close()
        return [dict(row) for row in rows]

    def _zip_artifact(self, target: Path) -> Path:
        skipped = {".venv", "__pycache__", ".pytest_cache", ".mypy_cache", "build"}
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(EXTENSION_DIR.rglob("*")):
                relative = path.relative_to(EXTENSION_DIR)
                if any(part in skipped for part in relative.parts):
                    continue
                if path.is_file():
                    archive.write(path, relative.as_posix())
        return target

    async def test_production_gateway_install_sync_draft_send_uninstall(self) -> None:
        supervisor = self.container.extension_supervisor
        artifact = self._zip_artifact(self.tmp / "nju-smail-0.1.0.zip")
        preview = await supervisor.inspect(str(artifact))
        self.assertEqual(EXTENSION_ID, preview.extension_id)
        operation = await supervisor.begin_install(
            preview.plan_id, self._confirmation(preview)
        )
        result = await self._run(operation)
        self.assertEqual("SUCCEEDED", result.status.value, result.diagnostic_code)
        operation = await supervisor.begin_enable(EXTENSION_ID)
        result = await self._run(operation)
        self.assertEqual("SUCCEEDED", result.status.value, result.diagnostic_code)
        # The production gateway publishes descriptors from the durable lifecycle.
        await self.container.refresh_tool_registry()
        connection = await asyncpg.connect(self.database_url)
        try:
            await connection.execute(
                "INSERT INTO tasks (id, owner_id, objective, status, version) "
                "VALUES ($1, $2, $3, $4, $5) ON CONFLICT (id) DO NOTHING",
                "task-f06-root",
                "owner",
                "smail root flow",
                "CREATED",
                0,
            )
        finally:
            await connection.close()
        try:
            synced = await supervisor.invoke_tool(
                EXTENSION_ID,
                "smail.sync",
                {},
                task_id="task-f06-root",
                run_id="run-f06-root",
                idempotency_key="f06-root-sync-1",
                deadline_seconds=120.0,
            )
            self.assertEqual("SUCCEEDED", synced["outcome"], synced)
            self.assertEqual(1, synced["output"]["new_messages"])

            draft = await supervisor.invoke_tool(
                EXTENSION_ID,
                "smail.prepare_reply",
                {
                    "account_id": "nju",
                    "to": [GOOD],
                    "subject": "Re: Welcome",
                    "body_text": "A controlled reply",
                },
                task_id="task-f06-root",
                run_id="run-f06-root",
                idempotency_key="f06-root-draft",
                deadline_seconds=60.0,
            )
            self.assertEqual("SUCCEEDED", draft["outcome"], draft)
            output = draft["output"]
            arguments = {
                "account_id": output["account_id"],
                "account_fingerprint": output["account_fingerprint"],
                "draft_id": output["draft_id"],
                "draft_version": output["version"],
                "canonical_digest": output["canonical_digest"],
                "local_action_id": output["local_action_id"],
                "message_id": output["message_id"],
                "from_address": output["from_address"],
                "to": list(output["to"]),
                "cc": list(output["cc"]),
                "bcc": list(output["bcc"]),
                "subject": output["subject"],
                "mime_sha256": output["mime_sha256"],
                "attachment_hashes": list(output["attachment_hashes"]),
            }
            descriptor = self.container.tool_registry.snapshot().resolve(
                "smail.send", "0.1.0"
            )
            self.assertIn("mail.send", descriptor.required_capabilities)

            def tool_call(approval_id: str | None) -> ToolCall:
                return ToolCall(
                    tool_id="smail.send",
                    tool_version=descriptor.version,
                    task_id="task-f06-root",
                    arguments=arguments,
                    target={"account_id": "nju"},
                    workflow_allowed_tools=frozenset({"smail.send"}),
                    granted_capabilities=frozenset({"mail.send"}),
                    approval_id=approval_id,
                    idempotency_key=output["local_action_id"],
                )

            needed = await self.container.tool_gateway.invoke(tool_call(None))
            self.assertEqual(ToolOutcomeKind.APPROVAL_REQUIRED, needed.kind)
            approval = await self.container.approvals.get(needed.approval_id or "")
            await self.container.approvals.approve(
                approval.id, nonce=approval.nonce, actor_id="owner"
            )
            outcome = await self.container.tool_gateway.invoke(tool_call(approval.id))
            self.assertEqual(ToolOutcomeKind.SUCCESS, outcome.kind, outcome.message)
            self.assertEqual(1, len(self.smtp_state.messages), outcome.result)
            self.assertEqual(
                arguments["mime_sha256"],
                hashlib.sha256(self.smtp_state.messages[0]).hexdigest(),
            )
            ledger = await self._sql(
                "SELECT local_action_id, status FROM mail_delivery_actions "
                "WHERE local_action_id = $1",
                output["local_action_id"],
            )
            self.assertEqual(
                [{"local_action_id": output["local_action_id"], "status": "SUCCEEDED"}],
                ledger,
            )

            # Reconciliation converges the host ledger: simulate the Sent copy.
            self.imap_state.folders["Sent"].append(
                ImapMessage(
                    uid=40,
                    raw=(
                        f"From: {USERNAME}\r\n"
                        f"To: {GOOD}\r\n"
                        f"Subject: Re: Welcome\r\n"
                        f"Message-ID: {output['message_id']}\r\n"
                        "\r\nA controlled reply"
                    ).encode(),
                )
            )
            message_ids = await self._sql(
                f'SELECT count(*) AS count FROM "{NAMESPACE}".mail_messages'
            )
            self.assertEqual(1, message_ids[0]["count"])

            self.imap_state.folders["INBOX"].append(
                ImapMessage(uid=2, raw=_raw("<two@example.test>", "Follow-up", "More"))
            )
            await self.container.extension_supervisor.stop_all()
            await self.container.extension_supervisor.recover()
            after_restart = await supervisor.invoke_tool(
                EXTENSION_ID,
                "smail.sync",
                {},
                task_id="task-f06-root",
                run_id="run-f06-root",
                idempotency_key="f06-root-sync-2",
                deadline_seconds=120.0,
            )
            self.assertEqual(1, after_restart["output"]["new_messages"])
        finally:
            with contextlib.suppress(Exception):
                operation = await supervisor.begin_disable(EXTENSION_ID)
                await self._run(operation)
            operation = await supervisor.begin_uninstall(EXTENSION_ID)
            result = await self._run(operation)
            self.assertEqual("SUCCEEDED", result.status.value, result.diagnostic_code)
            record = await supervisor.record(EXTENSION_ID)
            self.assertEqual("UNINSTALLED", record.state.value)  # type: ignore[union-attr]
            namespaces = await self._sql(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name = $1",
                NAMESPACE,
            )
            self.assertEqual(1, len(namespaces), "business data must be retained")


if __name__ == "__main__":
    unittest.main()
