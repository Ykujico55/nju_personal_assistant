"""Real host-process coordination; synthetic workers, no installed extensions or user data."""
import asyncio
import os
import sys
from pathlib import Path

import pytest

from personal_assistant.infrastructure.extensions.config_store import FileExtensionConfigStore

CHILD = r"""
import asyncio, json, sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from personal_assistant.core.extensions.manifest import ManifestParser
from personal_assistant.core.extensions.models import ExtensionRecord, ExtensionState
from personal_assistant.infrastructure.extensions.config_store import FileExtensionConfigStore
from personal_assistant.infrastructure.extensions.processes import ProcessRuntimeSupervisor
class Worker:
    def __init__(self, manifest):
        self.manifest = manifest
        self.client = SimpleNamespace(running=False)
        self.config = None
    async def start(self, *, non_secret_config, timeout_seconds):
        self.config = non_secret_config
        self.client.running = True
    async def health(self, *, timeout_seconds):
        return {'healthy': True}
    async def close(self):
        self.client.running = False
async def main():
    root = Path(sys.argv[1])
    manifest = ManifestParser().parse(Path('extensions/example_echo'))
    record = ExtensionRecord(manifest, 'sha256:' + '0'*64, ExtensionState.ENABLED)
    runtime = ProcessRuntimeSupervisor(config_store=FileExtensionConfigStore(root))
    workers = []
    def build(*args, **kwargs):
        worker = Worker(manifest)
        workers.append(worker)
        return worker
    with patch('personal_assistant.infrastructure.extensions.processes._build_worker', build):
        try:
            await runtime.start(record)
            print('READY', flush=True)
            while not (root / 'stop').exists():
                await asyncio.sleep(.05)
            assert workers[-1].config['prefix'] == 'new-public-fixture'
            assert len(workers) == 2
        finally:
            await runtime.stop_all()
asyncio.run(main())
"""


@pytest.mark.asyncio
async def test_save_then_apply_synchronizes_two_real_host_processes(tmp_path: Path):
    store = FileExtensionConfigStore(tmp_path)
    extension_id = "example.echo"
    await store.save(extension_id, {"prefix": "old-public-fixture"})
    first = await store.status(extension_id)
    await store.apply(extension_id, first["saved_digest"])
    children = []
    try:
        for _ in range(2):
            child = await asyncio.create_subprocess_exec(
                sys.executable, "-Xutf8", "-B", "-c", CHILD, str(tmp_path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            children.append(child)
            line = await asyncio.wait_for(child.stdout.readline(), 30)
            if line.strip() != b"READY":
                _, error = await child.communicate()
                pytest.fail(error.decode(errors="replace"))
        initial = await store.status(extension_id)
        assert initial["state"] == "APPLIED" and len(initial["runtimes"]) == 2
        await store.save(extension_id, {"prefix": "new-public-fixture"})
        saved = await store.status(extension_id)
        assert saved["state"] == "PENDING"
        assert all(r["loaded_digest"] == initial["desired_digest"] for r in saved["runtimes"])
        await store.apply(extension_id, saved["saved_digest"])
        async with asyncio.timeout(15):
            while True:
                current = await store.status(extension_id)
                if current["state"] == "APPLIED":
                    break
                await asyncio.sleep(.05)
        assert len(current["runtimes"]) == 2
        assert all(r["loaded_digest"] == saved["saved_digest"] for r in current["runtimes"])
        (tmp_path / "stop").touch()
        for child in children:
            _, error = await asyncio.wait_for(child.communicate(), 10)
            assert child.returncode == 0, error.decode(errors="replace")
        assert (await store.status(extension_id))["runtimes"] == []
        assert (await store.status(extension_id))["state"] == "PENDING"
    finally:
        for child in children:
            if child.returncode is None:
                child.terminate()
            await child.communicate()
