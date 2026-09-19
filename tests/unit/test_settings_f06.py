"""F06 settings: mail send policy and controlled-recipient normalization."""

from __future__ import annotations

import unittest
from pathlib import Path

from personal_assistant.settings import ConfigurationError, Settings, normalize_mail_recipients


def base_settings(**overrides: object) -> Settings:
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
        "database_url": "postgresql://assistant:change-me@127.0.0.1:5432/assistant",
        "extension_root": Path("./var/extensions"),
        "artifact_root": Path("./var/artifacts"),
        "trust_cloudflare_access": False,
        "public_origin": None,
        "cf_access_team_domain": None,
        "cf_access_aud": None,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


class MailSettingsTests(unittest.TestCase):
    def test_send_disabled_by_default(self) -> None:
        settings = base_settings()
        self.assertFalse(settings.mail_send_enabled)
        self.assertEqual((), settings.mail_test_recipients)

    def test_send_enabled_requires_a_controlled_recipient(self) -> None:
        with self.assertRaises(ConfigurationError):
            base_settings(mail_send_enabled=True)

    def test_recipients_are_normalized_and_deduplicated(self) -> None:
        settings = base_settings(
            mail_send_enabled=True,
            mail_test_recipients=("Me@Example.com", "me@example.com", "other@example.com"),
        )
        self.assertEqual(("me@example.com", "other@example.com"), settings.mail_test_recipients)

    def test_invalid_recipient_is_rejected(self) -> None:
        with self.assertRaises(ConfigurationError):
            normalize_mail_recipients(("not an address",))

    def test_message_size_bounds(self) -> None:
        with self.assertRaises(ConfigurationError):
            base_settings(mail_max_message_bytes=1024)
        with self.assertRaises(ConfigurationError):
            base_settings(mail_max_message_bytes=64 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
