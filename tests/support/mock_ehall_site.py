"""Local HTTPS mock of a supervised ehall-style site (never the real one).

Used only by F07 browser integration tests.  It serves a login challenge, an
application list with permanently prohibited entries, a low-risk form with
optional autosave/unknown-field/version/risk variants, a submission endpoint
that can lose its response, and a read-only flow-tracking page.
"""

from __future__ import annotations

import json
import secrets
import ssl
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .mail_servers import TlsPair, generate_tls_pair

LOGIN_PATH = "/sso/login"
PORTAL_PATH = "/portal"
APP_PATH = "/apps/proof"
AUTOSAVE_PATH = "/apps/proof/autosave"
SUBMIT_PATH = "/apps/proof/submit"
STATUS_PATH = "/apps/proof/status"
TRANSCRIPT_PATH = "/apps/transcript"
TRANSCRIPT_SUBMIT_PATH = "/apps/transcript/submit"
TRANSCRIPT_STATUS_PATH = "/apps/transcript/status"
TEST_STATE_PATH = "/__test__/state"

# Human-verified fingerprint of the plain mock application page, captured once
# with a real Chromium run (``page_structure_document`` + ``compute_page_fingerprint``).
# The env-gated real-browser test asserts the live page still matches it; any
# change to the mock markup or to the driver's structure extraction fails the
# test loudly instead of silently weakening page-version pinning.
APP_PAGE_FINGERPRINT = "6a6217bf80777b30ef61b01ceaf5dfcb9081f0964046f70ddd8fa0198f711eec"

TEST_USERNAME = "test-student"
TEST_PASSWORD = "fixture-password-not-a-real-credential"

APP_LINKS = (
    ("在读证明申请", APP_PATH),
    ("研究生成绩单打印", "/apps/transcript"),
    ("退课申请", "/apps/withdraw"),
    ("在线缴费", "/apps/payment"),
    ("选课变更", "/apps/course-change"),
)


@dataclass
class MockEhallState:
    username: str = TEST_USERNAME
    password: str = TEST_PASSWORD
    sessions: set[str] = field(default_factory=set)
    autosave_attempts: int = 0
    submissions: list[dict[str, str]] = field(default_factory=list)
    logins: int = 0
    lose_response: bool = False
    hide_tracking: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


