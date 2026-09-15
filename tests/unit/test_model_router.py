from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from personal_assistant.core.models import (
    ContextField,
    DataClassification,
    DisclosureConsent,
    DisclosureDenied,
    ModelOutput,
    ModelRequest,
    ModelRouter,
)


class FakeProvider:
    def __init__(self, provider_id: str, *, remote: bool) -> None:
        self.provider_id = provider_id
        self.is_remote = remote
        self.calls = 0

    async def complete(self, request: ModelRequest) -> ModelOutput:
        self.calls += 1
        return ModelOutput(text=request.instruction, provider_id=self.provider_id, model_id="fake")


class ModelRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_remote_sensitive_data_needs_exact_consent(self) -> None:
        provider = FakeProvider("remote", remote=True)
        router = ModelRouter((provider,))
        request = ModelRequest(
            purpose="draft reply",
            instruction="draft",
            fields=(
                ContextField("mail", "private body", DataClassification.SENSITIVE, "mail:1"),
            ),
        )
        with self.assertRaises(DisclosureDenied):
            await router.complete(request, provider_id="remote")

        consent = DisclosureConsent.for_request(
            provider_id="remote",
            request=request,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            user_id="owner",
        )
        result = await router.complete(request, provider_id="remote", consent=consent)
        self.assertEqual("remote", result.provider_id)

        changed = ModelRequest(
            purpose=request.purpose,
            instruction=request.instruction,
            fields=(ContextField("mail", "changed", DataClassification.SENSITIVE, "mail:1"),),
        )
        with self.assertRaises(DisclosureDenied):
            await router.complete(changed, provider_id="remote", consent=consent)

    async def test_secret_is_denied_even_for_local_provider(self) -> None:
        provider = FakeProvider("local", remote=False)
        router = ModelRouter((provider,))
        request = ModelRequest(
            purpose="login",
            instruction="use credential",
            fields=(ContextField("password", "do-not-send", DataClassification.SECRET, "vault"),),
        )
        with self.assertRaises(DisclosureDenied):
            await router.complete(request, provider_id="local")
        self.assertEqual(0, provider.calls)

    async def test_missing_remote_consent_can_route_to_explicit_local_fallback(self) -> None:
        remote = FakeProvider("remote", remote=True)
        local = FakeProvider("local", remote=False)
        router = ModelRouter((remote, local))
        request = ModelRequest(
            purpose="summarize",
            instruction="summary",
            fields=(
                ContextField("record", "personal", DataClassification.PERSONAL, "file:1"),
            ),
        )
        result = await router.complete(
            request, provider_id="remote", local_fallback_id="local"
        )
        self.assertEqual("local", result.provider_id)
        self.assertEqual(0, remote.calls)
        self.assertEqual(1, local.calls)


if __name__ == "__main__":
    unittest.main()
