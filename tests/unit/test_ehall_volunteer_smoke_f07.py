"""One-login, route-and-fill acceptance helper stops before final submit."""

from __future__ import annotations

import unittest

from scripts.ehall_volunteer_smoke import drive_trial, matching_visible_values


class FakeFlow:
    def __init__(
        self, *, already_online: bool = False, second_entry_timeout: bool = False
    ) -> None:
        self.calls: list[tuple[str, object]] = []
        self.already_online = already_online
        self.second_entry_timeout = second_entry_timeout

    async def open_service(self, name: str, **kwargs: object) -> None:
        self.calls.append(("open_service", (name, kwargs)))
        if name == "第二课堂成绩单（在线办理）" and self.second_entry_timeout:
            self.already_online = True
            self.second_entry_timeout = False
            raise TimeoutError("the page refreshed into the online service")

    async def has_visible_text(self, label: str) -> bool:
        return self.already_online and label == "志愿服务经历"

    async def open_scoped_action(
        self, item: str, action: str, **kwargs: object
    ) -> None:
        self.calls.append(("open_scoped_action", (item, action, kwargs)))

    async def fill(self, values: dict[str, str]) -> None:
        self.calls.append(("fill", dict(values)))

    async def inspect_options(self, label: str) -> tuple[str, ...]:
        self.calls.append(("inspect_options", label))
        return ("请选择", "张三", "李四")

    async def confirm_and_submit(self, label: str) -> None:
        raise AssertionError(f"final submit must never be invoked: {label}")


class VolunteerSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_once_then_route_and_fill_in_page_order(self) -> None:
        flow = FakeFlow()
        selected = await drive_trial(flow)
        self.assertEqual("张三", selected["审核人"])
        self.assertEqual(
            [
                "open_service", "open_service", "open_scoped_action",
                "fill", "fill", "fill", "fill", "fill", "fill",
                "inspect_options", "fill",
            ],
            [name for name, _ in flow.calls],
        )
        self.assertEqual(
            ("第二课堂", {}), flow.calls[0][1]
        )
        self.assertEqual(
            (
                "第二课堂成绩单（在线办理）",
                {"current_page_only": True, "catalog": False, "timeout_seconds": 15},
            ),
            flow.calls[1][1],
        )
        self.assertEqual(
            ("志愿服务经历", "申请", {}), flow.calls[2][1]
        )
        self.assertEqual(["审核学院"], list(flow.calls[8][1]))
        self.assertEqual("审核人", flow.calls[9][1])

    async def test_no_reviewer_option_stops_without_submitting(self) -> None:
        flow = FakeFlow()

        async def no_options(_label: str) -> tuple[str, ...]:
            return ("请选择", "请选择...")

        flow.inspect_options = no_options  # type: ignore[method-assign]
        with self.assertRaisesRegex(RuntimeError, "审核人"):
            await drive_trial(flow)
        self.assertEqual("fill", flow.calls[-1][0])
        self.assertEqual(["审核学院"], list(flow.calls[-1][1]))

    async def test_already_on_online_page_skips_reopening_service(self) -> None:
        flow = FakeFlow(already_online=True)
        await drive_trial(flow)
        self.assertEqual(1, sum(name == "open_service" for name, _ in flow.calls))
        self.assertEqual("open_scoped_action", flow.calls[1][0])

    async def test_refresh_into_online_page_during_lookup_continues(self) -> None:
        flow = FakeFlow(second_entry_timeout=True)
        await drive_trial(flow)
        self.assertEqual(2, sum(name == "open_service" for name, _ in flow.calls))
        self.assertEqual("open_scoped_action", flow.calls[2][0])

    def test_visible_value_mismatch_is_not_reported_as_success(self) -> None:
        planned = {"名称": "测试", "审核学院": "文学院"}
        self.assertTrue(matching_visible_values(planned, planned))
        self.assertFalse(matching_visible_values(planned, {"名称": "测试", "审核学院": "请选择"}))
        self.assertFalse(matching_visible_values(planned, {"名称": "测试"}))
