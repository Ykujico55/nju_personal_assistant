"""F02: keep the written contract consistent with the implemented semantics.

These checks are deliberately textual: they guard the specific statements the
independent audits found drifting (replay dependency on 0004, cancellation
semantics, verifier worker cleanup).
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class F02ContractConsistencyTests(unittest.TestCase):
    def test_cross_restart_replay_is_documented_as_request_scope(self) -> None:
        text = (ROOT / "docs" / "CONTRACTS_AND_INTERFACES.md").read_text("utf-8")
        self.assertIn("0004_f02_operation_request_scope", text)
        self.assertIn("request_scope", text)
        self.assertNotIn("依赖 0003", text)

    def test_cancellation_semantics_are_documented_as_reraise(self) -> None:
        text = (ROOT / "docs" / "CONTRACTS_AND_INTERFACES.md").read_text("utf-8")
        self.assertIn("重新抛出 `CancelledError`", text)
        self.assertIn("不得伪装成普通 RPC 错误", text)
        self.assertNotIn("读 EOF、取消都会终止进程并抛类型化", text)

    def test_contract_verifier_shields_its_worker_cleanup(self) -> None:
        source = (
            ROOT
            / "src"
            / "personal_assistant"
            / "infrastructure"
            / "extensions"
            / "processes.py"
        ).read_text("utf-8")
        self.assertIn("await shield_cleanup(worker.close())", source)

    def test_rpc_call_and_drain_both_use_a_single_deadline(self) -> None:
        source = (
            ROOT / "src" / "personal_assistant" / "core" / "extensions" / "rpc.py"
        ).read_text("utf-8")
        self.assertEqual(2, source.count("asyncio.timeout_at(deadline)"))


if __name__ == "__main__":
    unittest.main()
