from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from personal_assistant.settings import ConfigurationError, Settings


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


if __name__ == "__main__":
    unittest.main()
