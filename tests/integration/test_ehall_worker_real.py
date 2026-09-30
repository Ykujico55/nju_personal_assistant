"""F07 Gate 3/5: real extension zip through the real supervisor and workers.

The browser is the deterministic test double (real Playwright coverage lives in
``test_browser_companion_real_f07.py``); everything else is real: PostgreSQL,
per-version venv, Worker subprocess, host browser capability, Tool Gateway,
R2 approvals and the side-effect outbox.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg

from personal_assistant.bootstrap import build_container
from personal_assistant.core.browser import BrowserSessionState
from personal_assistant.core.extensions.lifecycle import InstallationConfirmation
from personal_assistant.domain import ToolCall, ToolOutcomeKind
from personal_assistant.settings import Settings
from tests.support.browser import (
    TEST_APP_PATH,
    TEST_DISCOVERY_PATH,
    TEST_ORIGIN,
    standard_companion,
)

BASE_URL = os.getenv("PA_TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[2]
EXTENSION_DIR = ROOT / "extensions" / "nju_ehall"
EXTENSION_ID = "nju.ehall"
EXTENSION_VERSION = "0.1.0"
NAMESPACE = "ext_nju_2e_ehall"
TASK_ID = "task-f07-root"
_SKIP_PARTS = {".venv", "__pycache__", ".pytest_cache", ".mypy_cache", "build", ".git"}


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


def _zip_artifact(target: Path) -> Path:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(EXTENSION_DIR.rglob("*")):
            if any(part in _SKIP_PARTS for part in path.parts):
                continue
            if path.is_dir():
                continue
            archive.write(path, path.relative_to(EXTENSION_DIR).as_posix())
    return target


@unittest.skipUnless(BASE_URL, "set PA_TEST_DATABASE_URL to run real Worker tests")
class EhallWorkerRealTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        base = _dsn(BASE_URL or "")
        if not urlsplit(base).path.lstrip("/").endswith("_test"):
            raise RuntimeError("PA_TEST_DATABASE_URL must name a dedicated *_test database")
        self._admin_dsn = _with_db(base, "postgres")
        self._db_name = f"pa_f07_worker_{uuid.uuid4().hex}"
        admin = await asyncpg.connect(self._admin_dsn)
        try:
            await admin.execute(f'CREATE DATABASE "{self._db_name}"')
        finally:
            await admin.close()
        self.tmp = Path(tempfile.mkdtemp(prefix="pa_f07_worker_"))
        self.companion = standard_companion()
        self.companion.pages[TEST_DISCOVERY_PATH].links = [
            {"text": "在读证明申请", "path": TEST_APP_PATH},
            {"text": "退课申请", "path": "/apps/withdraw"},
            {"text": "在线缴费", "path": "/apps/payment"},
        ]
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
            database_url=_with_db(base, self._db_name),
            extension_root=self.tmp / "extensions",
            artifact_root=self.tmp / "artifacts",
            trust_cloudflare_access=False,
            public_origin=None,
            cf_access_team_domain=None,
            cf_access_aud=None,
            browser_allowed_origins=(TEST_ORIGIN,),
            browser_submit_enabled=True,
        )
        self.container = build_container(
            self.settings, browser_companion=self.companion
        )
        await self.container.storage.startup()
        await self.container.extension_config_store.save(
            EXTENSION_ID, {"browser_origin": TEST_ORIGIN}
        )
        async with self.container.storage.database.connection() as connection:
            await connection.execute(
                "INSERT INTO tasks (id, owner_id, objective, status, version) "
                "VALUES ($1, $2, $3, $4, $5) ON CONFLICT (id) DO NOTHING",
                TASK_ID,
                "owner",
                "ehall root flow",
                "CREATED",
                0,
            )
        self.supervisor = self.container.extension_supervisor

    async def asyncTearDown(self) -> None:
        with contextlib.suppress(Exception):
            await self.supervisor.stop_all()
        with contextlib.suppress(Exception):
            await self.container.storage.close()
        _safe_rmtree(self.tmp)
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

    # -- helpers -----------------------------------------------------------

    def _confirmation(self, preview: object) -> InstallationConfirmation:
        from datetime import UTC, datetime

        return InstallationConfirmation(
            plan_id=preview.plan_id,  # type: ignore[attr-defined]
            confirmation_nonce=preview.confirmation_nonce,  # type: ignore[attr-defined]
            preview_hash=preview.preview_hash,  # type: ignore[attr-defined]
            actor="owner",
            confirmed_at=datetime.now(UTC),
            accepted_warning=True,
        )

    async def _run(self, operation: object) -> object:
        return await self.supervisor.wait_operation(
            operation.id, timeout_seconds=300  # type: ignore[attr-defined]
        )

    async def _install_and_enable(self) -> None:
        artifact = _zip_artifact(self.tmp / "nju_ehall.zip")
        preview = await self.supervisor.inspect(str(artifact))
        operation = await self.supervisor.begin_install(
            preview.plan_id, self._confirmation(preview)
        )
        result = await self._run(operation)
        self.assertEqual("SUCCEEDED", result.status.value, result.diagnostic_code)
        operation = await self.supervisor.begin_enable(EXTENSION_ID)
        result = await self._run(operation)
        self.assertEqual("SUCCEEDED", result.status.value, result.diagnostic_code)
        await self.container.refresh_tool_registry()

    async def _invoke(self, tool_id: str, arguments: dict[str, object]) -> dict:
        return await self.supervisor.invoke_tool(
            EXTENSION_ID,
            tool_id,
            arguments,
            task_id=TASK_ID,
            run_id="run-f07",
            idempotency_key=f"idem-{tool_id}",
            deadline_seconds=120.0,
        )

    async def _sql(self, statement: str, *parameters: object) -> list[dict]:
        async with self.container.storage.database.connection() as connection:
            rows = await connection.fetch(statement, *parameters)
        return [dict(row) for row in rows]

    async def _gateway_call(self, tool_id: str, arguments: dict[str, object]) -> object:
        descriptor = self.container.tool_registry.snapshot().resolve(
            tool_id, EXTENSION_VERSION
        )
        capabilities = set(descriptor.required_capabilities)
        call = ToolCall(
            tool_id=tool_id,
            tool_version=EXTENSION_VERSION,
            arguments=arguments,
            task_id=TASK_ID,
            target={"origin": TEST_ORIGIN, "transaction_id": "proof.apply"},
            workflow_allowed_tools=frozenset({"ehall.fill_form", "ehall.submit"}),
            granted_capabilities=frozenset(capabilities),
        )
        first = await self.container.tool_gateway.invoke(call)
        self.assertEqual(first.kind, ToolOutcomeKind.APPROVAL_REQUIRED)
        record = await self.container.approvals.get(first.approval_id or "")
        await self.container.approvals.approve(
            first.approval_id or "", nonce=record.nonce, actor_id="owner"
        )
        return await self.container.tool_gateway.invoke(
            ToolCall(
                tool_id=tool_id,
                tool_version=EXTENSION_VERSION,
                arguments=arguments,
                task_id=TASK_ID,
                target={"origin": TEST_ORIGIN, "transaction_id": "proof.apply"},
                workflow_allowed_tools=frozenset({"ehall.fill_form", "ehall.submit"}),
                granted_capabilities=frozenset(capabilities),
                approval_id=first.approval_id,
            )
        )

    # -- tests -------------------------------------------------------------

    async def test_full_supervised_flow_through_real_worker_and_gateway(self) -> None:
        await self._install_and_enable()

        discovered = await self._invoke("ehall.discover_apps", {"purpose": "办理在读证明"})
        self.assertEqual(discovered["outcome"], "SUCCEEDED")
        apps = {
            item["name"]: item
            for item in discovered["output"]["apps"]  # type: ignore[index]
        }
        self.assertTrue(apps["退课申请"]["prohibited"])
        self.assertTrue(apps["在读证明申请"]["supported"])
        session_id = discovered["output"]["session_id"]

        inspected = await self._invoke(
            "ehall.inspect_transaction",
            {"session_id": session_id, "app_path": TEST_APP_PATH},
        )
        self.assertEqual(inspected["outcome"], "SUCCEEDED")
        fingerprint = inspected["output"]["page_fingerprint"]
        self.assertEqual(len(fingerprint), 64)

        prepared = await self._invoke(
            "ehall.prepare_preview",
            {
                "session_id": session_id,
                "transaction_id": "proof.apply",
                "values": {
                    "reason": "需要办理在读证明",
                    "phone": "13800000000",
                    "delivery": "paper",
                },
            },
        )
        self.assertEqual(prepared["outcome"], "SUCCEEDED")
        plan = prepared["output"]["plan"]
        self.assertEqual(plan["expected_page_fingerprint"], fingerprint)

        # The extension migration ran in its own schema through the host proxy.
        schemas = await self._sql(
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name = $1",
            NAMESPACE,
        )
        self.assertEqual(len(schemas), 1)
        rows = await self._sql(
            "SELECT transaction_id, status FROM ext_nju_2e_ehall.ehall_transactions"
        )
        self.assertEqual(rows[0]["transaction_id"], "proof.apply")

        filled = await self._gateway_call(
            "ehall.fill_form",
            {
                "session_id": session_id,
                "adapter_id": plan["adapter_id"],
                "adapter_version": plan["adapter_version"],
                "transaction_id": plan["transaction_id"],
                "app_id": plan["app_id"],
                "expected_origin": plan["expected_origin"],
                "expected_page_fingerprint": plan["expected_page_fingerprint"],
                "consequences": plan["consequences"],
                "fields": plan["fields"],
                "attachments": [],
            },
        )
        self.assertEqual(filled.kind, ToolOutcomeKind.SUCCESS, filled.message)
        preview = filled.result
        record = await self._sql(
            "SELECT state, preview_hash FROM browser_sessions WHERE session_id = $1",
            session_id,
        )
        self.assertEqual(record[0]["state"], BrowserSessionState.PREVIEW_READY.value)
        self.assertEqual(record[0]["preview_hash"], preview["canonical_payload_hash"])

        # Success is proven by the tracking page, not by DOM text.
        self.companion.receipt_appears_after_click = "NJU-2026-0001"
        submitted = await self._gateway_call(
            "ehall.submit",
            {
                "session_id": session_id,
                "preview_hash": preview["canonical_payload_hash"],
                "preview_nonce": preview["nonce"],
                "action_id": "proof.submit",
            },
        )
        self.assertEqual(submitted.kind, ToolOutcomeKind.SUCCESS)
        self.assertEqual(submitted.result["state"], "SUCCEEDED")
        receipt = await self._sql(
            "SELECT outcome, receipt FROM browser_sessions WHERE session_id = $1",
            session_id,
        )
        self.assertEqual(receipt[0]["outcome"], "SUCCEEDED")
        self.assertIn("NJU-2026-", str(receipt[0]["receipt"]))

    async def test_open_transaction_routes_through_the_real_gateway(self) -> None:
        await self._install_and_enable()
        discovered = await self._invoke("ehall.discover_apps", {"purpose": "办理在读证明"})
        self.assertEqual(discovered["outcome"], "SUCCEEDED")
        session_id = discovered["output"]["session_id"]
        descriptor = self.container.tool_registry.snapshot().resolve(
            "ehall.open_transaction", EXTENSION_VERSION
        )
        call = ToolCall(
            tool_id="ehall.open_transaction",
            tool_version=EXTENSION_VERSION,
            arguments={
                "session_id": session_id,
                "adapter_id": "nju.ehall.proof",
                "transaction_id": "proof.apply",
            },
            task_id=TASK_ID,
            target={"origin": TEST_ORIGIN, "transaction_id": "proof.apply"},
            workflow_allowed_tools=frozenset({"ehall.open_transaction"}),
            granted_capabilities=frozenset(descriptor.required_capabilities),
        )
        result = await self.container.tool_gateway.invoke(call)
        self.assertEqual(result.kind, ToolOutcomeKind.SUCCESS, result.message)
        self.assertEqual(result.result["path"], TEST_APP_PATH)  # type: ignore[index]
        self.assertEqual(len(result.result["page_fingerprint"]), 64)  # type: ignore[index]
        # The bound landing page is inspectable as usual.
        inspected = await self._invoke(
            "ehall.inspect_transaction",
            {"session_id": session_id, "app_path": TEST_APP_PATH},
        )
        self.assertEqual(inspected["outcome"], "SUCCEEDED")

    async def test_unknown_submit_reconciles_read_only(self) -> None:
        await self._install_and_enable()
        # The site accepts the write, but the Companion reply is malformed.
        # Broker -> Gateway -> PostgreSQL Outbox must preserve UNKNOWN and
        # reconcile read-only without issuing a second click.
        self.companion.malformed_click_result = True
        discovered = await self._invoke("ehall.discover_apps", {"purpose": "x"})
        self.assertEqual(discovered["outcome"], "SUCCEEDED", discovered)
        session_id = discovered["output"]["session_id"]
        inspected = await self._invoke(
            "ehall.inspect_transaction",
            {"session_id": session_id, "app_path": TEST_APP_PATH},
        )
        self.assertEqual(inspected["outcome"], "SUCCEEDED", inspected)
        prepared = await self._invoke(
            "ehall.prepare_preview",
            {
                "session_id": session_id,
                "transaction_id": "proof.apply",
                "values": {
                    "reason": "需要办理在读证明",
                    "phone": "13800000000",
                    "delivery": "paper",
                },
            },
        )
        plan = prepared["output"]["plan"]
        filled = await self._gateway_call(
            "ehall.fill_form",
            {
                "session_id": session_id,
                "adapter_id": plan["adapter_id"],
                "adapter_version": plan["adapter_version"],
                "transaction_id": plan["transaction_id"],
                "app_id": plan["app_id"],
                "expected_origin": plan["expected_origin"],
                "expected_page_fingerprint": plan["expected_page_fingerprint"],
                "consequences": plan["consequences"],
                "fields": plan["fields"],
                "attachments": [],
            },
        )
        self.assertEqual(filled.kind, ToolOutcomeKind.SUCCESS, filled.message)
        preview = filled.result
        submitted = await self._gateway_call(
            "ehall.submit",
            {
                "session_id": session_id,
                "preview_hash": preview["canonical_payload_hash"],
                "preview_nonce": preview["nonce"],
                "action_id": "proof.submit",
            },
        )
        self.assertEqual(submitted.kind, ToolOutcomeKind.OUTCOME_UNKNOWN)
        state = await self._sql(
            "SELECT state FROM browser_sessions WHERE session_id = $1", session_id
        )
        self.assertEqual(state[0]["state"], BrowserSessionState.UNKNOWN.value)
        intents = await self._sql(
            "SELECT state FROM side_effect_intents "
            "WHERE tool_id = 'ehall.submit' ORDER BY created_at DESC LIMIT 1"
        )
        self.assertEqual(intents[0]["state"], "UNKNOWN")
        # Automated retries must never reach the transport.
        retry = await self._gateway_call(
            "ehall.submit",
            {
                "session_id": session_id,
                "preview_hash": preview["canonical_payload_hash"],
                "preview_nonce": preview["nonce"],
                "action_id": "proof.submit",
            },
        )
        self.assertEqual(retry.kind, ToolOutcomeKind.FAILED)
        self.assertEqual(len(self.companion.click_calls), 1)

        self.companion.tracking_matches = ["NJU-2026-0009"]
        reconciled = await self._invoke("ehall.reconcile", {"session_id": session_id})
        self.assertEqual(reconciled["outcome"], "SUCCEEDED")
        self.assertEqual(reconciled["output"]["result"], "MATCHED")
        self.assertEqual(reconciled["output"]["reference"], "NJU-2026-0009")

    async def test_disable_refuses_tools_and_recover_restores_them(self) -> None:
        await self._install_and_enable()
        discovered = await self._invoke("ehall.discover_apps", {"purpose": "x"})
        self.assertEqual(discovered["outcome"], "SUCCEEDED")
        operation = await self.supervisor.begin_disable(EXTENSION_ID)
        result = await self._run(operation)
        self.assertEqual("SUCCEEDED", result.status.value)
        from personal_assistant.core.extensions.errors import ExtensionOperationError

        with self.assertRaises(ExtensionOperationError):
            await self._invoke("ehall.discover_apps", {"purpose": "x"})
        operation = await self.supervisor.begin_enable(EXTENSION_ID)
        result = await self._run(operation)
        self.assertEqual("SUCCEEDED", result.status.value, result.diagnostic_code)
        await self.container.refresh_tool_registry()
        again = await self._invoke("ehall.discover_apps", {"purpose": "x"})
        self.assertEqual(again["outcome"], "SUCCEEDED")

    async def test_uninstall_removes_code_and_retains_data(self) -> None:
        await self._install_and_enable()
        discovered = await self._invoke("ehall.discover_apps", {"purpose": "x"})
        self.assertEqual(discovered["outcome"], "SUCCEEDED", discovered)
        session_id = discovered["output"]["session_id"]
        inspected = await self._invoke(
            "ehall.inspect_transaction",
            {"session_id": session_id, "app_path": TEST_APP_PATH},
        )
        self.assertEqual(inspected["outcome"], "SUCCEEDED", inspected)
        prepared = await self._invoke(
            "ehall.prepare_preview",
            {
                "session_id": session_id,
                "transaction_id": "proof.apply",
                "values": {
                    "reason": "需要办理在读证明",
                    "phone": "13800000000",
                    "delivery": "paper",
                },
            },
        )
        self.assertEqual(prepared["outcome"], "SUCCEEDED", prepared)
        operation = await self.supervisor.begin_disable(EXTENSION_ID)
        await self._run(operation)
        operation = await self.supervisor.begin_uninstall(EXTENSION_ID)
        result = await self._run(operation)
        self.assertEqual("SUCCEEDED", result.status.value, result.diagnostic_code)
        record = await self.supervisor.record(EXTENSION_ID)
        self.assertEqual("UNINSTALLED", record.state.value)  # type: ignore[union-attr]
        schemas = await self._sql(
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name = $1",
            NAMESPACE,
        )
        self.assertEqual(len(schemas), 1, "business data must be retained")


if __name__ == "__main__":
    unittest.main()
