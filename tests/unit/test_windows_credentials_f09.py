"""F09.1 portable, adversarial tests for the native credential boundary."""
from __future__ import annotations

import importlib
import re
import traceback
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from personal_assistant.core.secrets import SecretHandle, SecretUnavailableError

SYNTHETIC = "synthetic-only-密钥\nsecond-line"


class FakeCredentialApi:
    def __init__(self):
        self.records = {}
        self.calls = []
        self.fail = None
        self.buffers = []

    def write(self, record):
        self.calls.append(("write", record.target))
        self.buffers.append(record.blob)
        if self.fail:
            raise OSError(SYNTHETIC)
        self.records[record.target] = replace(record, blob=bytearray(record.blob))

    def read(self, target):
        self.calls.append(("read", target))
        if self.fail:
            raise OSError(SYNTHETIC)
        if target not in self.records:
            return None
        record = replace(self.records[target], blob=bytearray(self.records[target].blob))
        self.buffers.append(record.blob)
        return record

    def delete(self, target):
        self.calls.append(("delete", target))
        if self.fail:
            raise OSError(SYNTHETIC)
        self.records.pop(target, None)


def backend(api=None, namespace="f09-unit"):
    module = importlib.import_module(
        "personal_assistant.infrastructure.platform.windows.credential_manager"
    )
    return module.WindowsCredentialManager(namespace=namespace, api=api or FakeCredentialApi())


@pytest.mark.asyncio
async def test_unicode_roundtrip_kind_binding_and_buffer_cleanup():
    api = FakeCredentialApi()
    store = backend(api)
    handle = await store.put(name="test label", kind="mail_client_password", value=SYNTHETIC)
    assert re.fullmatch(r"wcm1_[0-9a-f]{32}", handle.id)
    record = next(iter(api.records.values()))
    assert record.credential_type == 1 and record.persist == 2
    assert SYNTHETIC not in record.target and SYNTHETIC not in repr(record)
    assert record.blob == SYNTHETIC.encode("utf-8")
    assert await backend(api).resolve_for_broker(handle, purpose="mail.transport") == SYNTHETIC
    assert all(not any(buffer) for buffer in api.buffers)
    with pytest.raises(SecretUnavailableError):
        await store.resolve_for_broker(
            SecretHandle(handle.id, "push_vapid_private"), purpose="web_push"
        )
    await store.delete(handle)
    await store.delete(handle)
    with pytest.raises(SecretUnavailableError):
        await store.resolve_for_broker(handle, purpose="mail.transport")


@pytest.mark.asyncio
async def test_same_label_allocates_distinct_handles_and_namespaces():
    api = FakeCredentialApi()
    store = backend(api)
    first = await store.put(name="same label", kind="model_api_key", value="first synthetic")
    second = await store.put(name="same label", kind="model_api_key", value="second synthetic")
    assert first != second
    await store.delete(first)
    assert await store.resolve_for_broker(second, purpose="model") == "second synthetic"
    with pytest.raises(SecretUnavailableError):
        await backend(api, "another-host").resolve_for_broker(second, purpose="model")


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"blob": bytearray(b"\xff")},
    {"blob": bytearray(b"x" * 2561)},
    {"credential_type": 2},
    {"persist": 3},
    {"target": "foreign/credential"},
    {"username": "different-kind"},
])
async def test_corrupt_record_is_rejected_without_exposing_payload(change):
    api = FakeCredentialApi()
    store = backend(api)
    handle = await store.put(name="test label", kind="model_api_key", value=SYNTHETIC)
    target, record = next(iter(api.records.items()))
    api.records[target] = replace(record, **change)
    with pytest.raises(SecretUnavailableError) as failure:
        await store.resolve_for_broker(handle, purpose="model")
    assert SYNTHETIC not in "".join(traceback.format_exception(failure.value))
    assert failure.value.__cause__ is None and failure.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["put", "read", "delete"])
