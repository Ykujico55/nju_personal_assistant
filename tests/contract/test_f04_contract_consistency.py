"""F04: keep model adapters and disclosure consent consistent with the contract.

Textual checks guard dependency placement, immutable migration history, the
"router never executes tools" rule and the documented error/binding semantics.
"""

from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Frozen history: F04 must not edit these files.  The values match TODO.md and
# docs/CONTRACTS_AND_INTERFACES.md.
FROZEN_MIGRATIONS = {
    "0001_core.sql": "836878d68c83edac5b70671e3aa5da12e7f949c89c72cae8d300edc7fba8cd59",
    "0002_f01_persistence.sql": "5fdc67aaff07dbcd486fc62774ad5745cbef871332433dfb8535790a0a18a580",
    "0003_f02_operations.sql": (
        "28cbda227918bfcd80366208b59713eb0dbb0b0cbdaa582cb5e55a91ce2e1e03"
    ),
    "0004_f02_operation_request_scope.sql": (
        "6b6bb9f5cea83b443c9ca345f7a9d134f6ebca69969f01e23557ecff706af6fc"
    ),
}


class F04ContractConsistencyTests(unittest.TestCase):
    def test_frozen_migration_files_are_byte_identical(self) -> None:
        for name, digest in FROZEN_MIGRATIONS.items():
            with self.subTest(migration=name):
                raw = (ROOT / "migrations" / name).read_bytes()
                self.assertEqual(digest, hashlib.sha256(raw).hexdigest())

    def test_0005_is_registered_for_wheel_packaging(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text("utf-8")
        self.assertIn("migrations/0005_f04_model_disclosure.sql", pyproject)
        self.assertIn('share/personal-assistant/migrations', pyproject)
        self.assertIn('include = ["personal_assistant*"]', pyproject)

    def test_core_and_domain_never_import_http_or_vendor_sdks(self) -> None:
        markers = (
            "import httpx",
            "from httpx",
            "import openai",
            "from openai",
            "import ollama",
            "import anthropic",
            "import requests",
            "import aiohttp",
            "personal_assistant.infrastructure",
        )
        offenders = []
        for layer in ("core", "domain"):
            for path in (ROOT / "src" / "personal_assistant" / layer).rglob("*.py"):
                text = path.read_text("utf-8")
                if any(marker in text for marker in markers):
                    offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([], offenders)

    def test_router_never_executes_tools_or_touches_gateways(self) -> None:
        source = (
            ROOT / "src" / "personal_assistant" / "core" / "models" / "router.py"
        ).read_text("utf-8")
        for marker in ("ToolGateway", "tool_gateway", "ToolRegistry", "SideEffect"):
            self.assertNotIn(marker, source)
        # A fallback target must be local; a provider error must propagate.
        self.assertIn("fallback.is_remote", source)

    def test_model_adapters_are_protocol_only_and_silent(self) -> None:
        adapters = ROOT / "src" / "personal_assistant" / "infrastructure" / "models"
        for path in sorted(adapters.rglob("*.py")):
            text = path.read_text("utf-8")
            with self.subTest(module=path.name):
                for marker in (
                    "import openai",
                    "from openai",
                    "import anthropic",
                    "from anthropic",
                    "import ollama",
                    "import logging",
                    "logger",
                    "print(",
                ):
                    self.assertNotIn(marker, text)

    def test_remote_adapter_resolves_credentials_through_the_secret_port(self) -> None:
        source = (
            ROOT
            / "src"
            / "personal_assistant"
            / "infrastructure"
            / "models"
            / "openai_compatible.py"
        ).read_text("utf-8")
        self.assertIn(
            "personal_assistant.core.secrets import SecretHandle, SecretStorePort", source
        )
        self.assertIn("resolve_for_broker", source)
        self.assertIn("ModelCredentialUnavailableError", source)
        self.assertNotIn("api_key = config", source)

    def test_local_adapter_cannot_point_outside_loopback(self) -> None:
        source = (
            ROOT
            / "src"
            / "personal_assistant"
            / "infrastructure"
            / "models"
            / "ollama.py"
        ).read_text("utf-8")
        self.assertIn("require_loopback=True", source)
        self.assertIn("allow_http=True", source)

    def test_settings_reject_partial_model_configuration(self) -> None:
        source = (ROOT / "src" / "personal_assistant" / "settings.py").read_text("utf-8")
        for marker in (
            "PA_MODEL_REMOTE_BASE_URL",
            "PA_MODEL_REMOTE_SECRET_HANDLE",
            "PA_MODEL_LOCAL_BASE_URL",
            "PA_MODEL_LOCAL_FALLBACK_PROVIDER_ID",
            "require_loopback",
        ):
            self.assertIn(marker, source)

    def test_adapters_ignore_environment_proxies_and_never_chain_raw_exceptions(self) -> None:
        base = (
            ROOT
            / "src"
            / "personal_assistant"
            / "infrastructure"
            / "models"
            / "base.py"
        ).read_text("utf-8")
        self.assertIn("trust_env=False", base)
        self.assertIn("run_cleanup(response.aclose())", base)
        self.assertIn("follow_redirects=False", base)
        # No ``from exc`` anywhere in the adapters: an httpx/broker/JSON
        # exception would otherwise keep credentials or request bodies alive in
        # the chain, and a formatted traceback would render them.
        offenders = [
            path.name
            for path in (
                ROOT / "src" / "personal_assistant" / "infrastructure" / "models"
            ).rglob("*.py")
            if "from exc" in path.read_text("utf-8")
        ]
        self.assertEqual([], offenders)

    def test_recipient_fingerprint_is_bound_in_migration_core_and_router(self) -> None:
        migration = (
            ROOT / "migrations" / "0005_f04_model_disclosure.sql"
        ).read_text("utf-8")
        self.assertIn("recipient_fingerprint char(64) NOT NULL", migration)
        disclosure = (
            ROOT / "src" / "personal_assistant" / "core" / "models" / "disclosure.py"
        ).read_text("utf-8")
        for marker in (
            "def recipient_fingerprint(",
            "class DisclosureRecipientUnknownError",
            "recipient_fingerprint: str",
        ):
            self.assertIn(marker, disclosure)
        router = (
            ROOT / "src" / "personal_assistant" / "core" / "models" / "router.py"
        ).read_text("utf-8")
        self.assertIn("recipient_fingerprint(provider.recipient)", router)
        self.assertIn("run_cleanup", router)

    def test_response_metadata_is_never_trusted(self) -> None:
        adapters = ROOT / "src" / "personal_assistant" / "infrastructure" / "models"
        base = (adapters / "base.py").read_text("utf-8")
        # The configured model id is the only identity: provider-controlled
        # response text must not become ``ModelOutput.model_id``.
        self.assertIn("model_id=recipient.model_id", base)
        self.assertNotIn('payload.get("model")', base)
        self.assertIn("AUDITABLE_USAGE_KEYS", (
            ROOT / "src" / "personal_assistant" / "core" / "models" / "router.py"
        ).read_text("utf-8"))
        self.assertIn("bounded_usage", (
            ROOT / "src" / "personal_assistant" / "core" / "models" / "provider.py"
        ).read_text("utf-8"))
        # The vendor body is never parsed for an error string.
        for path in sorted(adapters.rglob("*.py")):
            self.assertNotIn("vendor_error_code", path.read_text("utf-8"))

    def test_classification_is_canonicalized_and_consent_ids_are_authorized(self) -> None:
        models = ROOT / "src" / "personal_assistant" / "core" / "models"
        provider = (models / "provider.py").read_text("utf-8")
        self.assertIn("def classification_of(", provider)
        self.assertIn("def is_secret_classification(", provider)
        for name in ("router.py", "disclosure.py"):
            self.assertIn("is_secret_classification", (models / name).read_text("utf-8"))
        base = (
            ROOT / "src" / "personal_assistant" / "infrastructure" / "models" / "base.py"
        ).read_text("utf-8")
        self.assertIn("is_secret_classification", base)
        self.assertIn("the model response is malformed", base)
        router = (models / "router.py").read_text("utf-8")
        # Audit records only the consent the authorizer returned and the call
        # actually used, never the caller-supplied id.
        self.assertIn("used_consent_id = decision.consent_id", router)
        self.assertIn("consent_id=used_consent_id", router)

    def test_recipient_identity_is_frozen_and_requests_are_validated(self) -> None:
        base = (
            ROOT / "src" / "personal_assistant" / "infrastructure" / "models" / "base.py"
        ).read_text("utf-8")
        # URL, payload model and recipient share one request-scoped snapshot,
        # and a repointed adapter is refused by the router.
        self.assertIn("recipient = self._recipient", base)
        self.assertIn('url = f"{recipient.endpoint}', base)
        self.assertIn("self._recipient = RecipientIdentity(", base)
        self.assertNotIn("self._base_url =", base)
        self.assertIn("del request, payload, headers", base)
        self.assertIn("malformed_request", base)
        router = (
            ROOT / "src" / "personal_assistant" / "core" / "models" / "router.py"
        ).read_text("utf-8")
        self.assertIn("_recipient_identities", router)
        self.assertIn("recipient_fingerprint(provider.recipient)", router)
        provider = (
            ROOT / "src" / "personal_assistant" / "core" / "models" / "provider.py"
        ).read_text("utf-8")
        self.assertIn("def coerce_context_field(", provider)
        self.assertIn("model request {name} must be a string", provider)
        # Fields are always written back from the normalized tuple; no equality
        # check (a duck field's ``__eq__`` could keep a mutable object alive).
        self.assertIn('object.__setattr__(self, "fields", normalized)', provider)
        self.assertNotIn("!= self.fields", provider)

    def test_disclosure_contract_is_documented(self) -> None:
        text = (ROOT / "docs" / "CONTRACTS_AND_INTERFACES.md").read_text("utf-8")
        for marker in (
            "DisclosureConsentService",
            "model_disclosure_consents",
            "model_disclosure_commands",
            "DisclosureAuthorizer",
            "disclosure_preview_mismatch",
            "disclosure_denied",
            "disclosure_recipient_unknown",
            "idempotency_conflict",
            "0005_f04_model_disclosure",
            "recipient_fingerprint",
            "trust_env",
            "rejection_code",
            "CANONICAL",
        ):
            self.assertIn(marker, text)

    def test_no_plaintext_api_keys_in_source_or_fixtures(self) -> None:
        pattern = re.compile(r"sk-(?:live|proj)-[A-Za-z0-9]{8,}")
        offenders = []
        for base in ("src", "tests"):
            for path in (ROOT / base).rglob("*.py"):
                if pattern.search(path.read_text("utf-8")):
                    offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
