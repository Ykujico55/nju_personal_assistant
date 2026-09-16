"""F03: keep the Cloudflare Access boundary consistent with its written contract.

These checks are deliberately textual: they guard dependency placement, the
"never trust email headers" rule, the stable error codes and the absence of
hard-coded tokens in fixtures.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class F03ContractConsistencyTests(unittest.TestCase):
    def test_runtime_dependencies_declare_async_http_and_crypto_stacks(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text("utf-8")
        lock = (ROOT / "dependency.lock").read_text("utf-8")
        for package in ("httpx", "PyJWT", "cryptography"):
            self.assertIn(package, pyproject)
            self.assertIn(f"{package}==", lock)

    def test_core_and_domain_never_import_crypto_or_http_stacks(self) -> None:
        markers = (
            "import jwt",
            "from jwt",
            "import httpx",
            "from httpx",
            "import cryptography",
            "from cryptography",
        )
        offenders = []
        for layer in ("core", "domain"):
            for path in (ROOT / "src" / "personal_assistant" / layer).rglob("*.py"):
                text = path.read_text("utf-8")
                if any(marker in text for marker in markers):
                    offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([], offenders)

    def test_api_layer_does_not_import_infrastructure(self) -> None:
        offenders = [
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "src" / "personal_assistant" / "api").rglob("*.py")
            if "personal_assistant.infrastructure" in path.read_text("utf-8")
        ]
        self.assertEqual([], offenders)

    def test_identity_contract_lives_in_core_auth(self) -> None:
        core_ports = (
            ROOT / "src" / "personal_assistant" / "core" / "auth" / "ports.py"
        ).read_text("utf-8")
        for marker in (
            "class AccessIdentity",
            "class AccessTokenVerifier",
            "class AccessTokenRejectedError",
            "class AccessTokenUnavailableError",
        ):
            self.assertIn(marker, core_ports)
        contract = (
            ROOT
            / "src"
            / "personal_assistant"
            / "infrastructure"
            / "auth"
            / "contract.py"
        ).read_text("utf-8")
        self.assertIn("from personal_assistant.core.auth import", contract)
        self.assertIn("class JwksProvider", contract)
        self.assertIn("class UnknownSigningKeyError", contract)
        self.assertNotIn("class AccessIdentity", contract)
        self.assertNotIn("class AccessTokenVerifier", contract)

    def test_next_steps_names_the_correct_interface_owner(self) -> None:
        text = (ROOT / "docs" / "NEXT_STEPS.md").read_text("utf-8")
        self.assertIn("core/auth/ports.py", text)
        self.assertNotIn("infrastructure/auth/contract.py`：`AccessIdentity", text)

    def test_middleware_consumes_the_core_auth_port(self) -> None:
        source = (
            ROOT
            / "src"
            / "personal_assistant"
            / "api"
            / "middleware"
            / "cloudflare_access.py"
        ).read_text("utf-8")
        self.assertIn("from personal_assistant.core.auth import", source)

    def test_identity_is_never_derived_from_email_headers(self) -> None:
        forbidden = "Cf-Access-Authenticated-User-Email"
        offenders = [
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "src").rglob("*.py")
            if forbidden in path.read_text("utf-8")
        ]
        self.assertEqual([], offenders)

    def test_access_tokens_are_never_hard_coded_in_source_or_fixtures(self) -> None:
        pattern = re.compile(r"eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.")
        offenders = []
        for base in ("src", "tests"):
            for path in (ROOT / base).rglob("*.py"):
                if pattern.search(path.read_text("utf-8")):
                    offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([], offenders)

    def test_docs_document_the_f03_carriers_errors_and_cache_bounds(self) -> None:
        text = (ROOT / "docs" / "CONTRACTS_AND_INTERFACES.md").read_text("utf-8")
        for marker in (
            "Cf-Access-Jwt-Assertion",
            "CF_Authorization",
            "/cdn-cgi/access/certs",
            "CLOUDFLARE_ACCESS_TOKEN_MISSING",
            "CLOUDFLARE_ACCESS_TOKEN_INVALID",
            "CLOUDFLARE_ACCESS_UNAVAILABLE",
            "CloudflareJwksProvider",
            "core/auth",
        ):
            self.assertIn(marker, text)

    def test_middleware_does_not_verify_signatures_itself(self) -> None:
        source = (
            ROOT
            / "src"
            / "personal_assistant"
            / "api"
            / "middleware"
            / "cloudflare_access.py"
        ).read_text("utf-8")
        self.assertNotIn("jwt.decode", source)
        self.assertNotIn("import jwt", source)


if __name__ == "__main__":
    unittest.main()
