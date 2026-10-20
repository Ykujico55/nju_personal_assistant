"""Interactive-session bridge to the existing authenticated Desktop Companion."""

from __future__ import annotations

import ctypes
import os
from uuid import uuid4

from personal_assistant.core.browser.policy import normalize_origin
from personal_assistant.core.browser.ports import DesktopBrowserPort


def interactive_session() -> bool:
    if os.name != "nt":
        return False
    session = ctypes.c_uint32()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.ProcessIdToSessionId.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
    kernel.ProcessIdToSessionId.restype = ctypes.c_int
    return (
        bool(kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session))) and session.value != 0
    )


class WindowsDesktopInteraction:
    def __init__(
        self,
        companion: DesktopBrowserPort,
        *,
        allowed_origins: tuple[str, ...],
        task_id: str,
        extension_id: str,
    ):
        self.companion, self.allowed_origins = companion, allowed_origins
        self.task_id, self.extension_id = task_id, extension_id
        self.sessions: set[str] = set()

    async def request_visible_browser(self, url: str, reason: str) -> str:
        if not interactive_session():
            raise ValueError("visible browser requires a signed-in interactive Windows session")
        if normalize_origin(url) not in self.allowed_origins or not reason or len(reason) > 256:
            raise ValueError("browser request is outside the host-approved scope")
        session = "desktop_" + uuid4().hex
        await self.companion.create_session(
            session_id=session,
            purpose=reason,
            allowed_origins=self.allowed_origins,
            origin_mode="allowlist",
            task_id=self.task_id,
            extension_id=self.extension_id,
        )
        try:
            await self.companion.navigate(session, url)
        except BaseException:
            await self.companion.close_session(session)
            raise
        self.sessions.add(session)
        return session

    async def close_session(self, session_id: str) -> None:
        if session_id not in self.sessions:
            raise ValueError("session is not owned by this desktop bridge")
        await self.companion.close_session(session_id)
        self.sessions.remove(session_id)
