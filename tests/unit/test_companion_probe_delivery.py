from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.support.companion_process import pid_alive


class CompanionPidProbeTests(unittest.TestCase):
    def test_localized_tasklist_bytes_keep_exact_pid_without_utf8_decoding(self):
        output = b'"\xd0\xb3chrome.exe","1234","Console","1","10 K"\r\n'
        with patch("tests.support.companion_process.sys.platform", "win32"), patch(
            "tests.support.companion_process.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout=output),
        ) as run:
            self.assertTrue(pid_alive(1234))
            self.assertFalse(pid_alive(123))
            self.assertFalse(run.call_args.kwargs.get("text", False))

    def test_failed_process_probe_cannot_claim_the_browser_was_reaped(self):
        with patch("tests.support.companion_process.sys.platform", "win32"), patch(
            "tests.support.companion_process.subprocess.run",
            return_value=SimpleNamespace(returncode=1, stdout=b""),
        ), self.assertRaises(RuntimeError):
            pid_alive(1234)
