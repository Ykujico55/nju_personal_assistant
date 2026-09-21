"""F07 extension tests: nju.ehall business logic with host capability doubles."""

from __future__ import annotations

import hashlib
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from nju_ehall.worker import MIGRATIONS, EhallExtension
from personal_assistant_sdk import (
    PROTOCOL_VERSION,
    InvocationContext,
    Outcome,
    RuntimeContext,
)

from personal_assistant.core.browser import scan_text_for_prohibited_terms

EXTENSION_ID = "nju.ehall"
EXTENSION_VERSION = "0.1.0"
ORIGIN = "https://ehall.test.example"


def snapshot(
    *,
    authenticated: bool = True,
    fingerprint: str = "a" * 64,
    risk: str = "EXTERNAL_WRITE",
    categories: Sequence[str] = (),
    fields: Sequence[Mapping[str, Any]] = (),
    links: Sequence[Mapping[str, str]] = (),
    actions: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "session_id": "brs_test",
        "url": f"{ORIGIN}/apps/proof",
        "origin": ORIGIN,
        "path": "/apps/proof",
        "title": "在读证明申请",
        "fingerprint": fingerprint,
        "captured_at": "2026-09-19T12:00:00+00:00",
        "authenticated": authenticated,
        "fields": [dict(item) for item in fields],
        "actions": [
            dict(item)
            for item in (
                actions
                if actions is not None
                else [{"action_id": "proof.submit", "locator": "act:0", "known": True}]
            )
        ],
        "links": [dict(item) for item in links],
        "signals": [],
        "text_digest": "0" * 64,
        "byte_size": 100,
        "truncated": False,
        "risk": risk,
        "risk_categories": list(categories),
        "escalated": bool(categories),
    }


def known_fields(
    reason: str = "", phone: str = "", delivery: str = "paper"
) -> list[dict[str, Any]]:
    return [
        {
            "field_id": "reason",
            "locator": "ctl:0:0",
            "kind": "text",
            "value": reason,
            "required": True,
            "known": True,
        },
        {
            "field_id": "phone",
            "locator": "ctl:0:1",
            "kind": "text",
            "value": phone,
            "required": True,
            "known": True,
        },
        {
            "field_id": "delivery",
            "locator": "ctl:1:0",
            "kind": "select",
            "value": delivery,
            "options": ["paper", "email"],
            "required": True,
            "known": True,
        },
    ]


