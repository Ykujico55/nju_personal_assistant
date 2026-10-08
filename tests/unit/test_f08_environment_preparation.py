"""Safety cases for non-deploying F08.6 environment preparation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "prepare_f08.py"


class F08EnvironmentPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def run_script(
        self, *arguments: str, environment: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env=environment,
        )

    def configuration(self, **overrides: str) -> Path:
        values = {
            "PA_ENVIRONMENT": "production",
            "PA_STORAGE_BACKEND": "postgres",
            "PA_TRUST_CLOUDFLARE_ACCESS": "true",
            "PA_PUBLIC_HOST": "127.0.0.1",
            "PA_ADMIN_HOST": "127.0.0.1",
            "PA_HEALTH_HOST": "127.0.0.1",
            "PA_PUBLIC_ORIGIN": "https://assistant.example.test",
            "PA_CF_ACCESS_TEAM_DOMAIN": "team.cloudflareaccess.com",
            "PA_CF_ACCESS_AUD": "application-audience-tag",
            # Port 1 deliberately has no database: this must be a syntax check.
            "PA_DATABASE_URL": (
                "postgresql+asyncpg://fixture:synthetic-private-value@127.0.0.1:1/assistant"
            ),
            **overrides,
        }
        path = self.directory / "candidate.env"
        path.write_text("\n".join(f"{key}={value}" for key, value in values.items()), "utf-8")
        return path

    def test_prepare_creates_incomplete_templates_without_live_environment(self) -> None:
        result = self.run_script("prepare", "--output-dir", str(self.directory))
        self.assertEqual(0, result.returncode, result.stderr)
        template = (self.directory / ".env.template").read_text("utf-8")
        self.assertIn("PA_ENVIRONMENT=production", template)
        self.assertIn("PA_TRUST_CLOUDFLARE_ACCESS=true", template)
        self.assertIn("PA_MAIL_SEND_ENABLED=false", template)
        self.assertIn("PA_BROWSER_SUBMIT_ENABLED=false", template)
        self.assertIn("PA_DATABASE_URL=\n", template)
        self.assertNotIn("change-me", template)
        self.assertFalse((self.directory / ".env").exists())
        tunnel = (self.directory / "cloudflared.template.yml").read_text("utf-8")
        self.assertIn("http://127.0.0.1:8000", tunnel)
        self.assertIn("http_status:404", tunnel)
        self.assertNotIn("8001", tunnel)
        self.assertNotIn("8010", tunnel)

    def test_prepare_refuses_to_overwrite_existing_material(self) -> None:
        preserved = self.directory / ".env.template"
        preserved.write_text("local material must survive", "utf-8")
        result = self.run_script("prepare", "--output-dir", str(self.directory))
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("local material must survive", preserved.read_text("utf-8"))
        self.assertFalse((self.directory / "cloudflared.template.yml").exists())

    def test_prepare_rejects_loopback_public_domain_before_writing(self) -> None:
        result = self.run_script(
            "prepare", "--output-dir", str(self.directory),
            "--public-origin", "https://127.0.0.1",
        )
        self.assertNotEqual(0, result.returncode)
        self.assertEqual([], list(self.directory.iterdir()))

    def test_check_validates_configuration_without_contacting_database(self) -> None:
        path = self.configuration()
        result = self.run_script("check", "--env-file", str(path))
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["local_configuration_valid"])
        self.assertEqual("not_probed", report["database"])
        self.assertEqual("not_verified", report["deployed_access_policy"])
        self.assertEqual("unavailable_default", report["push_secret_backend"])
        self.assertNotIn("synthetic-private-value", result.stdout + result.stderr)

    def test_check_does_not_echo_invalid_configuration_values(self) -> None:
        path = self.configuration(PA_PUBLIC_PORT="do-not-echo-private-marker")
        result = self.run_script("check", "--env-file", str(path))
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(json.loads(result.stdout)["local_configuration_valid"])
        self.assertNotIn("do-not-echo-private-marker", result.stdout + result.stderr)
        self.assertNotIn("synthetic-private-value", result.stdout + result.stderr)

    def test_check_rejects_network_listener_even_with_access_enabled(self) -> None:
        result = self.run_script(
            "check", "--env-file", str(self.configuration(PA_PUBLIC_HOST="0.0.0.0"))
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("LISTENER_MISMATCH", result.stdout)

    def test_check_rejects_example_database_password(self) -> None:
        result = self.run_script(
            "check", "--env-file", str(self.configuration(
                PA_DATABASE_URL="postgresql+asyncpg://fixture:change-me@127.0.0.1:1/assistant"
            )),
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("DATABASE_PLACEHOLDER", result.stdout)

    def test_check_rejects_invalid_database_port(self) -> None:
        path = self.configuration(
            PA_DATABASE_URL="postgresql+asyncpg://fixture:synthetic@127.0.0.1:not-a-port/assistant"
        )
        result = self.run_script("check", "--env-file", str(path))
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(json.loads(result.stdout)["local_configuration_valid"])

    def test_check_rejects_environment_substitution_that_changes_at_launch(self) -> None:
        path = self.configuration(
            PA_DATABASE_URL="postgresql+asyncpg://fixture:${MISSING_VALUE}@127.0.0.1:5432/assistant"
        )
        result = self.run_script("check", "--env-file", str(path))
        self.assertNotEqual(0, result.returncode)
        self.assertIn("ENVIRONMENT_SUBSTITUTION", result.stdout)

    def test_check_rejects_encoded_example_password(self) -> None:
        path = self.configuration(
            PA_DATABASE_URL="postgresql+asyncpg://fixture:%63hange-me@127.0.0.1:5432/assistant"
        )
        result = self.run_script("check", "--env-file", str(path))
        self.assertNotEqual(0, result.returncode)
        self.assertIn("DATABASE_PLACEHOLDER", result.stdout)

    def test_check_rejects_inherited_configuration_that_would_override_file(self) -> None:
        result = self.run_script(
            "check", "--env-file", str(self.configuration()),
            environment={**os.environ, "PA_ENVIRONMENT": "development",
                         "PA_MODEL_REMOTE_SECRET_HANDLE": "do-not-echo-inherited-marker"},
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("AMBIENT_CONFIGURATION_CONFLICT", result.stdout)
        self.assertNotIn("do-not-echo-inherited-marker", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
