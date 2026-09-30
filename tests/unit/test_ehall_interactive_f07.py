"""Interactive instruction parsing and control selection."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from personal_assistant.ehall_interactive_entry import _redacted_capture, run_ehall
from personal_assistant.infrastructure.browser.interactive import (
    Control,
    EhallInteractiveFlow,
    ObservedField,
    match_control,
    parse_instruction,
    sample_values,
    split_scoped_action_step,
)


class InteractiveInstructionTests(unittest.TestCase):
    def test_live_capture_excludes_form_values_and_dynamic_url_tokens(self) -> None:
        raw = {
            "url": "https://ehallapp.nju.edu.cn/form?gid_=secret-gid",
            "title": "志愿服务经历申请",
            "actions": [{"locator": "act:0", "label": "提交", "kind": "submit"}],
            "structure": {
                "controls": [
                    {
                        "locator": "ctl:0:0",
                        "tag": "input",
                        "type": "text",
                        "name": "description",
                        "value": "private-answer",
                        "options": ["private-option"],
                    }
                ],
                "headings": [{"level": 1, "text": "志愿服务经历申请"}],
                "links": [],
                "forms": [
                    {
                        "action": "/save.do?gid_=secret-gid",
                        "method": "post",
                    }
                ],
                "hidden_fields": [{"type": "hidden", "name": "csrf"}],
            },
        }
        captured = _redacted_capture(raw)
        serialized = json.dumps(captured, ensure_ascii=False)
        self.assertNotIn("private-answer", serialized)
        self.assertNotIn("private-option", serialized)
        self.assertNotIn("secret-gid", serialized)
        self.assertEqual("https://ehallapp.nju.edu.cn/form", captured["url"])
        self.assertEqual(64, len(captured["fingerprint"]))
        self.assertEqual(
            {"controls", "headings", "links", "forms", "hidden_fields"},
            set(captured["structure_hashes"]),
        )
        self.assertTrue(
            all(len(value) == 64 for value in captured["structure_hashes"].values())
        )

    def test_application_suffix_identifies_a_scoped_button(self) -> None:
        self.assertEqual(
            ("志愿服务经历", "申请"),
            split_scoped_action_step("志愿服务经历（申请）"),
        )
        self.assertIsNone(split_scoped_action_step("临时借用（仅借1天）"))

    def test_parses_two_step_service_route(self) -> None:
        instruction = parse_instruction("打开 教室借用 > 临时借用（仅借1天）")
        self.assertEqual(("教室借用", "临时借用（仅借1天）"), instruction.route)

    def test_sample_values_only_use_clear_non_sensitive_controls(self) -> None:
        controls = (
            Control(0, 0, "textarea", "借用事由", "reason", "", ""),
            Control(0, 1, "number", "参与人数", "attendees", "", ""),
            Control(0, 2, "tel", "联系电话", "phone", "", ""),
            Control(0, 3, "select", "借用教室", "room", "", ""),
            Control(0, 4, "text", "备注", "note", "", "已填写"),
        )

        values, skipped = sample_values(controls)

        self.assertEqual(
            {"借用事由": "测试填写（未提交）", "参与人数": "10"}, values
        )
        self.assertEqual(("联系电话", "借用教室", "备注"), skipped)

    def test_sample_values_accept_text_inputs_for_obvious_numeric_fields(self) -> None:
        controls = (
            Control(0, 0, "text", "借用用途描述", "purpose", "请输入具体原因", ""),
            Control(0, 1, "text", "总人数", "people", "", ""),
            Control(0, 2, "text", "教室容量", "capacity", "", ""),
            Control(0, 3, "text", "教室借用数量", "rooms", "", ""),
            Control(0, 4, "text", "联系电话", "phone", "", ""),
            Control(0, 5, "text", "开始日期", "date", "", ""),
        )

        values, skipped = sample_values(controls)

        self.assertEqual(
            {
                "借用用途描述": "测试填写（未提交）",
                "总人数": "10",
                "教室容量": "30",
                "教室借用数量": "1",
            },
            values,
        )
        self.assertEqual(("联系电话", "开始日期"), skipped)

    def test_parses_service_fields_and_submit_label(self) -> None:
        instruction = parse_instruction(
            "办理 在读证明申请；填写 申请理由=毕业用途；联系方式=123456；提交按钮=提交"
        )
        self.assertEqual("在读证明申请", instruction.service)
        self.assertEqual({"申请理由": "毕业用途", "联系方式": "123456"}, instruction.fields)
        self.assertEqual("提交", instruction.submit_label)

    def test_matches_visible_form_label(self) -> None:
        controls = (
            Control(0, 0, "text", "申请理由", "reason", "", ""),
            Control(0, 1, "text", "联系电话", "phone", "", ""),
        )
        self.assertEqual(0, match_control("申请理由", controls).index)
        self.assertEqual(1, match_control("电话", controls).index)

    def test_ambiguous_field_is_not_filled(self) -> None:
        controls = (
            Control(0, 0, "text", "联系电话", "phone1", "", ""),
            Control(0, 1, "text", "联系电话", "phone2", "", ""),
        )
        with self.assertRaises(ValueError):
            match_control("联系电话", controls)


class LoginNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_detached_login_frame_is_skipped_while_finding_service(self) -> None:
        from playwright.async_api import Error as PlaywrightError

        class Locator:
            def __init__(self, *, detached: bool = False) -> None:
                self.detached = detached

            async def count(self) -> int:
                if self.detached:
                    raise PlaywrightError("Locator.count: Frame was detached")
                return 1

            def nth(self, _index: int) -> Locator:
                return self

            async def is_visible(self) -> bool:
                return True

            async def click(self) -> None:
                context.pages.append(popup)

        class Frame:
            def __init__(self, locator: Locator) -> None:
                self.locator = locator

            def get_by_text(self, _text: str, *, exact: bool = False) -> Locator:
                return self.locator

        class Page:
            def __init__(self, frames: list[Frame]) -> None:
                self.frames = frames

            def is_closed(self) -> bool:
                return False

            async def wait_for_load_state(self, _state: str, *, timeout: int) -> None:
                pass

        class Context:
            def __init__(self, pages: list[Page]) -> None:
                self.pages = pages

        popup = Page([])
        portal = Page([Frame(Locator(detached=True)), Frame(Locator())])
        context = Context([portal])
        flow = EhallInteractiveFlow(context, portal)

        result = await flow.open_service("成绩查询", timeout_seconds=2)

        self.assertIs(result, popup)


class SampleEntryTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_mode_reads_shape_and_never_fills_or_submits(self) -> None:
        browser = MagicMock()
        browser.start = AsyncMock()
        browser.close = AsyncMock()
        browser.page.goto = AsyncMock()
        browser.snapshot_page = AsyncMock(
            return_value={
                "url": "https://ehallapp.nju.edu.cn/form?gid_=secret-gid",
                "structure": {
                    "controls": [
                        {
                            "locator": "ctl:0:0",
                            "tag": "input",
                            "type": "text",
                            "name": "reason",
                            "value": "private-answer",
                        }
                    ],
                    "headings": [],
                    "links": [],
                    "forms": [],
                    "hidden_fields": [],
                },
                "actions": [],
            }
        )
        flow = MagicMock()
        flow.open_service = AsyncMock()
        flow.wait_for_fields = AsyncMock(
            return_value=(ObservedField(0, "申请理由", "text", True),)
        )
        flow.page.frames = [MagicMock()]
        flow.service = "志愿服务经历"
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "capture.json"
            with (
                patch(
                    "personal_assistant.ehall_interactive_entry.InteractiveHeadedBrowser",
                    return_value=browser,
                ),
                patch(
                    "personal_assistant.ehall_interactive_entry.EhallInteractiveFlow",
                    return_value=flow,
                ),
                patch("builtins.input", return_value=""),
            ):
                result = await run_ehall(
                    "打开 志愿服务经历", capture_out=output_path
                )
            report_text = output_path.read_text("utf-8")

        self.assertEqual(0, result)
        self.assertNotIn("secret-gid", report_text)
        self.assertNotIn("private-answer", report_text)
        self.assertIn("申请理由", report_text)
        flow.fill.assert_not_called()
        flow.confirm_and_submit.assert_not_called()

    async def test_entry_uses_item_scoped_apply_button(self) -> None:
        browser = MagicMock()
        browser.start = AsyncMock()
        browser.close = AsyncMock()
        browser.page.goto = AsyncMock()
        flow = MagicMock()
        flow.open_service = AsyncMock()
        flow.open_scoped_action = AsyncMock()
        flow.wait_for_fields = AsyncMock(return_value=())
        flow.controls = AsyncMock(return_value=())
        with (
            patch(
                "personal_assistant.ehall_interactive_entry.InteractiveHeadedBrowser",
                return_value=browser,
            ),
            patch(
                "personal_assistant.ehall_interactive_entry.EhallInteractiveFlow",
                return_value=flow,
            ),
            patch("builtins.input", side_effect=["", ""]),
        ):
            result = await run_ehall(
                "打开 第二课堂 > 志愿服务经历（申请）"
            )

        self.assertEqual(0, result)
        flow.open_service.assert_awaited_once_with(
            "第二课堂", progress=print, current_page_only=False, catalog=True
        )
        flow.open_scoped_action.assert_awaited_once_with(
            "志愿服务经历", "申请", progress=print
        )
        flow.confirm_and_submit.assert_not_called()

    async def test_rescan_rechecks_combobox_options_after_manual_change(self) -> None:
        browser = MagicMock()
        browser.start = AsyncMock()
        browser.close = AsyncMock()
        browser.page.goto = AsyncMock()
        flow = MagicMock()
        flow.open_service = AsyncMock()
        flow.wait_for_fields = AsyncMock(
            return_value=(ObservedField(0, "开始节次", "combobox", False),)
        )
        flow.controls = AsyncMock(return_value=())
        flow.inspect_options = AsyncMock(side_effect=[(), ("第一节", "第二节")])
        flow.confirm_and_submit = AsyncMock()
        with (
            patch(
                "personal_assistant.ehall_interactive_entry.InteractiveHeadedBrowser",
                return_value=browser,
            ),
            patch(
                "personal_assistant.ehall_interactive_entry.EhallInteractiveFlow",
                return_value=flow,
            ),
            patch("builtins.input", side_effect=["r", ""]),
            patch("builtins.print") as output,
        ):
            result = await run_ehall("打开 教室借用", sample=True)

        self.assertEqual(0, result)
        self.assertEqual(2, flow.inspect_options.await_count)
        self.assertIn(
            "下拉选项“开始节次”：第一节、第二节",
            [call.args[0] for call in output.call_args_list],
        )
        flow.confirm_and_submit.assert_not_awaited()

    async def test_sample_mode_reports_partial_failure_and_keeps_page_open(self) -> None:
        browser = MagicMock()
        browser.start = AsyncMock()
        browser.close = AsyncMock()
        browser.page.goto = AsyncMock()
        flow = MagicMock()
        flow.open_service = AsyncMock()
        flow.wait_for_fields = AsyncMock(return_value=())
        flow.controls = AsyncMock(
            return_value=(
                Control(0, 0, "text", "借用事由", "reason", "", ""),
                Control(0, 1, "text", "总人数", "people", "", ""),
            )
        )
        flow.fill = AsyncMock(side_effect=[ValueError("unsupported"), None])
        flow.confirm_and_submit = AsyncMock()
        with (
            patch(
                "personal_assistant.ehall_interactive_entry.InteractiveHeadedBrowser",
                return_value=browser,
            ),
            patch(
                "personal_assistant.ehall_interactive_entry.EhallInteractiveFlow",
                return_value=flow,
            ),
            patch("builtins.input", return_value="") as prompt,
            patch("builtins.print") as output,
        ):
            result = await run_ehall("打开 教室借用", sample=True)

        self.assertEqual(1, result)
        self.assertEqual(2, flow.fill.await_count)
        prompt.assert_called_once()
        flow.confirm_and_submit.assert_not_awaited()
        self.assertIn(
            "试填失败：借用事由（ValueError）",
            [call.args[0] for call in output.call_args_list],
        )

    async def test_sample_mode_accepts_explicit_custom_option_without_submit(self) -> None:
        browser = MagicMock()
        browser.start = AsyncMock()
        browser.close = AsyncMock()
        browser.page.goto = AsyncMock()
        flow = MagicMock()
        flow.open_service = AsyncMock()
        flow.wait_for_fields = AsyncMock(
            return_value=(ObservedField(0, "校区", "combobox", False),)
        )
        flow.controls = AsyncMock(return_value=())
        flow.inspect_options = AsyncMock(return_value=("鼓楼校区", "仙林校区"))
        flow.fill = AsyncMock()
        flow.confirm_and_submit = AsyncMock()
        with (
            patch(
                "personal_assistant.ehall_interactive_entry.InteractiveHeadedBrowser",
                return_value=browser,
            ),
            patch(
                "personal_assistant.ehall_interactive_entry.EhallInteractiveFlow",
                return_value=flow,
            ),
            patch("builtins.input", return_value=""),
        ):
            result = await run_ehall("打开 教室借用；校区=仙林校区", sample=True)

        self.assertEqual(0, result)
        flow.fill.assert_awaited_once_with({"校区": "仙林校区"})
        flow.confirm_and_submit.assert_not_awaited()

    async def test_sample_mode_fills_without_arming_submit(self) -> None:
        browser = MagicMock()
        browser.start = AsyncMock()
        browser.close = AsyncMock()
        browser.page.goto = AsyncMock()
        flow = MagicMock()
        flow.open_service = AsyncMock()
        flow.wait_for_fields = AsyncMock(
            return_value=(
                ObservedField(0, "借用事由", "text", True),
                ObservedField(0, "借用教室", "combobox", False),
            )
        )
        flow.inspect_options = AsyncMock(return_value=("鼓楼校区", "仙林校区"))
        flow.controls = AsyncMock(
            return_value=(Control(0, 0, "text", "借用事由", "reason", "", ""),)
        )
        flow.fill = AsyncMock()
        flow.confirm_and_submit = AsyncMock()
        with (
            patch(
                "personal_assistant.ehall_interactive_entry.InteractiveHeadedBrowser",
                return_value=browser,
            ),
            patch(
                "personal_assistant.ehall_interactive_entry.EhallInteractiveFlow",
                return_value=flow,
            ),
            patch("builtins.input", return_value=""),
            patch("builtins.print") as output,
        ):
            result = await run_ehall(
                "打开 教室借用 > 临时借用（仅借1天）", sample=True
            )

        self.assertEqual(0, result)
        self.assertEqual(
            ["教室借用", "临时借用（仅借1天）"],
            [call.args[0] for call in flow.open_service.await_args_list],
        )
        self.assertTrue(flow.open_service.await_args_list[1].kwargs["current_page_only"])
        self.assertFalse(flow.open_service.await_args_list[1].kwargs["catalog"])
        flow.fill.assert_awaited_once_with({"借用事由": "测试填写（未提交）"})
        flow.confirm_and_submit.assert_not_awaited()
        self.assertIn(
            "当前可见字段候选：2 个；可直接填写：1 个。",
            [call.args[0] for call in output.call_args_list],
        )
        flow.inspect_options.assert_awaited_once_with("借用教室")
        self.assertIn(
            "下拉选项“借用教室”：鼓楼校区、仙林校区",
            [call.args[0] for call in output.call_args_list],
        )
