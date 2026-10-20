from __future__ import annotations

import json
import unittest
from pathlib import Path

from personal_assistant.core.workflows import next_proposal, validate_workflow

ROOT = (
    Path(__file__).resolve().parents[2]
    / "extensions/assistant_workflows/src/assistant_workflows/data"
)


class DeliveryFlowTests(unittest.TestCase):
    def test_reply_flow_uses_only_registered_tools_and_asks_before_send(self):
        flow = validate_workflow(json.loads((ROOT / "mail_reply.json").read_text(encoding="utf-8")))
        state = {}
        inputs = {
            "account_id": "fixture",
            "query": "synthetic evidence",
            "to": "recipient@example.test",
            "subject": "fixture",
            "body_text": "fixture body",
        }
        invoked = []
        while True:
            decision = next_proposal(flow, state, inputs)
            if decision["kind"] == "NEEDS_USER":
                self.assertEqual("review_draft", state["pending_input"]["field"])
                inputs["review_draft"] = True
                continue
            if decision["kind"] == "COMPLETE":
                break
            invoked.append(decision["tool_id"])
            if decision["tool_id"] == "smail.prepare_reply":
                state.setdefault("outputs", {})["draft"] = {
                    "draft_id": "d",
                    "version": 1,
                    "canonical_digest": "0" * 64,
                    "local_action_id": "fixture-action",
                    "message_id": "<fixture@test>",
                    "from_address": "sender@example.test",
                    "to": [inputs["to"]],
                    "subject": "fixture",
                    "mime_sha256": "1" * 64,
                    "account_id": "fixture",
                    "account_fingerprint": "2" * 64,
                }
            else:
                state.setdefault("outputs", {})[state["step_id"]] = {"unknown": True, "results": []}
            state["cursor"] += 1
        self.assertEqual(
            ["smail.search", "knowledge.search", "smail.prepare_reply", "smail.send"], invoked
        )
        self.assertNotIn("ehall.submit", invoked)

    def test_form_prepare_and_final_submit_are_separate_flows_and_no_scripts(self):
        flow = validate_workflow(
            json.loads((ROOT / "form_prepare.json").read_text(encoding="utf-8"))
        )
        submit = validate_workflow(
            json.loads((ROOT / "form_submit.json").read_text(encoding="utf-8"))
        )
        ids = [step.get("tool_id") for step in flow["steps"]]
        self.assertIn("smail.search", ids)
        self.assertIn("knowledge.search", ids)
        self.assertIn("ehall.fill_form", ids)
        self.assertNotIn("ehall.submit", ids)
        self.assertIn("ehall.submit", [step.get("tool_id") for step in submit["steps"]])
        self.assertNotIn("javascript", json.dumps(flow).lower())