async def test_native_errors_are_typed_sanitized_and_never_success(operation):
    api = FakeCredentialApi()
    store = backend(api)
    handle = await store.put(name="test label", kind="model_api_key", value=SYNTHETIC)
    api.fail = True
    with pytest.raises(SecretUnavailableError) as failure:
        if operation == "put":
            await store.put(name="test label", kind="model_api_key", value=SYNTHETIC)
        elif operation == "read":
            await store.resolve_for_broker(handle, purpose="model")
        else:
            await store.delete(handle)
    assert failure.value.__cause__ is None and failure.value.__context__ is None
    assert SYNTHETIC not in "".join(traceback.format_exception(failure.value))
    trace = failure.value.__traceback__
    while trace:
        if trace.tb_frame.f_code.co_name == "put":
            assert SYNTHETIC not in trace.tb_frame.f_locals.values()
        trace = trace.tb_next


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [
    {"name": "", "kind": "model_api_key", "value": "synthetic"},
    {"name": "label\x00injection", "kind": "model_api_key", "value": "synthetic"},
    {"name": "label", "kind": "../foreign", "value": "synthetic"},
    {"name": "label", "kind": "model_api_key", "value": ""},
    {"name": "label", "kind": "model_api_key", "value": "密" * 854},
])
async def test_invalid_put_never_calls_os(arguments):
    api = FakeCredentialApi()
    with pytest.raises(SecretUnavailableError):
        await backend(api).put(**arguments)
    assert api.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("handle,purpose", [
    (SecretHandle("raw-api-key", "model_api_key"), "model"),
    (SecretHandle("wcm1_" + "a" * 32, "../foreign"), "model"),
    (SecretHandle("wcm1_" + "a" * 32, "model_api_key"), ""),
])
async def test_invalid_reference_or_purpose_never_calls_os(handle, purpose):
    api = FakeCredentialApi()
    with pytest.raises(SecretUnavailableError):
        await backend(api).resolve_for_broker(handle, purpose=purpose)
    assert api.calls == []


def test_platform_factory_is_lazy_and_never_falls_back_to_memory(monkeypatch):
    factory = importlib.import_module("personal_assistant.infrastructure.secrets.factory")
    from personal_assistant.infrastructure.secrets import UnavailableSecretStore

    monkeypatch.setattr(factory.sys, "platform", "linux")
    assert isinstance(factory.default_secret_store(), UnavailableSecretStore)
    windows = importlib.import_module("personal_assistant.infrastructure.platform.windows")
    marker = SimpleNamespace()
    monkeypatch.setattr(windows, "WindowsCredentialManager", lambda: marker)
    monkeypatch.setattr(factory.sys, "platform", "win32")
    assert factory.default_secret_store() is marker


@pytest.mark.asyncio
@pytest.mark.parametrize("storage,injected", [("postgres", False), ("postgres", True),
                                            ("memory", False)])
async def test_composition_root_selects_backend_and_preserves_explicit_injection(
    tmp_path, monkeypatch, storage, injected
):
    from personal_assistant import bootstrap
    from personal_assistant.infrastructure.memory.secrets import InMemorySecretStore
    from personal_assistant.settings import Settings

    store = backend()
    calls = []
    monkeypatch.setattr(bootstrap, "default_secret_store", lambda: calls.append(True) or store)
    with patch.dict("os.environ", {}, clear=True):
        settings = replace(Settings.from_env(), storage_backend=storage,
                           extension_root=tmp_path / "extensions",
                           artifact_root=tmp_path / "artifacts")
    container = bootstrap.build_container(settings, secret_store=store if injected else None)
    try:
        selected = container.push._secrets
        assert container.mail_broker._secret_store is selected
        if storage == "postgres":
            assert selected is store
            assert calls == ([] if injected else [True])
        else:
            assert isinstance(selected, InMemorySecretStore) and calls == []
    finally:
        await container.aclose()


@pytest.mark.asyncio
async def test_exact_utf8_byte_limit_is_supported():
    store = backend()
    value = "密" * 853 + "x"
    assert len(value.encode("utf-8")) == 2560
    handle = await store.put(name="byte limit", kind="model_api_key", value=value)
    assert await store.resolve_for_broker(handle, purpose="model") == value
    await store.delete(handle)


@pytest.mark.asyncio
async def test_cancelled_write_settles_then_cleans_only_its_new_reference():
    import asyncio
    import threading

    started, release = threading.Event(), threading.Event()

    class SlowApi(FakeCredentialApi):
        def write(self, record):
            super().write(record)
            started.set()
            assert release.wait(5)

    api = SlowApi()
    store = backend(api)
    task = asyncio.create_task(
        store.put(name="cancel test", kind="model_api_key", value="synthetic cancellation")
    )
    try:
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert api.records == {}
    assert [operation for operation, _ in api.calls] == ["write", "delete"]
