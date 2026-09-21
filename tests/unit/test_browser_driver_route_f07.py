"""F07: driver write-allowance routing (main-frame binding, login window)."""

from __future__ import annotations

import unittest

from personal_assistant.infrastructure.browser.driver import PlaywrightHeadedDriver

ORIGIN = "https://ehall.example.test"
SUBMIT_PATH = "/apps/proof/submit"
LOGIN_PATH = "/sso/login"


class _Frame:
    pass


class _Request:
    def __init__(
        self,
        method: str,
        url: str,
        *,
        navigation: bool,
        resource_type: str = "document",
        frame: object | None = None,
    ) -> None:
        self.method = method
        self.url = url
        self.resource_type = resource_type
        self._navigation = navigation
        self.frame = frame

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

    async def test_main_frame_form_post_consumes_the_submit_allowance(self) -> None:
        driver = self.driver()
        driver._submit_allowance = ("POST", ORIGIN, SUBMIT_PATH, 1)
        request = _Request(
            "POST",
            ORIGIN + SUBMIT_PATH,
            navigation=True,
            resource_type="document",
            frame=driver._page.main_frame,
        )
        route = _Route(request)
        await driver._route(route)
        self.assertTrue(route.continued)
        self.assertEqual(1, driver.allowed_write_requests)
        self.assertIsNone(driver._submit_allowance)


class LoginWindowTests(_DriverTestCase):
    async def test_undeclared_password_page_does_not_open_the_write_window(self) -> None:
        driver = self.driver()
        page = _PasswordPage(ORIGIN + "/profile/password")
        detected = await driver._observe_login(page, (LOGIN_PATH,))
        self.assertFalse(detected)
        self.assertFalse(driver._login_window)
        self.assertIsNone(driver._login_allowance)

    async def test_declared_login_path_still_opens_the_window(self) -> None:
        driver = self.driver()
        page = _PasswordPage(ORIGIN + LOGIN_PATH)
        detected = await driver._observe_login(page, (LOGIN_PATH,))
        self.assertTrue(detected)
        self.assertTrue(driver._login_window)


if __name__ == "__main__":
    unittest.main()
