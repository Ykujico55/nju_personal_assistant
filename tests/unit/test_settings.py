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
