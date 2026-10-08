"""Real same-user Windows vault persistence, with synthetic data and exact cleanup."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from personal_assistant.core.secrets import SecretUnavailableError

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows native credential API")


@pytest.mark.asyncio
async def test_native_credential_survives_new_process_and_is_deleted():
    from personal_assistant.infrastructure.platform.windows import WindowsCredentialManager

    namespace = "f09-test-" + uuid4().hex
    store = WindowsCredentialManager(namespace=namespace)
    synthetic = "synthetic-only-跨进程\n" + uuid4().hex
    handle = await store.put(name="F09.1 synthetic persistence check",
                             kind="model_api_key", value=synthetic)
    try:
        root = Path(__file__).resolve().parents[2]
        env = {key: value for key, value in os.environ.items() if not key.startswith("PA_")}
        env["PYTHONPATH"] = str(root / "src") + os.pathsep + str(root / "extension_sdk/src")
        child = """
import asyncio, hashlib, sys
from personal_assistant.core.secrets import SecretHandle
from personal_assistant.infrastructure.platform.windows import WindowsCredentialManager
async def check():
    store = WindowsCredentialManager(namespace=sys.argv[1])
    handle = SecretHandle(sys.argv[2], sys.argv[3])
    value = await store.resolve_for_broker(handle, purpose="f09.synthetic.persistence")
    assert hashlib.sha256(value.encode("utf-8")).hexdigest() == sys.argv[4]
    await store.delete(handle)
    print("persisted-and-deleted")
asyncio.run(check())
"""
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", child, namespace, handle.id, handle.kind,
             hashlib.sha256(synthetic.encode("utf-8")).hexdigest()],
            cwd=root, env=env, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, "synthetic vault subprocess failed"
        assert result.stdout.strip() == "persisted-and-deleted"
        assert not result.stderr and synthetic not in result.stdout
        with pytest.raises(SecretUnavailableError):
            await WindowsCredentialManager(namespace=namespace).resolve_for_broker(
                handle, purpose="f09.synthetic.deleted"
            )
    finally:
        await store.delete(handle)


@pytest.mark.asyncio
async def test_native_vapid_reference_is_usable_by_host_without_network():
    import base64
    from unittest.mock import AsyncMock

    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from personal_assistant.infrastructure.memory.push import InMemoryPushSubscriptionStore
    from personal_assistant.infrastructure.platform.windows import WindowsCredentialManager
    from personal_assistant.infrastructure.push.service import PushService

    private = ec.generate_private_key(ec.SECP256R1())
    def encode(raw):
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    raw = encode(private.private_numbers().private_value.to_bytes(32, "big"))
    public = encode(private.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint
    ))
    store = WindowsCredentialManager(namespace="f09-push-" + uuid4().hex)
    handle = await store.put(name="F09.1 synthetic VAPID only",
                             kind="push_vapid_private", value=raw)
    sender = AsyncMock()
    service = PushService(
        store=InMemoryPushSubscriptionStore(), secrets=store, sender=sender,
        owner_id="synthetic", public_key=public, secret_handle_id=handle.id,
        allowed_origins=("https://push.example.test",),
    )
    try:
        assert await service.configured_public_key() == public
        sender.send.assert_not_awaited()
        await store.delete(handle)
        assert await service.configured_public_key() is None
        sender.send.assert_not_awaited()
    finally:
        await store.delete(handle)
        await service.aclose()
