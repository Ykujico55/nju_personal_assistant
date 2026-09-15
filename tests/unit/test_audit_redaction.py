from __future__ import annotations

import unittest

from personal_assistant.core.audit import redact


class AuditRedactionTests(unittest.TestCase):
    def test_nested_secret_keys_are_redacted(self) -> None:
        result = redact(
            {
                "safe": "ok",
                "nested": [{"token": "abc", "cookie": "session", "count": 1}],
            }
        )
        self.assertEqual("ok", result["safe"])
        self.assertEqual("[REDACTED]", result["nested"][0]["token"])
        self.assertEqual("[REDACTED]", result["nested"][0]["cookie"])


if __name__ == "__main__":
    unittest.main()