class MockEhallSite:
    def __init__(self, tls: TlsPair | None = None, *, hostname: str = "localhost") -> None:
        self._tls = tls or generate_tls_pair(hostname)
        self._hostname = hostname
        self.state = MockEhallState()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    @property
    def origin(self) -> str:
        return f"https://{self._hostname}:{self.port}"

    def url(self, path: str) -> str:
        return f"{self.origin}{path}"

    def start(self) -> None:
        state = self.state
        tls = self._tls

        class _Server(ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request: object, client_address: object) -> None:
                # Aborted TLS connections are expected; never print page data.
                return None

        class _Handler(BaseHTTPRequestHandler):
            server_version = "MockEhall/1.0"
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: object) -> None:
                return None

            # -- helpers ---------------------------------------------------

            def _cookie_session(self) -> str:
                header = self.headers.get("Cookie", "")
                for part in header.split(";"):
                    name, _, value = part.strip().partition("=")
                    if name == "PA_SESSION":
                        return value
                return ""

            def _logged_in(self) -> bool:
                with state.lock:
                    return self._cookie_session() in state.sessions

            def _send(self, status: int, body: str, *, content_type: str = "text/html") -> None:
                payload = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", f"{content_type}; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)

            def _redirect(self, location: str, *, cookie: str = "") -> None:
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", location)
                if cookie:
                    self.send_header("Set-Cookie", cookie)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _json(self, payload: dict[str, object]) -> None:
                self._send(200, json.dumps(payload), content_type="application/json")

            # -- routing ---------------------------------------------------

            def do_GET(self) -> None:  # noqa: N802 - stdlib naming
                parts = urlsplit(self.path)
                path = parts.path
                query = parse_qs(parts.query)
                if path == LOGIN_PATH:
                    self._login_page()
                    return
                if path == PORTAL_PATH:
                    if not self._logged_in():
                        self._redirect(LOGIN_PATH)
                        return
                    self._portal_page()
                    return
                if path == APP_PATH:
                    if not self._logged_in():
                        self._redirect(LOGIN_PATH)
                        return
                    self._app_page(query)
                    return
                if path == TRANSCRIPT_PATH:
                    if not self._logged_in():
                        self._redirect(LOGIN_PATH)
                        return
                    self._transcript_page()
                    return
                if path in (STATUS_PATH, TRANSCRIPT_STATUS_PATH):
                    if not self._logged_in():
                        self._redirect(LOGIN_PATH)
                        return
                    self._status_page(query)
                    return
                if path == "/__test__/seed":
                    # Test-only: seed a historical receipt on the tracking page.
                    receipt = (query.get("receipt") or ["NJU-2026-0001"])[0][:32]
                    with state.lock:
                        state.submissions.insert(
                            0,
                            {
                                "receipt": receipt,
                                "reason": "seed",
                                "phone": "",
                                "delivery": "",
                            },
                        )
                    self._json({"seeded": receipt})
                    return
                if path == TEST_STATE_PATH:
                    with state.lock:
                        self._json(
                            {
                                "autosave_attempts": state.autosave_attempts,
                                "submissions": list(state.submissions),
                                "logins": state.logins,
                                "lose_response": state.lose_response,
                            }
                        )
                    return
                self._send(HTTPStatus.NOT_FOUND, "<h1>404</h1>")

            def do_POST(self) -> None:  # noqa: N802 - stdlib naming
                parts = urlsplit(self.path)
                path = parts.path
                if path == LOGIN_PATH:
                    self._login_submit()
                    return
                if path == AUTOSAVE_PATH:
                    with state.lock:
                        state.autosave_attempts += 1
                    self._send(HTTPStatus.NO_CONTENT, "")
                    return
                if path in (SUBMIT_PATH, TRANSCRIPT_SUBMIT_PATH):
                    if not self._logged_in():
                        self._redirect(LOGIN_PATH)
                        return
                    self._submit(parse_qs(parts.query))
                    return
                self._send(HTTPStatus.NOT_FOUND, "<h1>404</h1>")

            # -- pages -----------------------------------------------------

            def _login_page(self) -> None:
                self._send(
                    200,
                    """<!doctype html><html><head><title>统一身份认证</title></head>
<body><h1>统一身份认证</h1>
<form method="post" action="/sso/login">
<label>用户名 <input type="text" name="username" id="username" required></label>
<label>密码 <input type="password" name="password" id="password" required></label>
<button type="submit" id="login-button">登录</button>
</form>
<p>请用户亲自完成扫码、验证码或动态验证。</p></body></html>""",
                )

            def _login_submit(self) -> None:
                length = int(self.headers.get("Content-Length", "0") or 0)
                raw = self.rfile.read(length).decode("utf-8", "replace")
                form = parse_qs(raw)
                username = (form.get("username") or [""])[0]
                password = (form.get("password") or [""])[0]
                if username != state.username or password != state.password:
                    self._send(200, "<h1>用户名或密码错误</h1>")
                    return
                token = secrets.token_urlsafe(24)
                with state.lock:
                    state.sessions.add(token)
                    state.logins += 1
                self._redirect(PORTAL_PATH, cookie=f"PA_SESSION={token}; Path=/; HttpOnly")

            def _portal_page(self) -> None:
                links = "".join(
                    f'<li><a href="{href}">{label}</a></li>' for label, href in APP_LINKS
                )
                self._send(
                    200,
                    f"""<!doctype html><html><head><title>网上办事大厅</title></head>
<body><h1>可用事项</h1><ul>{links}</ul>
<button id="open-proof" onclick="location.href='{APP_PATH}'">在读证明申请</button>
<button id="open-transcript" onclick="location.href='{TRANSCRIPT_PATH}'">研究生成绩单打印</button>
</body></html>""",
                )

            def _transcript_page(self) -> None:
                self._send(
                    200,
                    f"""<!doctype html><html><head><title>研究生成绩单打印</title></head>
<body><h1>在读证明申请</h1>
<form method="post" action="{TRANSCRIPT_SUBMIT_PATH}">
<label>申请理由 <input type="text" name="reason" id="reason" required maxlength="200"></label>
<label>联系电话 <input type="text" name="phone" id="phone" required maxlength="20"></label>
<label>领取方式 <select name="delivery" id="delivery">
<option value="paper">纸质</option><option value="email">电子</option></select></label>
<button type="submit" id="submit-transcript">提交申请</button>
</form></body></html>""",
                )

            def _app_page(self, query: dict[str, list[str]]) -> None:
                variant = set(query)
                note = ""
                if "note" in variant:
                    note = '<label>备注 <input type="text" name="note" id="note"></label>'
                extra = ""
                if "extra" in variant:
                    extra = '<label>新字段 <input type="text" name="mystery" id="mystery"></label>'
                autosave = ""
                if "autosave" in variant:
                    delay = (query.get("delay") or ["0"])[0]
                    autosave = (
                        "<script>document.addEventListener('input', function(){"
                        "setTimeout(function(){fetch('/apps/proof/autosave',"
                        "{method:'POST',body:'x'});}," + delay + ");"
                        "});</script>"
                    )
                warning = ""
                if "risk" in variant:
                    warning = "<p>注意：本事项涉及在线缴费与退费说明。</p>"
                external = ""
                if "external" in variant:
                    external = '<img src="https://127.0.0.1:1/pixel.png" alt="">'
                pre_receipt = ""
                if "preReceipt" in variant:
                    pre_receipt = "<p>上次办理回执号 NJU-2026-0001 状态：已受理</p>"
                double = ""
                if "double" in variant:
                    double = (
                        "<script>document.querySelector('form').addEventListener('submit',"
                        "function(){fetch('/apps/proof/submit',{method:'POST',"
                        "body:new FormData(document.querySelector('form'))});});</script>"
                    )
                swap = ""
                if "swap" in variant:
                    # Rewrite the action at submit time: the page structure
                    # fingerprint cannot see it, so only the host's bound write
                    # allowance can stop the POST.
                    swap = (
                        "<script>document.querySelector('form').addEventListener("
                        "'submit',function(ev){ev.target.action='/apps/proof/other';});"
                        "</script>"
                    )
                if "swapLoad" in variant:
                    swap = (
                        "<script>document.querySelector('form').action="
                        "'/apps/proof/other';</script>"
                    )
                huge = ""
                if "huge" in variant:
                    huge = "<div>" + ("x" * 70000) + "</div>"
                fake_receipt = ""
                if "fakeReceipt" in variant:
                    # DOM-only "success": a receipt appears but no write is sent.
                    fake_receipt = (
                        "<script>document.querySelector('form').addEventListener("
                        "'submit',function(ev){ev.preventDefault();document.body."
                        "insertAdjacentHTML('beforeend',"
                        "'<p>回执号 NJU-2026-9999</p>');});</script>"
                    )
                self._send(
                    200,
                    f"""<!doctype html><html><head><title>在读证明申请</title></head>
<body><h1>在读证明申请</h1>{warning}{external}{pre_receipt}{huge}
<form method="post" action="{SUBMIT_PATH}">
<label>申请理由 <input type="text" name="reason" id="reason" required maxlength="200"></label>
{note}
<label>联系电话 <input type="text" name="phone" id="phone" required maxlength="20"></label>
<label>领取方式 <select name="delivery" id="delivery">
<option value="paper">纸质</option><option value="email">电子</option></select></label>
{extra}
<button type="submit" id="submit-proof">提交申请</button>
</form>{autosave}{double}{swap}{fake_receipt}</body></html>""",
                )

            def _submit(self, query: dict[str, list[str]]) -> None:
                length = int(self.headers.get("Content-Length", "0") or 0)
                raw = self.rfile.read(length).decode("utf-8", "replace")
                form = parse_qs(raw)
                reason = (form.get("reason") or [""])[0]
                phone = (form.get("phone") or [""])[0]
                delivery = (form.get("delivery") or [""])[0]
                with state.lock:
                    receipt = f"NJU-2026-{len(state.submissions) + 1:04d}"
                    state.submissions.append(
                        {
                            "receipt": receipt,
                            "reason": reason,
                            "phone": phone,
                            "delivery": delivery,
                            "hidden": state.hide_tracking or "hideTracking" in query,
                        }
                    )
                if "lost" in query or state.lose_response:
                    # The server recorded the submission but the client never
                    # learns the outcome (gateway timeout): the action is
                    # UNKNOWN and may only be resolved by read-only tracking.
                    self.send_response(HTTPStatus.GATEWAY_TIMEOUT)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self._send(
                    200,
                    f"""<!doctype html><html><head><title>受理成功</title></head>
<body><h1>受理成功</h1><p>回执号 {receipt}</p>
<p><a href="{STATUS_PATH}?receipt={receipt}">流程跟踪</a></p></body></html>""",
                )

            def _status_page(self, query: dict[str, list[str]]) -> None:
                receipt = (query.get("receipt") or [""])[0]
                with state.lock:
                    visible = [
                        item for item in state.submissions if not item.get("hidden")
                    ]
                    records = (
                        [item for item in visible if item["receipt"] == receipt]
                        if receipt
                        else visible
                    )
                if records:
                    items = "".join(
                        f"<li>回执号 {item['receipt']} 状态：已受理</li>" for item in records
                    )
                    self._send(
                        200,
                        f"<html><body><h1>流程跟踪</h1><ul>{items}</ul></body></html>",
                    )
                    return
                self._send(200, "<html><body><h1>流程跟踪</h1><p>暂无记录</p></body></html>")

        self._server = _Server(("127.0.0.1", 0), _Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(
            certfile=str(tls.directory / "server.pem"),
            keyfile=str(tls.directory / "server.key"),
        )
        self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def submission_count(self) -> int:
        with self.state.lock:
            return len(self.state.submissions)

    def autosave_attempts(self) -> int:
        with self.state.lock:
            return self.state.autosave_attempts

    def seed_receipt(self, receipt: str = "NJU-2026-0001") -> None:
        """Test-only: put a historical receipt on the tracking page."""

        with self.state.lock:
            self.state.submissions.insert(
                0,
                {"receipt": receipt, "reason": "seed", "phone": "", "delivery": ""},
            )

    def set_hide_tracking(self, value: bool = True) -> None:
        """Test-only: keep recorded submissions off the tracking page."""

        with self.state.lock:
            self.state.hide_tracking = value

    def set_lose_response(self, value: bool = True) -> None:
        """The next submissions lose their HTTP response (gateway timeout)."""

        with self.state.lock:
            self.state.lose_response = value


__all__ = [
    "APP_LINKS",
    "APP_PAGE_FINGERPRINT",
    "APP_PATH",
    "TRANSCRIPT_PATH",
    "TRANSCRIPT_STATUS_PATH",
    "TRANSCRIPT_SUBMIT_PATH",
    "AUTOSAVE_PATH",
    "LOGIN_PATH",
    "MockEhallSite",
    "MockEhallState",
    "PORTAL_PATH",
    "STATUS_PATH",
    "SUBMIT_PATH",
    "TEST_PASSWORD",
    "TEST_STATE_PATH",
    "TEST_USERNAME",
]
