"""Startup diagnostics stay useful without retaining exception text or secrets."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import socket
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from personal_assistant import host_entry
from personal_assistant.infrastructure.operations import host
from personal_assistant.infrastructure.operations.control import HostControl
from personal_assistant.settings import Settings
from tests.unit.test_settings import direct_settings_values

SECRET = "synthetic-secret-do-not-print-987"


def test_invalid_profile_has_fixed_code_and_correlation_id(tmp_path, monkeypatch, capsys):
    profile = tmp_path / "host.json"
    profile.write_text(SECRET)
    monkeypatch.setattr("sys.argv", ["host", "serve", "--profile", str(profile)])
    assert host_entry.main() == 1
    output = capsys.readouterr()
    record = json.loads(output.out)
    assert record["code"] == "HOST_PROFILE_INVALID"
    assert re.fullmatch(r"diag_[0-9a-f]{32}", record["diagnostic_id"])
    assert SECRET not in output.out + output.err


async def test_duplicate_guardian_does_not_freeze_or_change_original_control(tmp_path, monkeypatch):
    settings = replace(Settings(**direct_settings_values()), extension_root=tmp_path / "extensions")
    control = HostControl(tmp_path / "control")
    control.resume()
    control._write("status.json", {"fixture": True})
    before = {p.name: p.read_bytes() for p in control.root.iterdir()}
    lock = host.GuardianLock(control.root / "guardian.lock")
    monkeypatch.setattr(host, "settings_from_profile", lambda *a, **k: settings)
    try:
        with pytest.raises(ValueError) as caught:
            await host.guard(tmp_path / "host.json", tmp_path)
        assert caught.value.code == "HOST_ALREADY_RUNNING"
        assert not control.frozen()
        for name, content in before.items():
            assert (control.root / name).read_bytes() == content
    finally:
        lock.close()


async def test_busy_port_is_named_before_any_child_or_freeze(tmp_path, monkeypatch):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        settings = replace(
            Settings(**direct_settings_values()),
            public_port=listener.getsockname()[1],
            extension_root=tmp_path / "extensions",
        )
        monkeypatch.setattr(host, "settings_from_profile", lambda *a, **k: settings)
        start = AsyncMock(side_effect=RuntimeError(SECRET))
        monkeypatch.setattr(host.WindowsProcessSupervisor, "start", start)
        with pytest.raises(ValueError) as caught:
            await host.guard(tmp_path / "host.json", tmp_path)
        assert caught.value.code == "HOST_PORT_IN_USE"
        assert caught.value.component == "public"
        start.assert_not_called()
        assert not HostControl(tmp_path / "control").frozen()
        assert SECRET not in str(caught.value)


@pytest.mark.parametrize(
    "kind,code",
    [
        ("database", "HOST_DATABASE_CREDENTIAL_UNAVAILABLE"),
        ("model", "HOST_MODEL_CREDENTIAL_UNAVAILABLE"),
    ],
)
async def test_credential_preflight_classifies_without_leaking_value(
    tmp_path, monkeypatch, kind, code
):
    settings = replace(
        Settings(**direct_settings_values()),
        **(
            {"storage_backend": "postgres", "database_secret_handle": "fixture-reference"}
            if kind == "database"
            else {
                "model_remote_secret_handle": "fixture-reference",
                "model_remote_base_url": "https://model.example.test",
                "model_remote_model": "fixture",
            }
        ),
    )
    store = SimpleNamespace(resolve_for_broker=AsyncMock(side_effect=RuntimeError(SECRET)))
    monkeypatch.setattr(host, "build_secret_store", lambda: store)
    with pytest.raises(ValueError) as caught:
        await host.preflight_credentials(settings)
    assert caught.value.code == code
    assert SECRET not in str(caught.value)


async def test_http_lifespan_failure_is_recorded_with_fixed_metadata(tmp_path, monkeypatch, capsys):
    from fastapi import FastAPI

    from personal_assistant.core.extensions.errors import ExtensionOperationError

    @asynccontextmanager
    async def lifespan(app):
        raise ExtensionOperationError("HEALTHCHECK_FAILED", SECRET)
        yield

    app = FastAPI(lifespan=lifespan)
    control = HostControl(tmp_path)

    class Server:
        def __init__(self, config):
            self.application = config.app
            self.should_exit = False
            self.started = False

        async def serve(self):
            # Uvicorn consumes startup failures.
            with contextlib.suppress(ValueError):
                async with self.application.router.lifespan_context(self.application):
                    pass

    monkeypatch.setattr("uvicorn.Server", Server)
    with pytest.raises(ValueError) as caught:
        await asyncio.to_thread(host_entry.run_http_child, app, 8000, "public", control)
    assert caught.value.code == "HOST_RECOVERY_FAILED"
    text = (tmp_path / "diagnostic-public.json").read_text()
    record = json.loads(text)
    assert set(record) == {"code", "diagnostic_id", "component", "pid", "parent_pid"}
    assert record["code"] == "HOST_RECOVERY_FAILED"
    assert SECRET not in text + str(caught.value) + capsys.readouterr().err


def test_child_record_rejects_other_pid_and_non_allowlisted_fields(tmp_path):
    from personal_assistant.infrastructure.operations.diagnostics import read_child_diagnostic

    path = tmp_path / "diagnostic-agent.json"
    record = {
        "code": "HOST_DATABASE_UNAVAILABLE",
        "diagnostic_id": "diag_" + "a" * 32,
        "component": "agent",
        "pid": 42,
    }
    path.write_text(json.dumps(record))
    assert read_child_diagnostic(tmp_path, "agent", 99) is None
    assert read_child_diagnostic(tmp_path, "agent", 42).code == "HOST_DATABASE_UNAVAILABLE"
    for polluted in [
        {**record, "body": SECRET},
        {**record, "code": SECRET},
        {**record, "diagnostic_id": SECRET},
    ]:
        path.write_text(json.dumps(polluted))
        assert read_child_diagnostic(tmp_path, "agent", 42) is None


async def test_guardian_retains_matching_child_diagnostic_after_restart_limit(
    tmp_path, monkeypatch
):
    from personal_assistant.core.platform import ProcessHandle

    settings = replace(Settings(**direct_settings_values()), extension_root=tmp_path / "extensions")
    control = HostControl(tmp_path / "control")
    created = []

    class Supervisor:
        async def start(self, spec):
            name = next(item[6:] for item in spec.argv if item.startswith("child-"))
            pid = 1000 + len(created)
            handle = ProcessHandle("fixture-" + str(pid), pid)
            created.append(handle)
            control._write(
                "diagnostic-" + name + ".json",
                {
                    "code": "HOST_DATABASE_UNAVAILABLE",
                    "diagnostic_id": "diag_" + "b" * 32,
                    "component": name,
                    "pid": pid,
                },
            )
            return handle

        async def is_alive(self, handle):
            return False

        async def terminate(self, handle, **kwargs):
            pass

    async def no_wait(*args):
        return None

    monkeypatch.setattr(host, "settings_from_profile", lambda *a, **k: settings)
    monkeypatch.setattr(host, "preflight_ports", lambda *a, **k: None)
    monkeypatch.setattr(host, "WindowsProcessSupervisor", Supervisor)
    monkeypatch.setattr(host.asyncio, "sleep", no_wait)
    with pytest.raises(ValueError) as caught:
        await host.guard(tmp_path / "host.json", tmp_path, min_free_bytes=0)
    assert caught.value.code == "HOST_DATABASE_UNAVAILABLE"
    assert len(created) == 8
    status = json.loads((control.root / "status.json").read_text())
    assert status["diagnostic"]["diagnostic_id"] == caught.value.diagnostic_id
    assert status["diagnostic"]["component"] == "public"
    assert control.frozen()
    assert SECRET not in json.dumps(status)


def test_database_failure_classification_ignores_exception_message():
    from personal_assistant.infrastructure.operations.diagnostics import classify

    error = classify(ConnectionRefusedError(SECRET), component="agent")
    assert error.code == "HOST_DATABASE_UNAVAILABLE"
    assert SECRET not in json.dumps(error.response())
