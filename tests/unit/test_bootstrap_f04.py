"""F04 composition-root wiring: providers, persisted authorizer and fail-closed keys."""

from __future__ import annotations

import asyncio
import os
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from personal_assistant.bootstrap import build_container
from personal_assistant.core.models import (
    ContextField,
    DataClassification,
    DisclosureDenied,
    ModelRequest,
)
from personal_assistant.core.models.disclosure import DisclosureConsentService
from personal_assistant.core.models.errors import (
    ModelCredentialUnavailableError,
    ModelProviderUnavailableError,
)
from personal_assistant.core.secrets import SecretHandle, SecretUnavailableError
from personal_assistant.infrastructure.memory import InMemorySecretStore
from personal_assistant.infrastructure.models import (
    OpenAICompatibleChatProvider,
    OpenAICompatibleConfig,
)
from personal_assistant.infrastructure.secrets import UnavailableSecretStore
from personal_assistant.settings import Settings


def settings_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "environment": "development",
        "log_level": "INFO",
        "public_host": "127.0.0.1",
        "public_port": 8000,
        "admin_host": "127.0.0.1",
        "admin_port": 8001,
        "health_host": "127.0.0.1",
        "health_port": 8010,
        "storage_backend": "memory",
        "database_url": "postgresql+asyncpg://assistant:change-me@127.0.0.1:5432/assistant",
        "extension_root": Path("./var/extensions"),
        "artifact_root": Path("./var/artifacts"),
        "trust_cloudflare_access": False,
        "public_origin": None,
        "cf_access_team_domain": None,
        "cf_access_aud": None,
    }
    values.update(overrides)
    return values


def sensitive_request() -> ModelRequest:
    return ModelRequest(
        purpose="draft reply",
        instruction="draft",
        fields=(
            ContextField("mail_body", "private body", DataClassification.SENSITIVE, "mail:1"),
        ),
    )


class BootstrapF04Tests(unittest.TestCase):
    def test_memory_container_exposes_disclosure_service_without_models(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
        container = build_container(settings)
        self.assertIsInstance(container.disclosures, DisclosureConsentService)
        self.assertIsNone(container.model_router)
        asyncio.run(container.aclose())

    def test_router_uses_persisted_authorizer_and_denies_without_consent(self) -> None:
        settings = Settings(
            **settings_values(
                model_remote_base_url="https://api.example.test/v1",
                model_remote_model="gpt-test-1",
                model_remote_secret_handle="handle-1",
            )
        )
        container = build_container(settings, secret_store=InMemorySecretStore())
        router = container.model_router
        assert router is not None
        self.assertEqual(("remote.openai",), router.provider_ids)
        with self.assertRaises(DisclosureDenied):
            asyncio.run(
                router.complete(
                    sensitive_request(), provider_id="remote.openai", user_id="owner"
                )
            )
        asyncio.run(container.aclose())

    def test_explicit_local_fallback_stays_on_the_configured_loopback_provider(self) -> None:
        settings = Settings(
            **settings_values(
                model_remote_base_url="https://api.example.test/v1",
                model_remote_model="gpt-test-1",
                model_remote_secret_handle="handle-1",
                model_local_base_url="http://127.0.0.1:1",
                model_local_model="llama3.1:8b",
                model_local_fallback_provider_id="local.ollama",
            )
        )
        container = build_container(settings, secret_store=InMemorySecretStore())
        router = container.model_router
        assert router is not None
        self.assertEqual(("local.ollama", "remote.openai"), router.provider_ids)
        # No consent -> explicit local fallback; the unreachable loopback port
        # produces a typed failure on the local provider, never a second remote
        # provider or a silent success.
        with self.assertRaises(ModelProviderUnavailableError) as caught:
            asyncio.run(
                router.complete(
                    sensitive_request(), provider_id="remote.openai", user_id="owner"
                )
            )
        self.assertEqual("local.ollama", caught.exception.provider_id)
        asyncio.run(container.aclose())

    def test_composition_root_fails_closed_when_credentials_unavailable(self) -> None:
        settings = Settings(
            **settings_values(
                model_remote_base_url="https://api.example.test/v1",
                model_remote_model="gpt-test-1",
                model_remote_secret_handle="handle-1",
            )
        )
        container = build_container(settings, secret_store=UnavailableSecretStore())
        request = sensitive_request()
        now = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)

        async def exercise() -> None:
            preview = container.disclosures.preview(
                request, provider_id="remote.openai", now=now
            )
            record = await container.disclosures.confirm(
                request,
                provider_id="remote.openai",
                user_id="owner",
                preview_hash=preview.preview_hash,
                idempotency_key="confirm-1",
                now=now,
            )
            router = container.model_router
            assert router is not None
            with self.assertRaises(ModelCredentialUnavailableError):
                await router.complete(
                    request,
                    provider_id="remote.openai",
                    consent_id=record.id,
                    user_id="owner",
                    now=now + timedelta(minutes=1),
                )
            await container.aclose()

        asyncio.run(exercise())

    def test_unavailable_secret_store_fails_closed(self) -> None:
        store = UnavailableSecretStore()
        with self.assertRaises(SecretUnavailableError):
            asyncio.run(
                store.resolve_for_broker(
                    SecretHandle(id="handle-1", kind="model_api_key"), purpose="model:test"
                )
            )
        provider = OpenAICompatibleChatProvider(
            OpenAICompatibleConfig(
                provider_id="remote.openai",
                model_id="gpt-test-1",
                base_url="https://api.example.test/v1",
                secret_handle=SecretHandle(id="handle-1", kind="model_api_key"),
            ),
            secret_store=store,
        )
        with self.assertRaises(ModelCredentialUnavailableError):
            asyncio.run(provider.complete(sensitive_request()))


if __name__ == "__main__":
    unittest.main()
