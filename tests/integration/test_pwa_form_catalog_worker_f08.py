"""F08.3: a separate public API process reads a real enabled form Worker."""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg

from personal_assistant.bootstrap import Container, build_container
from personal_assistant.core.extensions.lifecycle import InstallationConfirmation
from personal_assistant.core.extensions.models import ExtensionState
from personal_assistant.core.extensions.operations import OperationState
from personal_assistant.settings import Settings

BASE_URL = os.getenv("PA_TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "extensions" / "example_echo"
EXTENSION_ID = "example.echo"
FORM_ID = "example.echo_settings"
VERSION = "0.1.0"


def _with_db(url: str, database: str) -> str:
    parts = urlsplit(url.replace("postgresql+asyncpg://", "postgresql://"))
    return urlunsplit((parts.scheme, parts.netloc, "/" + database, "", ""))


def _safe_rmtree(path: Path) -> None:
    if not path.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise AssertionError(f"refusing to delete {path}")
    shutil.rmtree(path, ignore_errors=True)


def _pid_is_running(pid: int) -> bool:
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel32.OpenProcess(0x1000, 0, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_uint32()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            raise OSError(ctypes.get_last_error(), "GetExitCodeProcess failed")
        return exit_code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


@unittest.skipUnless(BASE_URL, "set PA_TEST_DATABASE_URL for real PostgreSQL/Worker")
class PublicFormCatalogWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        base = (BASE_URL or "").replace("postgresql+asyncpg://", "postgresql://")
        if not urlsplit(base).path.lstrip("/").endswith("_test"):
            raise RuntimeError("PA_TEST_DATABASE_URL must name a dedicated *_test database")
        self._admin_dsn = _with_db(base, "postgres")
        self._db_name = f"pa_f08_forms_{uuid.uuid4().hex}"
        admin = await asyncpg.connect(self._admin_dsn)
        try:
            await admin.execute(f'CREATE DATABASE "{self._db_name}"')
        finally:
            await admin.close()
        self.database_url = _with_db(base, self._db_name)
        self.tmp = Path(tempfile.mkdtemp(prefix="pa_f08_forms_"))
        self.marker = self.tmp / "worker-calls.log"
        self.source = self.tmp / "example-source"
        shutil.copytree(EXAMPLE, self.source)
        worker = self.source / "src" / "example_echo" / "worker.py"
        code = worker.read_text("utf-8")
        marker_literal = repr(str(self.marker))
        import_marker = (
            "\nimport os as _os\n"
            "with open(" + marker_literal + ", 'a', encoding='utf-8') as _f:\n"
            "    _f.write(f'import:{_os.getpid()}\\n')\n"
        )
        call_marker = (
            "        with open(" + marker_literal + ", 'a', encoding='utf-8') as _f:\n"
            "            _f.write(f'form.list:{_os.getpid()}\\n')\n"
        )
        code = code.replace(
            "from personal_assistant_sdk.worker import run_stdio_worker",
            "from personal_assistant_sdk.worker import run_stdio_worker" + import_marker,
        )
        code = code.replace(
            "    def forms(self) -> tuple[FormSchemaDescriptor, ...]:\n",
            "    def forms(self) -> tuple[FormSchemaDescriptor, ...]:\n" + call_marker,
        )
        worker.write_text(code, encoding="utf-8")
        self.container: Container | None = build_container(
            Settings(
                environment="test",
                log_level="INFO",
                public_host="127.0.0.1",
                public_port=8000,
                admin_host="127.0.0.1",
                admin_port=8001,
                health_host="127.0.0.1",
                health_port=8010,
                storage_backend="postgres",
                database_url=self.database_url,
                extension_root=self.tmp / "installed",
                artifact_root=self.tmp / "artifacts",
                trust_cloudflare_access=False,
                public_origin=None,
                cf_access_team_domain=None,
                cf_access_aud=None,
            )
        )
        await self.container.storage.startup()

    async def asyncTearDown(self) -> None:
        if self.container is not None:
            await self.container.aclose()
            await self.container.storage.close()
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

    async def test_public_process_calls_real_form_list_and_reaps_worker(self) -> None:
        assert self.container is not None
        supervisor = self.container.extension_supervisor
        preview = await supervisor.inspect(str(self.source))
        self.assertEqual(EXTENSION_ID, preview.extension_id)
        self.assertFalse(self.marker.exists())  # No Worker before explicit install consent.
        install = await supervisor.begin_install(
            preview.plan_id,
            InstallationConfirmation(
                plan_id=preview.plan_id,
                confirmation_nonce=preview.confirmation_nonce,
                preview_hash=preview.preview_hash,
                actor="f08-real-worker-test",
                confirmed_at=datetime.now(UTC),
                accepted_warning=True,
            ),
        )
        installed = await supervisor.wait_operation(install.id, timeout_seconds=180.0)
        self.assertEqual(OperationState.SUCCEEDED, installed.status, installed)
        enable = await supervisor.begin_enable(EXTENSION_ID)
        enabled = await supervisor.wait_operation(enable.id, timeout_seconds=180.0)
        self.assertEqual(OperationState.SUCCEEDED, enabled.status, enabled)
        record = await supervisor.record(EXTENSION_ID)
        self.assertIsNotNone(record)
        assert record is not None and record.manifest is not None
        self.assertEqual(ExtensionState.ENABLED, record.state)
        self.assertEqual((FORM_ID,), record.manifest.forms)
        self.assertEqual(VERSION, record.manifest.version)

        await self.container.aclose()
        await self.container.storage.close()
        self.container = None
        previous_calls = self.marker.read_text("utf-8").splitlines()

        child = (
            "import json\n"
            "from fastapi.testclient import TestClient\n"
            "from personal_assistant.app import app\n"
            "with TestClient(app) as client:\n"
            "    response = client.get('/api/v1/forms')\n"
            "    print('F08_PUBLIC_FORMS=' + json.dumps({'status': response.status_code, "
            "'body': response.json()}), flush=True)\n"
        )
        environment = os.environ.copy()
        environment.update(
            PA_ENVIRONMENT="test",
            PA_STORAGE_BACKEND="postgres",
            PA_DATABASE_URL=self.database_url,
            PA_EXTENSION_ROOT=str(self.tmp / "installed"),
            PA_ARTIFACT_ROOT=str(self.tmp / "artifacts"),
            PA_TRUST_CLOUDFLARE_ACCESS="false",
        )
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", child],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        replies = [
            line.removeprefix("F08_PUBLIC_FORMS=")
            for line in result.stdout.splitlines()
            if line.startswith("F08_PUBLIC_FORMS=")
        ]
        self.assertEqual(1, len(replies), result.stdout)
        response = json.loads(replies[0])
        self.assertEqual(200, response["status"])
        self.assertEqual(
            [
                {
                    "extension_id": EXTENSION_ID,
                    "extension_version": VERSION,
                    "id": FORM_ID,
                    "json_schema": {
                        "type": "object",
                        "properties": {"prefix": {"type": "string", "maxLength": 40}},
                        "additionalProperties": False,
                    },
                    "ui_schema": {"prefix": {"ui:placeholder": "Echo: "}},
                }
            ],
            response["body"]["items"],
        )
        new_calls = self.marker.read_text("utf-8").splitlines()[len(previous_calls) :]
        imports = {line.partition(":")[2] for line in new_calls if line.startswith("import:")}
        invoked = {line.partition(":")[2] for line in new_calls if line.startswith("form.list:")}
        self.assertEqual(1, len(imports), new_calls)
        self.assertEqual(imports, invoked, new_calls)
        worker_pid = int(next(iter(imports)))
        self.assertFalse(
            _pid_is_running(worker_pid),
            f"Worker {worker_pid} survived public API exit",
        )