class FakeBrowser:
    def __init__(self, *, state: str = "AUTHENTICATED") -> None:
        self.state = state
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.adapters: list[Mapping[str, Any]] = []
        self.calls: list[str] = []
        self.records: list[tuple[str, Mapping[str, Any]]] = []
        self.reconciliations: list[tuple[str, Mapping[str, Any] | None]] = []
        self.found_text: str = ""
        self.navigate_snapshot: dict[str, Any] | None = None

    async def session(self, *, task_id: str, purpose: str) -> Mapping[str, Any]:
        self.calls.append("session")
        return {"session_id": "brs_test", "state": self.state, "purpose": purpose}

    async def status(self, session_id: str) -> Mapping[str, Any]:
        self.calls.append("status")
        return {"session_id": session_id, "state": self.state}

    async def register_adapter(self, descriptor: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append("register_adapter")
        self.adapters.append(descriptor)
        return {"adapter_id": descriptor["adapter_id"], "adapter_version": "1.0.0"}

    async def adapters(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.adapters)

    async def snapshot(
        self, session_id: str, *, adapter_id: str = "", transaction_id: str = ""
    ) -> Mapping[str, Any]:
        self.calls.append(f"snapshot:{adapter_id}")
        return self.snapshots.get(adapter_id or "", snapshot())

    async def find_text(self, session_id: str, query: str) -> Mapping[str, Any]:
        self.calls.append("find_text")
        if self.found_text:
            return {"found": True, "count": 1, "excerpt": self.found_text}
        return {"found": False, "count": 0, "excerpt": ""}

    async def navigate(
        self, session_id: str, *, adapter_id: str, transaction_id: str, url: str
    ) -> Mapping[str, Any]:
        self.calls.append(f"navigate:{url}")
        if self.navigate_snapshot is not None:
            return self.navigate_snapshot
        return self.snapshots.get(adapter_id, snapshot())

    async def classify_labels(
        self, labels: Sequence[str], *, forbidden_terms: Sequence[str] = ()
    ) -> tuple[Mapping[str, Any], ...]:
        self.calls.append("classify_labels")
        result = []
        for label in labels:
            matches = scan_text_for_prohibited_terms(label, forbidden_terms)
            result.append(
                {
                    "label": label,
                    "matched": [
                        {"category": item.category.value, "term": item.term} for item in matches
                    ],
                }
            )
        return tuple(result)

    async def record_discovery(self, session_id: str, *, app_count: int) -> Mapping[str, Any]:
        self.calls.append("record_discovery")
        self.records.append(("record_discovery", {"app_count": app_count}))
        return {"session_id": session_id, "state": "DISCOVERED"}

    async def record_preparation(
        self,
        session_id: str,
        *,
        adapter_id: str,
        adapter_version: str,
        app_id: str,
        transaction_id: str,
        page_fingerprint: str,
        planned_fields: int,
    ) -> Mapping[str, Any]:
        self.calls.append("record_preparation")
        self.records.append(
            (
                "record_preparation",
                {
                    "adapter_id": adapter_id,
                    "adapter_version": adapter_version,
                    "transaction_id": transaction_id,
                    "page_fingerprint": page_fingerprint,
                    "planned_fields": planned_fields,
                },
            )
        )
        return {"session_id": session_id, "state": "PREPARING"}

    async def reconcile(self, session_id: str) -> Mapping[str, Any]:
        """Host-driven read-only reconciliation; the host issues the proof."""

        self.calls.append("reconcile")
        if self.found_text:
            import hashlib

            reference = self.found_text.split()[1] if " " in self.found_text else ""
            self.reconciliations.append(("MATCHED", {"reference": reference}))
            return {
                "session_id": session_id,
                "state": "SUCCEEDED",
                "diagnostic_code": "RECONCILED",
                "receipt": {
                    "reference": reference,
                    "issued_by": "host_tracking",
                    "excerpt_sha256": hashlib.sha256(
                        self.found_text.encode("utf-8")
                    ).hexdigest(),
                },
            }
        self.reconciliations.append(("NOT_FOUND", None))
        return {
            "session_id": session_id,
            "state": "UNKNOWN",
            "diagnostic_code": "RECONCILE_NOT_FOUND",
            "receipt": None,
        }

    async def close(self, session_id: str) -> None:
        return None

    async def cancel(self, session_id: str) -> None:
        return None

    async def aclose(self) -> None:
        return None


class FakeData:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(
        self,
        statement: str,
        parameters: Sequence[Any] = (),
        *,
        timeout_seconds: float = 30.0,
    ) -> Mapping[str, Any]:
        self.statements.append(statement)
        if "FROM ehall_transactions" in statement:
            return {
                "rows": [
                    {
                        "transaction_id": "proof.apply",
                        "adapter_id": "nju.ehall.proof",
                        "adapter_version": "1.0.0",
                        "app_id": "nju.ehall.proof",
                        "page_fingerprint": "a" * 64,
                        "status": "PREPARING",
                        "updated_at": "2026-09-19T12:00:00+00:00",
                    }
                ],
                "rowcount": 1,
            }
        return {"rows": [], "rowcount": 1}

    async def transaction(
        self, statements: Sequence[Mapping[str, Any]], *, timeout_seconds: float = 60.0
    ) -> Mapping[str, Any]:
        return {"rows": [], "rowcount": len(statements)}

    async def migrate(
        self, migrations: Sequence[Mapping[str, Any]], *, timeout_seconds: float = 60.0
    ) -> Mapping[str, Any]:
        return {"namespace": "ext_nju_2e_ehall", "applied": [1], "skipped": []}

    async def aclose(self) -> None:
        return None


class ExtensionTestCase(unittest.IsolatedAsyncioTestCase):
    async def make_extension(
        self,
        *,
        browser: FakeBrowser | None = None,
        data: FakeData | None = None,
        configured: bool = True,
        host_browser: bool = True,
    ) -> EhallExtension:
        self.browser = browser or FakeBrowser()
        self.data = data or FakeData()
        config: dict[str, Any] = {}
        if configured:
            config["browser_origin"] = ORIGIN
        runtime = RuntimeContext(
            protocol_version=PROTOCOL_VERSION,
            extension_id=EXTENSION_ID,
            extension_version=EXTENSION_VERSION,
            data_namespace="ext_nju_2e_ehall",
            manifest_schema_hash="sha256:test",
            non_secret_config=config,
            host_data=self.data,
            host_browser=self.browser if host_browser else None,
        )
        extension = EhallExtension()
        await extension.initialize(runtime)
        return extension

    def context(self) -> InvocationContext:
        return InvocationContext(
            task_id="task-1",
            run_id="run-1",
            deadline="2026-09-19T12:00:30+00:00",
            idempotency_key="idem-1",
        )

    def configure_app_page(
        self,
        browser: FakeBrowser,
        *,
        fields: Sequence[Mapping[str, Any]] | None = None,
        **kwargs: Any,
    ) -> None:
        page = snapshot(fields=fields if fields is not None else known_fields(), **kwargs)
        browser.snapshots["nju.ehall.proof"] = page
        browser.navigate_snapshot = None


class ToolContractTests(ExtensionTestCase):
    async def test_tool_set_and_risks(self) -> None:
        extension = await self.make_extension()
        tools = {item.id: item for item in extension.tools()}
        self.assertEqual(
            set(tools),
            {
                "ehall.discover_apps",
                "ehall.inspect_transaction",
                "ehall.prepare_preview",
                "ehall.open_transaction",
                "ehall.fill_form",
                "ehall.submit",
                "ehall.reconcile",
            },
        )
        self.assertEqual(tools["ehall.open_transaction"].risk.value, "INTERNAL_WRITE")
        self.assertEqual(tools["ehall.fill_form"].risk.value, "EXTERNAL_WRITE")
        self.assertEqual(tools["ehall.submit"].risk.value, "EXTERNAL_WRITE")
        self.assertTrue(all(item.risk.value != "PROHIBITED" for item in tools.values()))

    async def test_host_executed_tools_refuse_worker_execution(self) -> None:
        extension = await self.make_extension()
        for tool in ("ehall.open_transaction", "ehall.fill_form", "ehall.submit"):
            result = await extension.invoke(tool, {"session_id": "brs_test"}, self.context())
            self.assertEqual(result.outcome, Outcome.PERMANENT)
            self.assertEqual(result.output["code"], "EHALL_HOST_EXECUTED")

    async def test_missing_configuration_needs_user_action(self) -> None:
        extension = await self.make_extension(configured=False)
        result = await extension.invoke("ehall.discover_apps", {"purpose": "x"}, self.context())
        self.assertEqual(result.outcome, Outcome.NEEDS_USER_ACTION)
        self.assertEqual(result.output["code"], "EHALL_CONFIG_REQUIRED")

    async def test_missing_browser_capability_fails_closed(self) -> None:
        extension = await self.make_extension(host_browser=False)
        result = await extension.invoke("ehall.discover_apps", {"purpose": "x"}, self.context())
        self.assertEqual(result.outcome, Outcome.PERMANENT)
        self.assertEqual(result.output["code"], "EHALL_BROWSER_UNAVAILABLE")


class DiscoveryTests(ExtensionTestCase):
    async def test_login_challenge_needs_the_user(self) -> None:
        browser = FakeBrowser()
        self.configure_app_page(browser, authenticated=False)
        extension = await self.make_extension(browser=browser)
        result = await extension.invoke("ehall.discover_apps", {"purpose": "x"}, self.context())
        self.assertEqual(result.outcome, Outcome.NEEDS_USER_ACTION)
        self.assertEqual(result.output["state"], "WAITING_USER")
        self.assertNotIn("record_discovery", browser.calls)

    async def test_discovery_classifies_prohibited_apps(self) -> None:
        browser = FakeBrowser()
        self.configure_app_page(
            browser,
            links=[
                {"text": "在读证明申请", "path": "/apps/proof"},
                {"text": "退课申请", "path": "/apps/withdraw"},
                {"text": "在线缴费", "path": "/apps/payment"},
            ],
        )
        extension = await self.make_extension(browser=browser)
        result = await extension.invoke("ehall.discover_apps", {"purpose": "x"}, self.context())
        self.assertEqual(result.outcome, Outcome.SUCCEEDED)
        apps = result.output["apps"]
        by_name = {item["name"]: item for item in apps}
        self.assertTrue(by_name["退课申请"]["prohibited"])
        self.assertTrue(by_name["在线缴费"]["prohibited"])
        self.assertFalse(by_name["在读证明申请"]["prohibited"])
        self.assertTrue(by_name["在读证明申请"]["supported"])
        self.assertEqual(result.output["state"], "DISCOVERED")

    async def test_both_adapters_are_registered_and_visible_actions_are_listed(self) -> None:
        browser = FakeBrowser()
        self.configure_app_page(
            browser,
            links=[],
            actions=[
                {"locator": "act:0", "label": "在读证明申请", "kind": "other"},
                {"locator": "act:1", "label": "研究生成绩单打印", "kind": "other"},
                {"locator": "act:2", "label": "在线缴费", "kind": "other"},
            ],
        )
        extension = await self.make_extension(browser=browser)
        result = await extension.invoke("ehall.discover_apps", {"purpose": "x"}, self.context())
        self.assertEqual(result.outcome, Outcome.SUCCEEDED)
        by_name = {item["name"]: item for item in result.output["apps"]}
        self.assertTrue(by_name["在读证明申请"]["supported"])
        self.assertTrue(by_name["研究生成绩单打印"]["supported"])
        self.assertEqual(by_name["在读证明申请"]["path"], "/apps/proof")
        self.assertEqual(by_name["研究生成绩单打印"]["path"], "/apps/transcript")
        self.assertTrue(by_name["在线缴费"]["prohibited"])
        self.assertEqual(
            sorted(item["adapter_id"] for item in browser.adapters),
            ["nju.ehall.proof", "nju.ehall.transcript"],
        )

    async def test_adapter_registered_only_once(self) -> None:
        browser = FakeBrowser()
        self.configure_app_page(browser)
        extension = await self.make_extension(browser=browser)
        await extension.invoke("ehall.discover_apps", {"purpose": "x"}, self.context())
        await extension.invoke("ehall.discover_apps", {"purpose": "x"}, self.context())
        self.assertEqual(
            sorted(item["adapter_id"] for item in browser.adapters),
            ["nju.ehall.proof", "nju.ehall.transcript"],
        )
        self.assertEqual(browser.calls.count("register_adapter"), 2)


class InspectionTests(ExtensionTestCase):
    async def test_supported_transaction_returns_materials_and_fields(self) -> None:
        browser = FakeBrowser()
        self.configure_app_page(browser)
        extension = await self.make_extension(browser=browser)
        result = await extension.invoke(
            "ehall.inspect_transaction",
            {"session_id": "brs_test", "app_path": "/apps/proof"},
            self.context(),
        )
        self.assertEqual(result.outcome, Outcome.SUCCEEDED)
        self.assertEqual(result.output["page_fingerprint"], "a" * 64)
        self.assertEqual(result.output["risk"], "EXTERNAL_WRITE")
        self.assertEqual(len(result.output["fields"]), 3)

    async def test_unsupported_transaction_is_permanent(self) -> None:
        extension = await self.make_extension()
        result = await extension.invoke(
            "ehall.inspect_transaction",
            {"session_id": "brs_test", "app_path": "/apps/withdraw"},
            self.context(),
        )
        self.assertEqual(result.outcome, Outcome.PERMANENT)
        self.assertEqual(result.output["code"], "EHALL_UNSUPPORTED_TRANSACTION")

    async def test_prohibited_page_semantics_refuse_inspection(self) -> None:
        browser = FakeBrowser()
        self.configure_app_page(browser, risk="PROHIBITED", categories=["PAYMENT"])
        extension = await self.make_extension(browser=browser)
        result = await extension.invoke(
            "ehall.inspect_transaction",
            {"session_id": "brs_test", "app_path": "/apps/proof"},
            self.context(),
        )
        self.assertEqual(result.outcome, Outcome.PERMANENT)
        self.assertEqual(result.output["error"], "EHALL_PROHIBITED")


class PreparationTests(ExtensionTestCase):
    async def test_missing_required_materials_need_the_user(self) -> None:
        browser = FakeBrowser()
        self.configure_app_page(browser)
        extension = await self.make_extension(browser=browser)
        result = await extension.invoke(
            "ehall.prepare_preview",
            {"session_id": "brs_test", "transaction_id": "proof.apply", "values": {}},
            self.context(),
        )
        self.assertEqual(result.outcome, Outcome.NEEDS_USER_ACTION)
        self.assertEqual(sorted(result.output["missing"]), ["delivery", "phone", "reason"])
        self.assertNotIn("record_preparation", browser.calls)

    async def test_complete_values_build_the_plan_and_persist(self) -> None:
        browser = FakeBrowser()
        self.configure_app_page(browser)
        extension = await self.make_extension(browser=browser)
        result = await extension.invoke(
            "ehall.prepare_preview",
            {
                "session_id": "brs_test",
                "transaction_id": "proof.apply",
                "values": {
                    "reason": "需要办理在读证明",
                    "phone": "13800000000",
                    "delivery": "paper",
                },
            },
            self.context(),
        )
        self.assertEqual(result.outcome, Outcome.SUCCEEDED)
        plan = result.output["plan"]
        self.assertEqual(plan["expected_origin"], ORIGIN)
        self.assertEqual(plan["expected_page_fingerprint"], "a" * 64)
        self.assertEqual(plan["fields"][0]["source"], "USER_INPUT")
        self.assertIn("record_preparation", browser.calls)
        self.assertTrue(any("ehall_transactions" in item for item in self.data.statements))
        self.assertFalse(result.output["preview"]["authoritative"])

    async def test_prohibited_page_refuses_preparation(self) -> None:
        browser = FakeBrowser()
        self.configure_app_page(browser, risk="PROHIBITED", categories=["REVOCATION"])
        extension = await self.make_extension(browser=browser)
        result = await extension.invoke(
            "ehall.prepare_preview",
            {
                "session_id": "brs_test",
                "transaction_id": "proof.apply",
                "values": {"reason": "x", "phone": "13800000000", "delivery": "paper"},
            },
            self.context(),
        )
        self.assertEqual(result.outcome, Outcome.PERMANENT)
        self.assertEqual(result.output["code"], "EHALL_PROHIBITED")


class ReconciliationTests(ExtensionTestCase):
    async def test_reconcile_reports_the_host_adjudicated_result(self) -> None:
        browser = FakeBrowser(state="UNKNOWN")
        browser.found_text = "回执号 NJU-2026-0007 状态：已受理"
        extension = await self.make_extension(browser=browser)
        result = await extension.invoke(
            "ehall.reconcile", {"session_id": "brs_test"}, self.context()
        )
        self.assertEqual(result.outcome, Outcome.SUCCEEDED)
        self.assertEqual(result.output["result"], "MATCHED")
        self.assertEqual(result.output["reference"], "NJU-2026-0007")
        self.assertEqual(browser.reconciliations[0][0], "MATCHED")
        self.assertEqual(browser.calls[-1], "reconcile")

    async def test_reconcile_never_declares_a_result_itself(self) -> None:
        browser = FakeBrowser(state="UNKNOWN")
        extension = await self.make_extension(browser=browser)
        result = await extension.invoke(
            "ehall.reconcile", {"session_id": "brs_test"}, self.context()
        )
        self.assertEqual(result.outcome, Outcome.SUCCEEDED)
        self.assertEqual(result.output["result"], "NOT_FOUND")
        self.assertEqual(result.output["reference"], "")

    async def test_reconcile_not_found_keeps_unknown(self) -> None:
        browser = FakeBrowser(state="UNKNOWN")
        extension = await self.make_extension(browser=browser)
        result = await extension.invoke(
            "ehall.reconcile", {"session_id": "brs_test"}, self.context()
        )
        self.assertEqual(result.outcome, Outcome.SUCCEEDED)
        self.assertEqual(result.output["result"], "NOT_FOUND")
        self.assertEqual(browser.reconciliations[0][0], "NOT_FOUND")


class SlotTests(ExtensionTestCase):
    async def test_migrations_describe_the_extension_schema(self) -> None:
        extension = await self.make_extension()
        migrations = extension.migrations()
        self.assertEqual([item.version for item in migrations], [1])
        path = Path(__file__).resolve().parents[2] / "extensions" / "nju_ehall" / str(
            migrations[0].path
        )
        self.assertTrue(path.is_file())
        self.assertEqual(
            hashlib.sha256(path.read_bytes()).hexdigest(), migrations[0].checksum
        )
        self.assertEqual(MIGRATIONS[0]["version"], 1)

    async def test_forms_expose_the_transaction_fields(self) -> None:
        extension = await self.make_extension()
        forms = extension.forms()
        self.assertEqual(forms[0].id, "ehall.transaction_form")
        schema = forms[0].json_schema
        self.assertEqual(schema["type"], "object")
        self.assertIn("reason", schema["properties"])
        self.assertEqual(sorted(schema["required"]), ["delivery", "phone", "reason"])

    async def test_workflow_uses_only_declared_tools(self) -> None:
        extension = await self.make_extension()
        workflow = extension.workflows()[0]
        tool_ids = {item.id for item in extension.tools()}
        for step in workflow.steps:
            self.assertIn(step["tool"], tool_ids)
        self.assertEqual(workflow.id, "ehall.supervised_flow")

    async def test_retrieve_returns_extension_owned_evidence(self) -> None:
        from personal_assistant_sdk import ContextQuery

        extension = await self.make_extension()
        evidence = await extension.retrieve(ContextQuery(text="在读证明"))
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].source_id, "ehall:proof.apply")
        self.assertIn("PREPARING", evidence[0].text)


if __name__ == "__main__":
    unittest.main()
