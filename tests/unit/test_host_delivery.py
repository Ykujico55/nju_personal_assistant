from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
from xml.etree import ElementTree

from personal_assistant.core.platform import ProcessSpec


class HostDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_supervisor_rejects_credentials_and_shell_execution(self) -> None:
        from personal_assistant.infrastructure.platform.windows.operations import (
            WindowsProcessSupervisor,
        )

        supervisor = WindowsProcessSupervisor()
        for spec in (
            ProcessSpec(argv=("python", "--password", "synthetic-secret"), cwd=Path.cwd()),
            ProcessSpec(
                argv=("python", "safe.py"),
                cwd=Path.cwd(),
                environment={"PA_DATABASE_URL": "postgresql://u:synthetic-secret@localhost/db"},
            ),
            ProcessSpec(argv=("cmd", "/c", "echo", "x"), cwd=Path.cwd()),
        ):
            with self.subTest(spec=spec), self.assertRaises(ValueError) as failure:
                await supervisor.start(spec)
            self.assertNotIn("synthetic-secret", str(failure.exception))

    async def test_autostart_xml_is_user_logon_only_and_does_not_save_passwords(self) -> None:
        from personal_assistant.infrastructure.platform.windows.operations import task_xml

        raw = task_xml(
            ("C:/Program Files/Python/python.exe", "-m", "personal_assistant.host_entry"),
            cwd=Path("C:/assistant"),
            user_id="fixture-user",
        )
        root = ElementTree.fromstring(raw)
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        self.assertEqual(
            "InteractiveToken", root.findtext("t:Principals/t:Principal/t:LogonType", namespaces=ns)
        )
        self.assertIsNotNone(root.find("t:Triggers/t:LogonTrigger", ns))
        self.assertIsNone(root.find("t:Triggers/t:BootTrigger", ns))
        self.assertEqual(
            "IgnoreNew", root.findtext("t:Settings/t:MultipleInstancesPolicy", namespaces=ns)
        )
        self.assertNotIn("Password", raw)

    async def test_service_registration_never_overwrites_existing_task(self) -> None:
        from personal_assistant.infrastructure.platform.windows.operations import (
            WindowsServiceManager,
        )

        runner = AsyncMock(return_value=(0, "existing"))
        manager = WindowsServiceManager(runner=runner, user_id="fixture-user", cwd=Path.cwd())
        with self.assertRaises(ValueError):
            await manager.install(
                "assistant", ("python.exe", "-m", "personal_assistant.host_entry")
            )
        self.assertFalse(any("/Create" in call.args[0] for call in runner.await_args_list))

    async def test_file_watch_observes_changes_and_does_not_follow_symlink_roots(self) -> None:
        from personal_assistant.infrastructure.platform.windows.operations import WindowsFileWatcher

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            watcher = WindowsFileWatcher(interval=0.01)
            iterator = watcher.watch([root])
            import asyncio

            pending = asyncio.create_task(anext(iterator))
            await asyncio.sleep(0.05)
            (root / "fixture.txt").write_text("one", encoding="utf-8")
            change = await asyncio.wait_for(pending, timeout=2)
            self.assertEqual(root / "fixture.txt", change.path)
            self.assertEqual("created", change.kind)
            await iterator.aclose()

    async def test_freeze_is_persistent_and_checked_by_r2_policy(self):
        from personal_assistant.core.tools import ToolPolicy
        from personal_assistant.core.tools.policy import ToolPolicyError
        from personal_assistant.domain import RiskLevel, ToolCall, ToolDescriptor
        from personal_assistant.infrastructure.operations.control import HostControl

        with tempfile.TemporaryDirectory() as directory:
            control = HostControl(Path(directory))
            control.freeze()
            reopened = HostControl(Path(directory))
            self.assertTrue(reopened.frozen())
            descriptor = ToolDescriptor(
                id="fixture.send",
                version="1",
                extension_id="fixture",
                extension_version="1",
                risk=RiskLevel.EXTERNAL_WRITE,
                input_schema={},
                output_schema={},
            )
            call = ToolCall(
                tool_id="fixture.send",
                tool_version="1",
                task_id="t",
                arguments={},
                workflow_allowed_tools=frozenset({"fixture.send"}),
            )
            with self.assertRaises(ToolPolicyError):
                ToolPolicy(frozen_probe=reopened.frozen).check(descriptor, call)
            control.resume()
            ToolPolicy(frozen_probe=reopened.frozen).check(descriptor, call)

    async def test_desktop_session0_refuses_before_any_browser_call(self):
        from unittest.mock import patch

        from personal_assistant.infrastructure.platform.windows.desktop import (
            WindowsDesktopInteraction,
        )

        client = AsyncMock()
        desktop = WindowsDesktopInteraction(
            client,
            allowed_origins=("https://fixture.example",),
            task_id="t",
            extension_id="fixture",
        )
        with (
            patch(
                "personal_assistant.infrastructure.platform.windows.desktop.interactive_session",
                return_value=False,
            ),
            self.assertRaises(ValueError),
        ):
            await desktop.request_visible_browser("https://fixture.example/", "login")
        client.create_session.assert_not_called()

    async def test_database_secret_is_resolved_inside_host_connect_without_migrations(self):
        from unittest.mock import patch

        from personal_assistant.infrastructure.database import (
            PostgresAdapterConfig,
            PostgresDatabase,
        )
        from personal_assistant.infrastructure.memory.secrets import InMemorySecretStore

        store = InMemorySecretStore()
        handle = await store.put(
            name="fixture",
            kind="database_dsn",
            value="postgresql://user:synthetic@localhost/fixture_test",
        )
        database = PostgresDatabase(PostgresAdapterConfig("postgresql://unused@localhost/unused"))
        database.use_secret_reference(handle, store)
        pool = AsyncMock()
        with (
            patch(
                "personal_assistant.infrastructure.database.connection.asyncpg.create_pool",
                new=AsyncMock(return_value=pool),
            ) as connect,
            patch(
                "personal_assistant.infrastructure.database.connection.run_migrations",
                new=AsyncMock(),
            ) as migrate,
        ):
            await database.connect()
            self.assertEqual(
                "postgresql://user:synthetic@localhost/fixture_test",
                connect.call_args.kwargs["dsn"],
            )
            migrate.assert_not_called()
            await database.close()

    async def test_profile_refuses_raw_credentials_and_unsupported_keys(self):
        from personal_assistant.infrastructure.operations.host import load_profile

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "host.json"
            for text in [
                '{"PA_DATABASE_URL":"postgresql://u:synthetic@localhost/db"}',
                '{"API_KEY":"synthetic"}',
                '{"PA_PUBLIC_HOST":"0.0.0.0"}',
            ]:
                path.write_text(text)
                with self.assertRaises(ValueError):
                    load_profile(path)

    async def test_metadata_logger_discards_body_and_rotates_bounded_files(self):
        from personal_assistant.infrastructure.operations.logging import MetadataLogger

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logger = MetadataLogger(root, max_bytes=200, copies=2)
            for _ in range(20):
                logger.emit(
                    "component.started",
                    component="agent",
                    pid=123,
                    body="synthetic-secret",
                    cookie="synthetic-cookie",
                )
            self.assertLessEqual(len(list(root.glob("*.jsonl*"))), 3)
            for path in root.glob("*.jsonl*"):
                self.assertNotIn("synthetic-secret", path.read_text())
                self.assertNotIn("synthetic-cookie", path.read_text())

    async def test_owned_windows_process_tree_is_closed_with_supervisor(self):
        import asyncio
        import os
        import sys

        from personal_assistant.infrastructure.platform.windows.operations import (
            WindowsProcessSupervisor,
        )
        from tests.integration.test_pwa_form_catalog_worker_f08 import _pid_is_running

        if os.name != "nt":
            self.skipTest("native Windows job object")
        supervisor = WindowsProcessSupervisor()
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "child.pid"
            script = Path(directory) / "parent.py"
            script.write_text(
                "import subprocess,sys,time\nfrom pathlib import Path\ntime.sleep(0.3)\n"
                "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\n"
                "Path(sys.argv[1]).write_text(str(p.pid))\ntime.sleep(30)\n"
            )
            handle = await supervisor.start(
                ProcessSpec(
                    argv=(sys.executable, str(script), str(marker)),
                    cwd=Path.cwd(),
                    stdin_json_rpc=False,
                )
            )
            child = None
            try:
                for _ in range(60):
                    if marker.exists():
                        child = int(marker.read_text())
                        break
                    await asyncio.sleep(0.05)
                self.assertIsNotNone(child)
                await supervisor.terminate(handle, grace_seconds=1)
                for _ in range(20):
                    if not _pid_is_running(child):
                        break
                    await asyncio.sleep(0.05)
                self.assertFalse(_pid_is_running(child))
            finally:
                if await supervisor.is_alive(handle):
                    await supervisor.terminate(handle)
                if child and _pid_is_running(child):
                    os.kill(
                        child, 15
                    )  # Only the child PID written by this owned synthetic fixture.
                    for _ in range(40):
                        if not _pid_is_running(child):
                            break
                        await asyncio.sleep(0.05)
