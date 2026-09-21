"""Supervised browser infrastructure: headed Playwright driver and Desktop Companion.

Only :mod:`personal_assistant.infrastructure.browser.driver` may import
Playwright.  The companion binds loopback only and hands out in-memory,
session-scoped capabilities; the host never persists cookies or storage state.
"""
