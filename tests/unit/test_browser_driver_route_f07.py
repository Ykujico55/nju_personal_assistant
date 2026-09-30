"""F07: driver write-allowance routing (main-frame binding, login window)."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from personal_assistant.core.browser import form_payload_sha256
from personal_assistant.infrastructure.browser.driver import (
    BrowserDriverError,
    ControlMeta,
    PlaywrightHeadedDriver,
)

ORIGIN = "https://ehall.example.test"
SUBMIT_PATH = "/apps/proof/submit"
LOGIN_PATH = "/sso/login"


class _Frame:
    pass


class _Control:
    def __init__(self, *, name: str) -> None:
        self.name = name


class _Request:
    def __init__(
        self,
        method: str,
        url: str,
        *,
        navigation: bool,
        resource_type: str = "document",
        frame: object | None = None,
        post_data: str = "",
        content_type: str = "application/x-www-form-urlencoded",
    ) -> None:
        self.method = method
        self.url = url
        self.resource_type = resource_type
        self._navigation = navigation
        self.frame = frame
        self.post_data = post_data
        self.headers = {"content-type": content_type}

    def is_navigation_request(self) -> bool:
        return self._navigation


class _Route:
    def __init__(self, request: _Request) -> None:
        self.request = request
        self.aborted = False
        self.continued = False

    async def abort(self) -> None:
        self.aborted = True

    async def continue_(self) -> None:
        self.continued = True


class _Page:
    def __init__(self) -> None:
        self.main_frame = _Frame()
        self.url = ORIGIN + "/portal"

    def locator(self, selector: str) -> _Locator:
        return _Locator(0)


class _Locator:
    def __init__(self, count: int) -> None:
        self._count = count

    async def count(self) -> int:
        return self._count


class _PasswordPage:
    """A page outside the declared login paths that still shows a password box."""

    def __init__(self, url: str) -> None:
        self.url = url

    def locator(self, selector: str) -> _Locator:
        return _Locator(1 if selector == "input[type=password]" else 0)


class _DriverTestCase(unittest.IsolatedAsyncioTestCase):
    def driver(self) -> PlaywrightHeadedDriver:
        driver = PlaywrightHeadedDriver(allowed_origins=[ORIGIN])
        driver._page = _Page()
        return driver


class SubmitAllowanceRoutingTests(_DriverTestCase):
    async def test_fill_has_one_deadline_and_reports_the_stalled_locator(self) -> None:
        driver = self.driver()
        driver._page = object()
        driver._deadline = 0.6
        driver._control_meta = {
            f"ctl:0:{index}": ControlMeta(
                locator=f"ctl:0:{index}", index=index, tag="input", type="text",
                name=f"field{index}", element_id="", required=False, readonly=False,
                disabled=False, options=(), max_length=0,
            )
            for index in range(2)
        }

        async def slow_apply(*_args: object) -> None:
            await asyncio.sleep(0.35)

        with (
            patch.object(driver, "_control_element", return_value=object()),
            patch.object(driver, "_apply_value", side_effect=slow_apply),
            self.assertRaises(BrowserDriverError) as caught,
        ):
            await driver.fill((("ctl:0:0", "one"), ("ctl:0:1", "two")))
        self.assertEqual("BROWSER_TIMEOUT", caught.exception.code)
        self.assertEqual("ctl:0:1", driver.last_fill_locator)

    async def test_explicit_navigation_mode_allows_page_posts_only_before_fill(self) -> None:
        driver = PlaywrightHeadedDriver(
            allowed_origins=[ORIGIN], allow_navigation_posts=True
        )
        driver._page = _Page()
        before = _Route(
            _Request("POST", ORIGIN + "/app/load", navigation=False,
                     resource_type="xhr")
        )
        await driver._route(before)
        self.assertTrue(before.continued)
        driver._mutations_blocked = True
        after = _Route(
            _Request("POST", ORIGIN + "/app/save", navigation=False,
                     resource_type="xhr")
        )
        await driver._route(after)
        self.assertTrue(after.aborted)

    async def test_background_fetch_cannot_consume_the_submit_allowance(self) -> None:
        driver = self.driver()
        driver._submit_allowance = ("POST", ORIGIN, SUBMIT_PATH, 1)
        request = _Request(
            "POST",
            ORIGIN + SUBMIT_PATH,
            navigation=False,
            resource_type="fetch",
            frame=driver._page.main_frame,
        )
        route = _Route(request)
        await driver._route(route)
        self.assertTrue(route.aborted)
        self.assertEqual(0, driver.allowed_write_requests)
        self.assertIsNotNone(driver._submit_allowance)

    def _bind_payload(
        self,
        driver: PlaywrightHeadedDriver,
        body: str,
        *,
        locators: tuple[str, ...] = (),
    ) -> None:
        pairs = tuple(
            item.split("=", 1) for item in body.split("&") if "=" in item
        )
        driver._submit_payload_locators = frozenset(locators)
        driver._submit_payload_sha256 = form_payload_sha256(())
        driver._submit_body_sha256 = form_payload_sha256(pairs)

    async def test_main_frame_form_post_consumes_the_submit_allowance(self) -> None:
        driver = self.driver()
        driver._submit_allowance = ("POST", ORIGIN, SUBMIT_PATH, 1)
        self._bind_payload(driver, "reason=need-proof")
        request = _Request(
            "POST",
            ORIGIN + SUBMIT_PATH,
            navigation=True,
            resource_type="document",
            frame=driver._page.main_frame,
            post_data="reason=need-proof",
        )
        route = _Route(request)
        await driver._route(route)
        self.assertTrue(route.continued)
        self.assertEqual(1, driver.allowed_write_requests)
        self.assertIsNone(driver._submit_allowance)

    async def test_an_unapproved_extra_hidden_field_aborts_the_write(self) -> None:
        driver = self.driver()
        driver._submit_allowance = ("POST", ORIGIN, SUBMIT_PATH, 1)
        self._bind_payload(driver, "reason=need-proof")
        request = _Request(
            "POST",
            ORIGIN + SUBMIT_PATH,
            navigation=True,
            resource_type="document",
            frame=driver._page.main_frame,
            post_data="reason=need-proof&withdraw=1",
        )
        route = _Route(request)
        await driver._route(route)
        self.assertTrue(route.aborted)
        self.assertEqual(0, driver.allowed_write_requests)
        self.assertEqual(1, driver.payload_mismatches)
        # The allowance is not consumed by a rejected payload.
        self.assertIsNotNone(driver._submit_allowance)

    async def test_a_rewritten_approved_field_aborts_the_write(self) -> None:
        driver = self.driver()
        driver._submit_allowance = ("POST", ORIGIN, SUBMIT_PATH, 1)
        self._bind_payload(driver, "reason=need-proof")
        request = _Request(
            "POST",
            ORIGIN + SUBMIT_PATH,
            navigation=True,
            resource_type="document",
            frame=driver._page.main_frame,
            post_data="reason=other",
        )
        route = _Route(request)
        await driver._route(route)
        self.assertTrue(route.aborted)
        self.assertEqual(1, driver.payload_mismatches)

    async def test_a_static_hidden_field_outside_the_approved_set_is_allowed(self) -> None:
        # A CSRF-style static hidden field is not part of the approved fields
        # (it never had an approval) but must be unchanged since the click.
        driver = self.driver()
        driver._control_meta = {
            "ctl:0:0": _Control(name="reason"),
            "ctl:0:1": _Control(name="csrf"),
        }
        driver._control_values = {"ctl:0:0": "need-proof", "ctl:0:1": "token-1"}
        driver._submit_allowance = ("POST", ORIGIN, SUBMIT_PATH, 1)
        driver._submit_payload_locators = frozenset({"ctl:0:0"})
        driver._submit_payload_sha256 = form_payload_sha256(
            (("ctl:0:0", "need-proof"),)
        )
        driver._submit_body_sha256 = form_payload_sha256(
            (("reason", "need-proof"), ("csrf", "token-1"))
        )
        request = _Request(
            "POST",
            ORIGIN + SUBMIT_PATH,
            navigation=True,
            resource_type="document",
            frame=driver._page.main_frame,
            post_data="reason=need-proof&csrf=token-1",
        )
        route = _Route(request)
        await driver._route(route)
        self.assertTrue(route.continued)
        self.assertEqual(1, driver.allowed_write_requests)

    async def test_a_changed_static_hidden_field_after_the_click_aborts(self) -> None:
        driver = self.driver()
        driver._control_meta = {
            "ctl:0:0": _Control(name="reason"),
            "ctl:0:1": _Control(name="csrf"),
        }
        driver._control_values = {"ctl:0:0": "need-proof", "ctl:0:1": "token-1"}
        driver._submit_allowance = ("POST", ORIGIN, SUBMIT_PATH, 1)
        driver._submit_payload_locators = frozenset({"ctl:0:0"})
        driver._submit_payload_sha256 = form_payload_sha256(
            (("ctl:0:0", "need-proof"),)
        )
        driver._submit_body_sha256 = form_payload_sha256(
            (("reason", "need-proof"), ("csrf", "token-1"))
        )
        request = _Request(
            "POST",
            ORIGIN + SUBMIT_PATH,
            navigation=True,
            resource_type="document",
            frame=driver._page.main_frame,
            post_data="reason=need-proof&csrf=token-2",
        )
        route = _Route(request)
        await driver._route(route)
        self.assertTrue(route.aborted)
        self.assertEqual(1, driver.payload_mismatches)

    async def test_a_non_urlencoded_body_fails_closed(self) -> None:
        driver = self.driver()
        driver._submit_allowance = ("POST", ORIGIN, SUBMIT_PATH, 1)
        self._bind_payload(driver, "reason=need-proof")
        request = _Request(
            "POST",
            ORIGIN + SUBMIT_PATH,
            navigation=True,
            resource_type="document",
            frame=driver._page.main_frame,
            post_data="--boundary--",
            content_type="multipart/form-data; boundary=boundary",
        )
        route = _Route(request)
        await driver._route(route)
        self.assertTrue(route.aborted)
        self.assertEqual(1, driver.payload_mismatches)


class SuccessfulControlTemplateTests(unittest.TestCase):
    def test_checked_checkbox_uses_its_html_value(self) -> None:
        pairs = PlaywrightHeadedDriver._successful_control_pairs(
            name="consent",
            type_="checkbox",
            value="true",
            value_attribute="accepted",
            disabled=False,
        )
        self.assertEqual((("consent", "accepted"),), pairs)

    def test_checked_checkbox_without_value_uses_html_default(self) -> None:
        pairs = PlaywrightHeadedDriver._successful_control_pairs(
            name="consent",
            type_="checkbox",
            value="true",
            value_attribute=None,
            disabled=False,
        )
        self.assertEqual((("consent", "on"),), pairs)

    def test_unchecked_checkbox_and_radio_are_omitted(self) -> None:
        for type_ in ("checkbox", "radio"):
            with self.subTest(type_=type_):
                pairs = PlaywrightHeadedDriver._successful_control_pairs(
                    name="choice",
                    type_=type_,
                    value="false",
                    value_attribute="paper",
                    disabled=False,
                )
                self.assertEqual((), pairs)

    def test_disabled_control_is_omitted(self) -> None:
        pairs = PlaywrightHeadedDriver._successful_control_pairs(
            name="disabled_note",
            type_="text",
            value="not-submitted",
            value_attribute="not-submitted",
            disabled=True,
        )
        self.assertEqual((), pairs)


class LoginWindowTests(_DriverTestCase):
    async def test_undeclared_password_page_does_not_open_the_write_window(self) -> None:
        driver = self.driver()
        page = _PasswordPage(ORIGIN + "/profile/password")
        detected = await driver._observe_login(page, (LOGIN_PATH,))
        self.assertFalse(detected)
        self.assertFalse(driver._login_window)
        self.assertIsNone(driver._login_allowance)

    async def test_login_path_prefix_evasion_does_not_open_the_window(self) -> None:
        driver = self.driver()
        page = _PasswordPage(ORIGIN + "/sso/login-evil")
        detected = await driver._observe_login(page, (LOGIN_PATH,))
        self.assertFalse(detected)
        self.assertIsNone(driver._login_allowance)
        self.assertFalse(driver._login_window)

    async def test_declared_login_glob_still_matches_explicitly(self) -> None:
        driver = self.driver()
        page = _PasswordPage(ORIGIN + "/sso/login")
        detected = await driver._observe_login(page, ("/sso/*",))
        self.assertTrue(detected)
        self.assertTrue(driver._login_window)

    async def test_declared_login_path_still_opens_the_window(self) -> None:
        driver = self.driver()
        page = _PasswordPage(ORIGIN + LOGIN_PATH)
        detected = await driver._observe_login(page, (LOGIN_PATH,))
        self.assertTrue(detected)
        self.assertTrue(driver._login_window)


class BlockedRequestDiagnosticsTests(_DriverTestCase):
    async def test_blocked_post_reports_only_static_path_metadata(self) -> None:
        driver = self.driver()
        request = _Request(
            "POST",
            ORIGIN + "/api/student-1234/queryTableConfig.do?gid_=session-secret",
            navigation=False,
            resource_type="xhr",
            post_data="password=body-secret",
        )
        route = _Route(request)

        await driver._route(route)

        self.assertTrue(route.aborted)
        samples = (await driver.diagnostics())["blocked_request_samples"]
        self.assertEqual(1, len(samples))
        self.assertEqual("POST", samples[0]["method"])
        self.assertEqual("xhr", samples[0]["resource_type"])
        self.assertEqual("queryTableConfig.do", samples[0]["endpoint"])
        self.assertRegex(samples[0]["path_id"], r"^[0-9a-f]{16}$")
        self.assertEqual(1, samples[0]["count"])
        self.assertNotIn("session-secret", repr(samples))
        self.assertNotIn("body-secret", repr(samples))
        self.assertNotIn("student-1234", repr(samples))

    async def test_blocked_request_samples_are_bounded(self) -> None:
        driver = self.driver()
        for index in range(40):
            suffix = f"{chr(97 + index // 26)}{chr(97 + index % 26)}"
            await driver._route(
                _Route(
                    _Request(
                        "POST",
                        ORIGIN + f"/api/{suffix}.do",
                        navigation=False,
                        resource_type="fetch",
                    )
                )
            )
        diagnostics = await driver.diagnostics()
        self.assertEqual(40, diagnostics["blocked_mutating_requests"])
        self.assertLessEqual(len(diagnostics["blocked_request_samples"]), 32)
        self.assertTrue(diagnostics["blocked_request_samples_truncated"])


if __name__ == "__main__":
    unittest.main()
