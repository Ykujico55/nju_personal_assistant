"""Restart coordinates owned guardians and preserves later freeze decisions."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from personal_assistant.infrastructure.operations import host
from personal_assistant.infrastructure.operations.control import HostControl
from personal_assistant.settings import Settings
from tests.unit.test_settings import direct_settings_values


def test_later_manual_freeze_cannot_be_cleared_by_restart_ticket(tmp_path):
    first, other = HostControl(tmp_path), HostControl(tmp_path)
    first.resume()
    ticket = first.begin_restart("restart-fixture")
    assert first.frozen()
    other.freeze()
    assert not first.resume_restart(ticket, "restart-fixture")
    assert other.frozen()


def test_owned_restart_freeze_resumes_but_preexisting_freeze_stays(tmp_path):
    control = HostControl(tmp_path)
    ticket = control.begin_restart("restart-fixture")
    control.freeze(owner="guardian", preserve_existing=True)
    assert control.resume_restart(ticket, "restart-fixture")
    assert not control.frozen()
    control.freeze()
    ticket = control.begin_restart("restart-other")
    assert not control.resume_restart(ticket, "restart-other")
    assert control.frozen()


def test_boot_scoped_stop_is_not_consumed_by_new_or_other_guardian(tmp_path):
    control = HostControl(tmp_path)
    control.request_stop(boot_id="a" * 32)
    assert control.stopping("a" * 32)
    assert not control.stopping("b" * 32)
    control.prepare_start("a" * 32)
    assert control.stopping("a" * 32)


def setup(tmp_path, monkeypatch):
    settings = replace(Settings(**direct_settings_values()), extension_root=tmp_path / "extensions")
    control = HostControl(tmp_path / "control")
    control.resume()
    control._write(
        "status.json", {"guardian_pid": 123, "boot_id": "a" * 32, "admin": True, "ready": True}
    )
    monkeypatch.setattr(host, "settings_from_profile", lambda *a, **k: settings)
    return control


async def test_restart_waits_for_old_lock_and_ready_then_preserves_admin(tmp_path, monkeypatch):
    control = setup(tmp_path, monkeypatch)
    probes = 0
    spawned = []

    def running(control):
        nonlocal probes
        probes += 1
        return probes < 3 or bool(spawned)

    def spawn(profile, root, *, admin, boot_id):
        assert probes >= 3
        assert control.stopping("a" * 32) and control.frozen()
        assert admin is True
        control._write(
            "status.json", {"guardian_pid": 456, "boot_id": boot_id, "admin": admin, "ready": True}
        )
        child = SimpleNamespace(pid=456, poll=lambda: None)
        spawned.append(child)
        return child

    monkeypatch.setattr(host, "guardian_running", running)
    monkeypatch.setattr(host, "spawn_guardian", spawn)
    result = await host.restart(tmp_path / "profile.json", tmp_path, timeout_seconds=2)
    assert result["ready"] and result["guardian_pid"] == 456
    assert result["resumed"] and not control.frozen()
    assert len(spawned) == 1


async def test_old_guardian_timeout_never_launches_second_instance(tmp_path, monkeypatch):
    control = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(host, "guardian_running", lambda control: True)
    spawn = Mock()
    monkeypatch.setattr(host, "spawn_guardian", spawn)
    with pytest.raises(ValueError) as caught:
        await host.restart(tmp_path / "profile.json", tmp_path, timeout_seconds=0.01)
    assert caught.value.code == "HOST_STOP_TIMEOUT"
    spawn.assert_not_called()
    assert control.frozen()


async def test_legacy_guardian_is_refused_without_mutating_control(tmp_path, monkeypatch):
    control = setup(tmp_path, monkeypatch)
    control._write("status.json", {"guardian_pid": 123, "components": {"public": 124}})
    before = (control.root / "freeze.json").read_bytes()
    monkeypatch.setattr(host, "guardian_running", lambda control: True)
    with pytest.raises(ValueError) as caught:
        await host.restart(tmp_path / "profile.json", tmp_path, timeout_seconds=0.01)
    assert caught.value.code == "HOST_RESTART_UNSUPPORTED"
    assert (control.root / "freeze.json").read_bytes() == before
    assert not control.stopping()


async def test_ready_timeout_and_new_manual_freeze_remain_frozen(tmp_path, monkeypatch):
    control = setup(tmp_path, monkeypatch)
    running_results = iter([True, False])
    monkeypatch.setattr(host, "guardian_running", lambda control: next(running_results, True))

    def spawn(profile, root, *, admin, boot_id):
        control.freeze()
        # An old ready status must never count as the new guardian's readiness.
        return SimpleNamespace(pid=456, poll=lambda: None)

    monkeypatch.setattr(host, "spawn_guardian", spawn)
    with pytest.raises(ValueError) as caught:
        await host.restart(tmp_path / "profile.json", tmp_path, timeout_seconds=0.02)
    assert caught.value.code == "HOST_READY_TIMEOUT"
    assert control.frozen()


async def test_concurrent_restart_is_refused_before_control_mutation(tmp_path, monkeypatch):
    control = setup(tmp_path, monkeypatch)
    before = (control.root / "freeze.json").read_bytes()
    lock = host.GuardianLock(control.root / "restart.lock")
    try:
        with pytest.raises(ValueError) as caught:
            await host.restart(tmp_path / "profile.json", tmp_path)
        assert caught.value.code == "HOST_RESTART_IN_PROGRESS"
        assert (control.root / "freeze.json").read_bytes() == before
        assert not control.stopping()
    finally:
        lock.close()


def test_readiness_requires_current_boot_pid_and_fresh_agent_heartbeat(tmp_path):
    import os
    from datetime import UTC, datetime, timedelta

    from personal_assistant.infrastructure.operations.readiness import mark_ready, ready

    control = HostControl(tmp_path)
    mark_ready(control, "agent", "a" * 32)
    assert ready(control, "agent", os.getpid(), "a" * 32)
    assert not ready(control, "agent", os.getpid() + 1, "a" * 32)
    assert not ready(control, "agent", os.getpid(), "b" * 32)
    record = control.read("ready-agent.json")
    record["seen_at"] = (datetime.now(UTC) - timedelta(seconds=31)).isoformat()
    control._write("ready-agent.json", record)
    assert not ready(control, "agent", os.getpid(), "a" * 32)


def _manual_freeze_under_lock(root, entered, release):
    control = HostControl(root)
    with control._locked():
        control._freeze("manual")
        entered.set()
        if not release.wait(5):
            raise RuntimeError("test release timeout")


def test_freeze_revision_compare_and_write_is_cross_process_locked(tmp_path):
    import multiprocessing
    from concurrent.futures import ThreadPoolExecutor

    control = HostControl(tmp_path)
    ticket = control.begin_restart("restart-fixture")
    context = multiprocessing.get_context("spawn")
    entered, release = context.Event(), context.Event()
    child = context.Process(target=_manual_freeze_under_lock, args=(tmp_path, entered, release))
    child.start()
    try:
        assert entered.wait(5)
        with ThreadPoolExecutor(max_workers=1) as executor:
            attempt = executor.submit(control.resume_restart, ticket, "restart-fixture")
            assert not attempt.done()
            release.set()
            assert attempt.result(timeout=5) is False
        child.join(5)
        assert child.exitcode == 0
        assert control.frozen()
    finally:
        release.set()
        if child.is_alive():
            child.terminate()
        child.join(5)
        child.close()


def test_readiness_binds_python_child_to_owned_launcher_without_accepting_unrelated_pid(
    tmp_path, monkeypatch
):
    from personal_assistant.infrastructure.operations import readiness

    control = HostControl(tmp_path)
    monkeypatch.setattr(readiness.os, "getpid", lambda: 222)
    monkeypatch.setattr(readiness.os, "getppid", lambda: 111)
    readiness.mark_ready(control, "public", "a" * 32)
    assert readiness.ready(control, "public", 111, "a" * 32)
    assert not readiness.ready(control, "public", 333, "a" * 32)
    assert not readiness.ready(control, "public", 111, "b" * 32)
    record = control.read("ready-public.json")
    record["parent_pid"] = True
    control._write("ready-public.json", record)
    assert not readiness.ready(control, "public", 111, "a" * 32)


async def test_agent_readiness_follows_successful_current_pid_boot_heartbeat(tmp_path, monkeypatch):
    import asyncio
    import os
    from unittest.mock import AsyncMock

    from personal_assistant.workers.agent_worker import heartbeat

    stop = asyncio.Event()
    written = []

    async def put(*args, **kwargs):
        written.append(args[2])
        stop.set()

    settings = replace(Settings(**direct_settings_values()), extension_root=tmp_path / "extensions")
    container = SimpleNamespace(
        settings=settings,
        records=SimpleNamespace(
            get=AsyncMock(return_value=None),
            put=put,
        ),
    )
    monkeypatch.setenv("PA_HOST_BOOT_ID", "c" * 32)
    await heartbeat(container, stop)
    assert written[0]["pid"] == os.getpid() and written[0]["boot_id"] == "c" * 32
    marker = HostControl(tmp_path / "control").read("ready-agent.json")
    assert marker["pid"] == os.getpid() and marker["boot_id"] == "c" * 32


def test_control_status_replace_waits_for_windows_reader_without_stopping_host(tmp_path):
    import os
    import time
    from concurrent.futures import ThreadPoolExecutor

    if os.name != "nt":
        pytest.skip("Windows delete sharing behavior")
    control = HostControl(tmp_path)
    control._write("status.json", {"ready": False})
    reader = (tmp_path / "status.json").open("rb")
    with ThreadPoolExecutor(max_workers=1) as executor:
        writing = executor.submit(control._write, "status.json", {"ready": True})
        try:
            time.sleep(0.08)
            assert not writing.done(), "a transient reader must not stop the guardian"
        finally:
            reader.close()
        writing.result(timeout=3)
    assert control.read("status.json") == {"ready": True}
    assert list(tmp_path.glob(".*.tmp")) == []


def test_persistent_status_replace_denial_remains_an_error_and_cleans_temporary(
    tmp_path, monkeypatch
):
    import os
    import time

    from personal_assistant.infrastructure.operations import control as module

    if os.name != "nt":
        pytest.skip("Windows delete sharing behavior")
    control = HostControl(tmp_path)
    control._write("status.json", {"ready": False})
    error = PermissionError("synthetic persistent sharing denial")
    error.winerror = 5
    calls = []

    def denied(*args):
        calls.append(1)
        raise error

    monkeypatch.setattr(module.os, "replace", denied)
    start = time.monotonic()
    with pytest.raises(PermissionError):
        control._write("status.json", {"ready": True})
    assert 1 < len(calls) < 1000
    assert time.monotonic() - start < 3
    assert control.read("status.json") == {"ready": False}
    assert list(tmp_path.glob(".*.tmp")) == []
