from __future__ import annotations

import unittest

from personal_assistant.core.agent.progress_detector import ProgressDetector
from personal_assistant.core.agent.safety_fuse import EmergencyFuse
from personal_assistant.domain import (
    TaskRun,
    ToolCall,
    ToolOutcome,
)


class SafetyFuseTests(unittest.TestCase):
    def test_third_identical_no_progress_call_reaches_hard_limit(self) -> None:
        detector = ProgressDetector()
        fuse = EmergencyFuse()
        run = TaskRun(id="run", task_id="task", objective="objective")
        call = ToolCall(
            tool_id="example.read",
            tool_version="1",
            arguments={"same": True},
            task_id="task",
        )
        for _ in range(3):
            run = detector.record(run, call, ToolOutcome.success(None))
        self.assertEqual(3, run.same_call_without_progress)
        self.assertEqual("SAME_CALL_WITHOUT_PROGRESS", fuse.pause_reason(run))


if __name__ == "__main__":
    unittest.main()

