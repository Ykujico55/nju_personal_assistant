from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from personal_assistant.core.models import ModelRequest
from personal_assistant.core.secrets import SecretHandle
from personal_assistant.infrastructure.memory.secrets import InMemorySecretStore


class ModelSetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_config_persists_only_handle_and_refuses_overwrite(self):
        from personal_assistant.infrastructure.models.setup import configure, load_config

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.json"
            store = InMemorySecretStore()
            result = await configure(path, store, "synthetic-fixture-key")
            self.assertEqual("deepseek-flash", result["PA_MODEL_REMOTE_MODEL"])
            self.assertNotIn("synthetic-fixture-key", path.read_text())
            self.assertEqual("disabled", result["PA_MODEL_REMOTE_THINKING"])
            self.assertEqual(result, load_config(path))
            with self.assertRaises(FileExistsError):
                await configure(path, store, "replacement-fixture")

    async def test_deepseek_options_and_secret_never_enter_payload(self):
        from personal_assistant.infrastructure.models.openai_compatible import (
            OpenAICompatibleChatProvider,
            OpenAICompatibleConfig,
        )

        provider = OpenAICompatibleChatProvider(
            OpenAICompatibleConfig(
                provider_id="deepseek",
                model_id="deepseek-flash",
                base_url="https://api.deepseek.com",
                secret_handle=SecretHandle("fixture", "model_api_key"),
                json_mode=True,
                thinking="disabled",
            ),
            secret_store=InMemorySecretStore(),
        )
        try:
            payload = provider._build_payload(
                ModelRequest(purpose="synthetic", instruction="return JSON"), "deepseek-flash"
            )
            self.assertEqual({"type": "json_object"}, payload["response_format"])
            self.assertEqual({"type": "disabled"}, payload["thinking"])
            self.assertNotIn("fixture", str(payload))
        finally:
            await provider.aclose()

    async def test_setup_config_rejects_raw_key_and_unknown_settings(self):
        from personal_assistant.infrastructure.models.setup import load_config

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.json"
            path.write_text('{"api_key":"synthetic"}')
            with self.assertRaises(ValueError):
                load_config(path)

    async def test_concurrent_setup_reserves_config_before_credential_write(self):
        import asyncio

        from personal_assistant.infrastructure.models.setup import configure

        class Store(InMemorySecretStore):
            calls = 0

            async def put(self, **values):
                self.calls += 1
                await asyncio.sleep(0.01)
                return await super().put(**values)

        with tempfile.TemporaryDirectory() as directory:
            store = Store()
            path = Path(directory) / "model.json"
            results = await asyncio.gather(
                configure(path, store, "fixture-one"),
                configure(path, store, "fixture-two"),
                return_exceptions=True,
            )
            self.assertEqual(1, store.calls)
            self.assertEqual(1, sum(isinstance(r, dict) for r in results))
