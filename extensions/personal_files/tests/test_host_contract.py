"""Actual host manifest, frozen registry, Gateway and capability router contracts.

Only this adapter test imports the host. The extension remains SDK-only. All
runtime state and file content are synthetic and isolated in a temporary directory.
"""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from personal_assistant_sdk import InvocationContext, RuntimeContext

from personal_files.worker import PersonalFilesExtension

PLUGIN = Path(__file__).resolve().parents[1]
PROJECT = PLUGIN.parents[1]


def host_module(name):
    original = sys.path[:]
    try:
        sys.path.insert(0, str(PROJECT / "src"))
        return importlib.import_module("personal_assistant." + name)
    finally:
        sys.path[:] = original


ManifestParser = host_module("core.extensions.manifest").ManifestParser
manifest_tool_descriptors = host_module("core.extensions.descriptors").manifest_tool_descriptors
ToolRegistry = host_module("core.tools.registry").ToolRegistry
schemas = host_module("core.tools.schemas")
ToolGateway = host_module("core.tools.gateway").ToolGateway
ToolPolicy = host_module("core.tools.policy").ToolPolicy
approvals = host_module("core.approvals")
CapabilityRoutingExecutor = host_module(
    "infrastructure.tools.capability_router"
).CapabilityRoutingExecutor
domain = host_module("domain")


class HostSchemaTests(unittest.TestCase):
    def test_manifest_schemas_accept_each_published_command_after_registry_freeze(self):
        manifest = ManifestParser().parse(PLUGIN)
        registry = ToolRegistry()
        registry.publish(manifest_tool_descriptors(manifest))
        examples = {
            "files.roots": {},
            "files.list": {"root_key": "work", "path": "", "offset": 0, "limit": 100},
            "files.read": {"root_key": "work", "path": "sample.txt", "offset": 0, "limit": 4},
            "files.preview": {
                "operation": "write",
                "root_key": "work",
                "path": "new.txt",
                "expected_sha256": None,
                "content": "synthetic",
            },
            "files.write": {"plan_id": "fileplan_123", "plan_hash": "0" * 64},
            "files.status": {"plan_id": "fileplan_123"},
        }
        for tool_id, arguments in examples.items():
            descriptor = registry.snapshot().resolve(tool_id, manifest.version)
            with self.subTest(tool=tool_id):
                schemas.validate_json_schema(arguments, descriptor.input_schema)
                with self.assertRaises(schemas.SchemaValidationError):
                    schemas.validate_json_schema(
                        {**arguments, "confirmed": True}, descriptor.input_schema
                    )
                schemas.validate_json_schema({"status": "fixture"}, descriptor.output_schema)
        config = json.loads((PLUGIN / manifest.config_schema).read_text())
        valid = {
            "state_directory": "C:/synthetic/state",
            "roots": [
                {
                    "key": "1.notes",
                    "label": "Notes",
                    "path": "C:/synthetic/root",
                    "mode": "read_only",
                }
            ],
        }
        schemas.validate_json_schema(valid, config)
        with self.assertRaises(schemas.SchemaValidationError):
            schemas.validate_json_schema({**valid, "roots": []}, config)


class WorkerInvoker:
    def __init__(self, worker):
        self.worker = worker
        self.calls = 0

    async def invoke_tool(
        self,
        extension_id,
        tool_id,
        arguments,
        *,
        task_id,
        run_id,
        idempotency_key,
        deadline_seconds,
    ):
        assert extension_id == "personal.files"
        self.calls += 1
        result = await self.worker.invoke(
            tool_id,
            dict(arguments),
            InvocationContext(
                task_id,
                run_id,
                (datetime.now(UTC) + timedelta(seconds=deadline_seconds)).isoformat(),
                idempotency_key,
            ),
        )
        return {"outcome": result.outcome.value, "output": dict(result.output)}


class HostGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.root, self.state = base / "root", base / "state"
        self.root.mkdir()
        self.state.mkdir()
        self.worker = PersonalFilesExtension()
        await self.worker.initialize(
            RuntimeContext(
                "1",
                "personal.files",
                "0.1.0",
                "ext_files",
                non_secret_config={
                    "state_directory": str(self.state),
                    "roots": [
                        {
                            "key": "work",
                            "label": "Work",
                            "path": str(self.root),
                            "mode": "read_write",
                        }
                    ],
                },
            )
        )
        self.addAsyncCleanup(self.worker.shutdown)
        self.manifest = ManifestParser().parse(PLUGIN)
        registry = ToolRegistry()
        registry.publish(manifest_tool_descriptors(self.manifest))
        self.invoker = WorkerInvoker(self.worker)
        self.gateway = ToolGateway(
            registry=registry,
            policy=ToolPolicy(frozen_probe=lambda: False),
            approvals=approvals.ApprovalService(approvals.InMemoryApprovalRepository()),
            executor=CapabilityRoutingExecutor(extension_router=self.invoker),
        )

    async def call(self, name, args, key):
        return await self.gateway.invoke(
            domain.ToolCall(
                tool_id=name,
                tool_version=self.manifest.version,
                arguments=args,
                task_id="fixture",
                workflow_allowed_tools=frozenset({name}),
                idempotency_key=key,
            )
        )

    async def success(self, name, args, key):
        result = await self.call(name, args, key)
        self.assertEqual(domain.ToolOutcomeKind.SUCCESS, result.kind, result.message)
        return result.result

    async def test_all_six_tools_execute_through_actual_gateway_and_owner_boundary_holds(self):
        roots = await self.success("files.roots", {}, "roots")
        self.assertEqual("work", roots["roots"][0]["key"])
        await self.success(
            "files.list", {"root_key": "work", "path": "", "offset": 0, "limit": 100}, "list"
        )
        plan = await self.success(
            "files.preview",
            {
                "operation": "write",
                "root_key": "work",
                "path": "a.txt",
                "expected_sha256": None,
                "content": "synthetic content",
            },
            "preview",
        )
        command = {key: plan[key] for key in ("plan_id", "plan_hash")}
        await self.success("files.write", command, "commit")
        self.assertEqual("synthetic content", (self.root / "a.txt").read_text())
        body = await self.success(
            "files.read",
            {"root_key": "work", "path": "a.txt", "offset": 0, "limit": 262144},
            "read",
        )
        self.assertEqual("synthetic content", body["content"])
        await self.success("files.status", {"plan_id": plan["plan_id"]}, "status")
        deletion = await self.success(
            "files.preview",
            {
                "operation": "delete",
                "root_key": "work",
                "path": "a.txt",
                "expected_sha256": body["sha256"],
            },
            "delete-preview",
        )
        denied = await self.call(
            "files.write", {key: deletion[key] for key in ("plan_id", "plan_hash")}, "delete-commit"
        )
        self.assertEqual(domain.ToolOutcomeKind.USER_ACTION_REQUIRED, denied.kind)
        self.assertTrue((self.root / "a.txt").exists())

    async def test_invalid_shape_is_host_rejected_and_conditional_input_never_touches_disk(self):
        invalid_shapes = (
            ("files.roots", {"confirmed": True}),
            ("files.list", {"root_key": "work", "limit": 201}),
            ("files.read", {"root_key": "work", "path": "a.txt", "offset": True}),
            (
                "files.preview",
                {
                    "operation": "write",
                    "root_key": "work",
                    "path": "a.txt",
                    "expected_sha256": None,
                    "content": "x",
                    "confirmed": True,
                },
            ),
            ("files.write", {"plan_id": "p", "plan_hash": "bad"}),
            ("files.status", {}),
        )
        for index, (name, args) in enumerate(invalid_shapes):
            with self.subTest(tool=name):
                result = await self.call(name, args, f"invalid-{index}")
                self.assertEqual(domain.ToolOutcomeKind.FAILED, result.kind)
        self.assertEqual(0, self.invoker.calls)
        for index, args in enumerate(
            (
                {
                    "operation": "write",
                    "root_key": "work",
                    "path": "a.txt",
                    "expected_sha256": None,
                },
                {
                    "operation": "delete",
                    "root_key": "work",
                    "path": "a.txt",
                    "expected_sha256": "0" * 64,
                    "content": "not permitted",
                },
                {
                    "operation": "delete",
                    "root_key": "work",
                    "path": "a.txt",
                    "expected_sha256": None,
                },
            )
        ):
            result = await self.call("files.preview", args, f"conditional-{index}")
            self.assertEqual(domain.ToolOutcomeKind.FAILED, result.kind)
        self.assertEqual([], list(self.root.iterdir()))
        self.assertEqual([], list(self.state.iterdir()))

    async def test_mkdir_then_edit_child_through_gateway_is_nonrecursive_and_replay_safe(self):
        parent = self.root / "existing"
        parent.mkdir()
        plan = await self.success(
            "files.preview",
            {
                "operation": "mkdir",
                "root_key": "work",
                "path": "existing/notes",
                "expected_sha256": None,
            },
            "mkdir-preview",
        )
        self.assertFalse((parent / "notes").exists())
        self.assertEqual("mkdir", plan["operation"])
        command = {key: plan[key] for key in ("plan_id", "plan_hash")}
        receipt = await self.success("files.write", command, "mkdir-commit")
        self.assertTrue((parent / "notes").is_dir())
        self.assertEqual("mkdir", receipt["operation"])
        file_plan = await self.success(
            "files.preview",
            {
                "operation": "write",
                "root_key": "work",
                "path": "existing/notes/note.md",
                "expected_sha256": None,
                "content": "generated note",
            },
            "note-preview",
        )
        await self.success(
            "files.write", {key: file_plan[key] for key in ("plan_id", "plan_hash")}, "note-write"
        )
        self.assertEqual("generated note", (parent / "notes" / "note.md").read_text())
        replay = await self.success("files.write", command, "mkdir-commit")
        self.assertEqual(receipt, replay)
        self.assertTrue((parent / "notes" / "note.md").is_file())
        for index, path in enumerate(("existing/notes", "missing/child")):
            denied = await self.call(
                "files.preview",
                {
                    "operation": "mkdir",
                    "root_key": "work",
                    "path": path,
                    "expected_sha256": None,
                },
                f"mkdir-refused-{index}",
            )
            self.assertNotEqual(domain.ToolOutcomeKind.SUCCESS, denied.kind)
        self.assertFalse((self.root / "missing").exists())
        directory_delete = await self.call(
            "files.preview",
            {
                "operation": "delete",
                "root_key": "work",
                "path": "existing/notes",
                "expected_sha256": "0" * 64,
            },
            "directory-delete",
        )
        self.assertNotEqual(domain.ToolOutcomeKind.SUCCESS, directory_delete.kind)
        self.assertTrue((parent / "notes" / "note.md").is_file())

    async def test_invalid_mkdir_never_creates_directory_or_persistent_plan(self):
        parent = self.root / "existing"
        parent.mkdir()
        valid = {
            "operation": "mkdir",
            "root_key": "work",
            "path": "existing/notes",
            "expected_sha256": None,
        }
        for index, extra in enumerate(
            (
                {"confirmed": True},
                {"path": 42},
                {"expected_sha256": "0" * 64},
                {"content": ""},
                {"content": None},
            )
        ):
            denied = await self.call("files.preview", {**valid, **extra}, f"bad-mkdir-{index}")
            self.assertEqual(domain.ToolOutcomeKind.FAILED, denied.kind)
        self.assertEqual([], list(parent.iterdir()))
        self.assertEqual([], list(self.state.iterdir()))
