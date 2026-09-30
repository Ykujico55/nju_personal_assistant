"""The live capture report must not persist session query values."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from scripts import ehall_real_acceptance
from scripts.ehall_real_acceptance import (
    _bounded_snapshot,
    _capture_path,
    _diagnostic_summary,
)


class RealAcceptanceReportTests(unittest.TestCase):
    def test_volunteer_dropdowns_are_checked_in_page_order(self) -> None:
        from scripts.ehall_real_acceptance import (
            _first_real_option,
            _option_value_by_label,
            _volunteer_dropdown_status,
            _volunteer_match_flags,
        )

        controls = [{"value": ""} for _ in range(9)]
        controls[7] = {"value": "college-1", "options": ["", "college-1"]}
        controls[8] = {"value": "", "options": ["", "reviewer-1"]}
        self.assertEqual((False, False), _volunteer_dropdown_status(controls))
        controls[5] = {"value": "测试学院"}
        self.assertEqual((True, False), _volunteer_dropdown_status(controls))
        self.assertEqual("reviewer-1", _first_real_option(controls[8]["options"]))
        self.assertEqual(
            "college-2",
            _option_value_by_label(
                {
                    "options": ["", "college-1", "college-2"],
                    "option_labels": ["请选择", "研究生院", "文学院"],
                },
                "文学院",
            ),
        )
        controls[6] = {"value": "测试审核人"}
        controls[8]["value"] = "reviewer-1"
        self.assertEqual((True, True), _volunteer_dropdown_status(controls))
        self.assertEqual(
            {"college": True, "reviewer": True},
            _volunteer_match_flags(
                controls, {"college": "college-1", "reviewer": "reviewer-1"}
            ),
        )
        controls[8]["value"] = ""
        self.assertEqual(
            {"college": True, "reviewer": False},
            _volunteer_match_flags(
                controls, {"college": "college-1", "reviewer": "reviewer-1"}
            ),
        )

    def test_volunteer_sample_uses_date_only_for_end_time(self) -> None:
        from scripts.ehall_real_acceptance import _volunteer_sample_values

        values = _volunteer_sample_values("college-2")
        self.assertEqual("2026-09-28", values["end"])
        self.assertEqual("college-2", values["college"])

    def test_volunteer_preview_schema_pins_all_nine_controls_without_submit_target(self) -> None:
        from scripts.ehall_real_acceptance import _volunteer_descriptor

        controls = [
            {"locator": f"ctl:0:{i}", "tag": "input", "type": "text", "name": name,
             "readonly": i >= 5, "options": []}
            for i, name in enumerate(
                ("data.mc", "data.kssj", "data.jssj", "preview-bdx", "preview-bdx", "", "")
            )
        ]
        controls.extend(
            {"locator": f"ctl:1:{i}", "tag": "select", "type": "select", "name": name,
             "readonly": False, "options": ["", "college-1"]}
            for i, name in enumerate(("data.shxy.id", ""))
        )
        descriptor = _volunteer_descriptor(
            {"structure": {"controls": controls}},
            origin="https://youth.nju.edu.cn",
            fingerprint="a" * 64,
        )
        self.assertEqual(9, len(descriptor.fields))
        self.assertEqual(
            ("名称", "开始时间", "结束时间", "团队职务", "服务时长", "审核学院"),
            tuple(item.label for item in descriptor.fields if item.required),
        )
        self.assertIsNone(descriptor.final_action())

    def test_diagnostics_include_only_bounded_blocked_request_metadata(self) -> None:
        summary = _diagnostic_summary(
            {
                "sessions": [
                    {
                        "driver": {
                            "blocked_origin_requests": 0,
                            "blocked_mutating_requests": 2,
                            "blocked_request_samples": [
                                {
                                    "method": "POST",
                                    "resource_type": "xhr",
                                    "endpoint": "queryTableConfig.do",
                                    "path_id": "0123456789abcdef",
                                    "count": 2,
                                    "headers": {"referer": "gid_=session-secret"},
                                },
                                {
                                    "method": "POST",
                                    "resource_type": "xhr",
                                    "endpoint": "secret-1234.do",
                                    "path_id": "bad?query",
                                    "count": 1,
                                },
                            ],
                        }
                    }
                ]
            }
        )
        self.assertEqual(2, summary["blocked_mutating_requests"])
        self.assertEqual(
            [
                {
                    "method": "POST",
                    "resource_type": "xhr",
                    "endpoint": "queryTableConfig.do",
                    "path_id": "0123456789abcdef",
                    "count": 2,
                }
            ],
            summary["blocked_request_samples"],
        )
        self.assertNotIn("session-secret", repr(summary))

    def test_capture_cli_defaults_to_open_origin_mode(self) -> None:
        with patch("sys.argv", ["ehall_real_acceptance.py", "capture"]), patch.object(
            ehall_real_acceptance, "capture", new_callable=AsyncMock, return_value=0
        ) as capture:
            self.assertEqual(0, ehall_real_acceptance.main())
        self.assertEqual("open", capture.await_args.args[0].origin_mode)

    def test_capture_scrubs_query_and_control_values(self) -> None:
        raw = {
            "url": "https://ehallapp.nju.edu.cn/app/index.do?t_s=123&gid_=private",
            "title": "事务",
            "structure": {
                "controls": [{"name": "reason", "value": "personal detail"}],
                "headings": [],
                "links": [],
                "forms": [
                    {"action": "/app/submit?ticket=private", "method": "post"}
                ],
                "hidden_fields": [{"type": "hidden", "name": "csrf"}],
            },
        }
        report = _bounded_snapshot(raw)
        self.assertEqual(
            "https://ehallapp.nju.edu.cn/app/index.do", report["url"]
        )
        self.assertEqual("", report["structure"]["controls"][0]["value"])
        self.assertEqual(
            "/app/submit", report["structure"]["forms"][0]["action"]
        )
        self.assertEqual(
            [{"type": "hidden", "name": "csrf"}],
            report["structure"]["hidden_fields"],
        )
        self.assertEqual("/app/index.do", _capture_path(raw["url"]))
