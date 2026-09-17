from __future__ import annotations

import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from personal_assistant.settings import ConfigurationError, Settings


def direct_settings_values(**overrides: object) -> dict[str, object]:
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
        "trust_cloudflare_access": True,
        "public_origin": "https://assistant.example.test",
        "cf_access_team_domain": "https://team.cloudflareaccess.com",
        "cf_access_aud": "application-audience-tag",
    }
    values.update(overrides)
    return values


def trusted_environment(**overrides: str) -> dict[str, str]:
    environment = {
        "PA_TRUST_CLOUDFLARE_ACCESS": "true",
        "PA_CF_ACCESS_TEAM_DOMAIN": "team.cloudflareaccess.com",
        "PA_CF_ACCESS_AUD": "application-audience-tag",
        "PA_PUBLIC_ORIGIN": "https://assistant.example.test",
        **overrides,
    }
    return environment


class SettingsTests(unittest.TestCase):
    def test_production_refuses_memory_storage(self) -> None:
        with patch.dict(
            os.environ,
            {"PA_ENVIRONMENT": "production", "PA_STORAGE_BACKEND": "memory"},
            clear=True,
        ), self.assertRaises(ConfigurationError):
            Settings.from_env()

    def test_admin_must_be_loopback(self) -> None:
        with patch.dict(
            os.environ, {"PA_ADMIN_HOST": "0.0.0.0"}, clear=True
        ), self.assertRaises(ConfigurationError):
            Settings.from_env()

    def test_development_defaults_are_safe(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
        self.assertEqual("memory", settings.storage_backend)
        self.assertEqual("127.0.0.1", settings.admin_host)
        self.assertEqual("127.0.0.1", settings.health_host)

    def test_unauthenticated_public_listener_must_stay_on_loopback(self) -> None:
        with patch.dict(
            os.environ, {"PA_PUBLIC_HOST": "0.0.0.0"}, clear=True
        ), self.assertRaises(ConfigurationError):
            Settings.from_env()

    def test_trusted_mode_normalizes_team_domain_forms(self) -> None:
        for raw in (
            "team",
            "team.cloudflareaccess.com",
            "TEAM.CloudflareAccess.com",
            "https://team.cloudflareaccess.com",
            "https://team.cloudflareaccess.com/",
        ):
            with self.subTest(raw=raw):
                with patch.dict(
                    os.environ,
                    trusted_environment(PA_CF_ACCESS_TEAM_DOMAIN=raw),
                    clear=True,
                ):
                    settings = Settings.from_env()
                self.assertEqual(
                    "https://team.cloudflareaccess.com", settings.cf_access_team_domain
                )

    def test_illegal_team_domains_are_rejected(self) -> None:
        for raw in (
            "http://team.cloudflareaccess.com",
            "https://team.cloudflareaccess.com:8443",
            "https://user:secret@team.cloudflareaccess.com",
            "https://team.cloudflareaccess.com/cdn-cgi/access/certs",
            "https://team.cloudflareaccess.com?next=evil",
            "https://team.cloudflareaccess.com#fragment",
            "https://evil.example.test",
            "https://team.cloudflareaccess.com.evil.example.test",
            "https://127.0.0.1",
            "https://localhost",
            "https://bad_label.cloudflareaccess.com",
            "-team.cloudflareaccess.com",
            "team-.cloudflareaccess.com",
            "https://team.cloudflareaccess.com/../evil",
            "ftp://team.cloudflareaccess.com",
        ):
            with self.subTest(raw=raw), patch.dict(
                os.environ,
                trusted_environment(PA_CF_ACCESS_TEAM_DOMAIN=raw),
                clear=True,
            ), self.assertRaises(ConfigurationError):
                Settings.from_env()

    def test_illegal_audience_is_rejected(self) -> None:
        for raw in ("   ", "has space", "line\nbreak"):
            with self.subTest(raw=raw), patch.dict(
                os.environ,
                trusted_environment(PA_CF_ACCESS_AUD=raw),
                clear=True,
            ), self.assertRaises(ConfigurationError):
                Settings.from_env()

    def test_illegal_public_origin_is_rejected(self) -> None:
        for raw in (
            "http://assistant.example.test",
            "https://user:secret@assistant.example.test",
            "https://assistant.example.test/oauth",
            "https://assistant.example.test?debug=1",
            "https://assistant.example.test#fragment",
        ):
            with self.subTest(raw=raw), patch.dict(
                os.environ,
                trusted_environment(PA_PUBLIC_ORIGIN=raw),
                clear=True,
            ), self.assertRaises(ConfigurationError):
                Settings.from_env()

    def test_public_origin_is_normalized(self) -> None:
        with patch.dict(
            os.environ,
            trusted_environment(PA_PUBLIC_ORIGIN="https://Assistant.Example.Test/"),
            clear=True,
        ):
            settings = Settings.from_env()
        self.assertEqual("https://assistant.example.test", settings.public_origin)

    def test_trusted_mode_requires_all_cloudflare_values(self) -> None:
        for missing in ("PA_CF_ACCESS_TEAM_DOMAIN", "PA_CF_ACCESS_AUD", "PA_PUBLIC_ORIGIN"):
            with self.subTest(missing=missing):
                environment = trusted_environment()
                del environment[missing]
                with patch.dict(os.environ, environment, clear=True), self.assertRaises(
                    ConfigurationError
                ):
                    Settings.from_env()

    def test_production_refuses_missing_cloudflare_configuration(self) -> None:
        environment = {
            "PA_ENVIRONMENT": "production",
            "PA_STORAGE_BACKEND": "postgres",
        }
        with patch.dict(os.environ, environment, clear=True), self.assertRaises(
            ConfigurationError
        ):
            Settings.from_env()

    def test_production_rejects_invalid_team_domain(self) -> None:
        environment = trusted_environment(
            PA_ENVIRONMENT="production",
            PA_STORAGE_BACKEND="postgres",
            PA_CF_ACCESS_TEAM_DOMAIN="https://evil.example.test",
        )
        with patch.dict(os.environ, environment, clear=True), self.assertRaises(
            ConfigurationError
        ):
            Settings.from_env()


class ModelSettingsTests(unittest.TestCase):
    def test_remote_model_requires_https_model_and_handle(self) -> None:
        with self.assertRaises(ConfigurationError):
            Settings(**direct_settings_values(model_remote_base_url="https://api.example.test/v1"))
        with self.assertRaises(ConfigurationError):
            Settings(
                **direct_settings_values(
                    model_remote_base_url="https://api.example.test/v1",
                    model_remote_model="gpt-test",
                )
            )
        with self.assertRaises(ConfigurationError):
            Settings(
                **direct_settings_values(
                    model_remote_base_url="http://api.example.test/v1",
                    model_remote_model="gpt-test",
                    model_remote_secret_handle="handle-1",
                )
            )
        for bad in (
            "https://user:pass@api.example.test/v1",
            "https://api.example.test/v1?k=1",
            "https://api.example.test/v1#fragment",
            "https:///v1",
        ):
            with self.subTest(base_url=bad), self.assertRaises(ConfigurationError):
                Settings(
                    **direct_settings_values(
                        model_remote_base_url=bad,
                        model_remote_model="gpt-test",
                        model_remote_secret_handle="handle-1",
                    )
                )

    def test_model_configuration_is_normalized_on_all_paths(self) -> None:
        settings = Settings(
            **direct_settings_values(
                model_remote_provider_id=" remote.openai ",
                model_remote_base_url="https://API.Example.Test/v1/",
                model_remote_model=" gpt-test ",
                model_remote_secret_handle=" Handle-1 ",
                model_local_base_url="http://LOCALHOST:11434/",
                model_local_model="llama3.1:8b",
            )
        )
        self.assertEqual("remote.openai", settings.model_remote_provider_id)
        self.assertEqual("https://api.example.test/v1", settings.model_remote_base_url)
        self.assertEqual("gpt-test", settings.model_remote_model)
        self.assertEqual("Handle-1", settings.model_remote_secret_handle)
        self.assertEqual("http://localhost:11434", settings.model_local_base_url)

        replaced = replace(settings, model_remote_base_url="https://API.Example.Test/v2/")
        self.assertEqual("https://api.example.test/v2", replaced.model_remote_base_url)

    def test_local_model_endpoint_must_be_loopback(self) -> None:
        for bad in (
            "http://10.0.0.5:11434",
            "https://models.example.test",
            "http://127.0.0.1.evil.example.test:11434",
            "http://user:pass@127.0.0.1:11434",
        ):
            with self.subTest(base_url=bad), self.assertRaises(ConfigurationError):
                Settings(
                    **direct_settings_values(
                        model_local_base_url=bad, model_local_model="llama3.1:8b"
                    )
                )

    def test_partial_model_configuration_is_rejected(self) -> None:
        with self.assertRaises(ConfigurationError):
            Settings(**direct_settings_values(model_remote_model="gpt-test"))
        with self.assertRaises(ConfigurationError):
            Settings(**direct_settings_values(model_remote_secret_handle="handle-1"))
        with self.assertRaises(ConfigurationError):
            Settings(**direct_settings_values(model_local_model="llama3.1:8b"))
        with self.assertRaises(ConfigurationError):
            Settings(**direct_settings_values(model_local_fallback_provider_id="local.ollama"))
        with self.assertRaises(ConfigurationError):
            Settings(
                **direct_settings_values(
                    model_local_base_url="http://127.0.0.1:11434",
                    model_local_model="llama3.1:8b",
                    model_local_provider_id="local.other",
                    model_local_fallback_provider_id="local.ollama",
                )
            )

    def test_duplicate_local_and_remote_provider_ids_are_rejected(self) -> None:
        with self.assertRaises(ConfigurationError):
            Settings(
                **direct_settings_values(
                    model_remote_provider_id="model.shared",
                    model_remote_base_url="https://api.example.test/v1",
                    model_remote_model="gpt-test",
                    model_remote_secret_handle="handle-1",
                    model_local_provider_id="model.shared",
                    model_local_base_url="http://127.0.0.1:11434",
                    model_local_model="llama3.1:8b",
                )
            )

    def test_model_endpoint_path_may_not_contain_dot_segments(self) -> None:
        for base_url in (
            "https://api.example.test/v1/../admin",
            "https://api.example.test/./v1",
            "https://api.example.test/v1/%2e%2e/admin",
            "https://api.example.test/v1/%2E%2E/admin",
            "https://api.example.test/v1/%2e/admin",
            "https://api.example.test/v1%2f..%2fadmin",
            "https://api.example.test/v1/%252e%252e/admin",
            "https://api.example.test/%252E%252E/admin",
        ):
            with self.subTest(base_url=base_url), self.assertRaises(ConfigurationError):
                Settings(
                    **direct_settings_values(
                        model_remote_base_url=base_url,
                        model_remote_model="gpt-test",
                        model_remote_secret_handle="handle-1",
                    )
                )
        with self.assertRaises(ConfigurationError):
            Settings(
                **direct_settings_values(
                    model_local_base_url="http://127.0.0.1:11434/../admin",
                    model_local_model="llama3.1:8b",
                )
            )

    def test_model_timeout_bounds(self) -> None:
        for bad in (0.0, 601.0):
            with self.subTest(timeout=bad), self.assertRaises(ConfigurationError):
                Settings(**direct_settings_values(model_remote_timeout_seconds=bad))

    def test_from_env_reads_model_configuration(self) -> None:
        environment = {
            "PA_MODEL_REMOTE_BASE_URL": "https://api.example.test/v1",
            "PA_MODEL_REMOTE_MODEL": "gpt-test",
            "PA_MODEL_REMOTE_SECRET_HANDLE": "handle-1",
            "PA_MODEL_REMOTE_TIMEOUT_SECONDS": "30",
            "PA_MODEL_LOCAL_BASE_URL": "http://127.0.0.1:11434",
            "PA_MODEL_LOCAL_MODEL": "llama3.1:8b",
            "PA_MODEL_LOCAL_FALLBACK_PROVIDER_ID": "local.ollama",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = Settings.from_env()
        self.assertEqual("https://api.example.test/v1", settings.model_remote_base_url)
        self.assertEqual(30.0, settings.model_remote_timeout_seconds)
        self.assertEqual("local.ollama", settings.model_local_fallback_provider_id)

        with patch.dict(
            os.environ,
            {**environment, "PA_MODEL_REMOTE_BASE_URL": "http://api.example.test/v1"},
            clear=True,
        ), self.assertRaises(ConfigurationError):
            Settings.from_env()

    def test_production_requires_complete_remote_model_configuration(self) -> None:
        environment = trusted_environment(
            PA_ENVIRONMENT="production",
            PA_STORAGE_BACKEND="postgres",
            PA_MODEL_REMOTE_BASE_URL="https://api.example.test/v1",
            PA_MODEL_REMOTE_MODEL="gpt-test",
            PA_MODEL_REMOTE_SECRET_HANDLE="handle-1",
        )
        with patch.dict(os.environ, environment, clear=True):
            settings = Settings.from_env()
        self.assertIsNotNone(settings.model_remote_base_url)

        incomplete = dict(environment)
        del incomplete["PA_MODEL_REMOTE_SECRET_HANDLE"]
        with patch.dict(os.environ, incomplete, clear=True), self.assertRaises(
            ConfigurationError
        ):
            Settings.from_env()


class HandBuiltSettingsTests(unittest.TestCase):
    def test_direct_construction_rejects_foreign_team_domain(self) -> None:
        with self.assertRaises(ConfigurationError):
            Settings(**direct_settings_values(cf_access_team_domain="https://attacker.example"))

    def test_direct_construction_rejects_invalid_audience_and_origin(self) -> None:
        with self.assertRaises(ConfigurationError):
            Settings(**direct_settings_values(cf_access_aud="not valid"))
        with self.assertRaises(ConfigurationError):
            Settings(
                **direct_settings_values(public_origin="http://assistant.example.test")
            )

    def test_direct_construction_accepts_canonical_values(self) -> None:
        settings = Settings(**direct_settings_values())
        self.assertEqual(
            "https://team.cloudflareaccess.com", settings.cf_access_team_domain
        )

    def test_direct_construction_writes_back_normalized_values(self) -> None:
        settings = Settings(
            **direct_settings_values(
                cf_access_team_domain=" TEAM.CloudflareAccess.com ",
                cf_access_aud=" application-audience-tag ",
                public_origin="https://Assistant.Example.Test/",
            )
        )
        self.assertEqual(
            "https://team.cloudflareaccess.com", settings.cf_access_team_domain
        )
        self.assertEqual("application-audience-tag", settings.cf_access_aud)
        self.assertEqual("https://assistant.example.test", settings.public_origin)

    def test_replace_writes_back_normalized_values(self) -> None:
        settings = Settings(**direct_settings_values())
        replaced = replace(
            settings,
            cf_access_team_domain="TEAM.cloudflareaccess.com",
            cf_access_aud="  application-audience-tag  ",
        )
        self.assertEqual(
            "https://team.cloudflareaccess.com", replaced.cf_access_team_domain
        )
        self.assertEqual("application-audience-tag", replaced.cf_access_aud)

    def test_replace_also_validates(self) -> None:
        settings = Settings(**direct_settings_values())
        with self.assertRaises(ConfigurationError):
            replace(settings, cf_access_team_domain="https://attacker.example")


if __name__ == "__main__":
    unittest.main()
