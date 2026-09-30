# ruff: noqa: E501
"""Controlled real-ehall acceptance harness (read-only capture, then preview).

This is NOT production code and NOT a test double: it talks to the real
``https://ehall.nju.edu.cn`` through the real headed Desktop Companion.

Two non-interactive phases; the human only interacts with the visible Chromium
window (SSO / captcha / QR).  The script never types a password, never bypasses
a challenge and never submits:

* ``capture`` — opens the entry page, waits until the user has finished SSO,
  reads the portal links, navigates one chosen transaction page and writes its
  canonical structure + fingerprint to a JSON report.  Read-only: no fill, no
  click, no network write.  If ``--app-path`` is omitted the script waits for a
  choice file containing the path (so the operator can pick a link after seeing
  the portal list).
* ``preview`` — re-uses a capture report, pins the human-verified fingerprint in
  a host adapter, drives the real ``BrowserSessionBroker`` through login to the
  transaction page and stops at the authoritative ``PREVIEW_READY`` preview.
  ``submit_enabled`` is hard-coded ``False``: there is no submit path here.

No cookie, password, verification code, storage state or raw HTML is ever
printed, logged or written; the companion only returns bounded structures.

Run from the repository root, e.g.::

    .venv-win\\Scripts\\python.exe scripts\\ehall_real_acceptance.py capture --origins https://ehall.nju.edu.cn --out %TEMP%\\ehall_capture.json
    .venv-win\\Scripts\\python.exe scripts\\ehall_real_acceptance.py preview --report %TEMP%\\ehall_capture.json --values %TEMP%\\ehall_values.json
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import secrets
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

DEFAULT_ORIGIN = "https://ehall.nju.edu.cn"
HANDSHAKE_TIMEOUT_SECONDS = 30.0
POLL_SECONDS = 2.0
DEFAULT_LOGIN_TIMEOUT = 900.0
DEFAULT_CHOICE_TIMEOUT = 900.0
MAX_BLOCKED_REQUEST_SAMPLES = 32
_DIAGNOSTIC_ENDPOINT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}\.(?:do|json)$")
_DIAGNOSTIC_PATH_ID = re.compile(r"^[0-9a-f]{16}$")
EXTENSION_ID = "nju.ehall"
EXTENSION_VERSION = "0.1.0"
ADAPTER_ID = "nju.ehall.proof"
ADAPTER_VERSION = "1.0.0"
TRANSACTION_ID = "proof.apply"
TASK_ID = "ehall-real-acceptance"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _log(log_path: Path, event: dict[str, object]) -> None:
    payload = {"at": _now(), **event}
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _normalize_origins(raw: str) -> tuple[str, ...]:
    origins: list[str] = []
    for item in raw.split(","):
        candidate = item.strip().rstrip("/")
        if not candidate:
            continue
        parts = urlsplit(candidate)
        if parts.scheme != "https" or not parts.hostname:
            raise SystemExit(f"origin must be an https origin: {candidate!r}")
        if parts.path not in ("", "/") or parts.query or parts.fragment:
            raise SystemExit(f"origin must not contain a path/query: {candidate!r}")
        normalized = f"https://{parts.hostname}" + (
            f":{parts.port}" if parts.port not in (None, 443) else ""
        )
        if normalized not in origins:
            origins.append(normalized)
    if not origins:
        raise SystemExit("at least one --origins value is required")
    return tuple(origins)


def _safe_url(url: str) -> str:
    """Keep the route while omitting dynamic query/session parameters."""

    parts = urlsplit(url)
    fragment = parts.fragment.split("?", 1)[0]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", fragment))


def _capture_path(url: str) -> str:
    parts = urlsplit(_safe_url(url))
    path = parts.path or "/"
    return f"{path}#{parts.fragment}" if parts.fragment else path


def _bounded_snapshot(raw: dict[str, object]) -> dict[str, object]:
    structure = raw.get("structure") if isinstance(raw.get("structure"), dict) else {}
    return {
        "url": _safe_url(str(raw.get("url", ""))),
        "title": str(raw.get("title", "")),
        "login_page": bool(raw.get("login_page", False)),
        "scan_incomplete": bool(raw.get("scan_incomplete", False)),
        "actions": list(raw.get("actions", [])),
        "links": [dict(item) for item in raw.get("links", []) if isinstance(item, dict)],
        "structure": {
            "controls": [
                {**item, "value": ""}
                for item in structure.get("controls", [])
                if isinstance(item, dict)
            ],
            "headings": list(structure.get("headings", [])),
            "links": list(structure.get("links", [])),
            "forms": [
                {**item, "action": _safe_url(str(item.get("action", "")))}
                for item in structure.get("forms", [])
                if isinstance(item, dict)
            ],
            "hidden_fields": list(structure.get("hidden_fields", [])),
        },
    }


def _fingerprint(snapshot: dict[str, object]) -> str:
    from personal_assistant.core.browser import (
        compute_page_fingerprint,
        page_structure_document,
    )

    structure = snapshot["structure"]
    assert isinstance(structure, dict)
    document = page_structure_document(
        controls=structure.get("controls", ()),
        headings=structure.get("headings", ()),
        links=structure.get("links", ()),
        forms=structure.get("forms", ()),
        hidden_fields=structure.get("hidden_fields", ()),
    )
    return compute_page_fingerprint(document)


def _write_report(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _diagnostic_summary(raw: dict[str, object]) -> dict[str, object]:
    """Copy only bounded, token-free browser diagnostics into a capture report."""

    sessions = raw.get("sessions")
    if not isinstance(sessions, list):
        sessions = []
    origin_blocks = 0
    mutation_blocks = 0
    samples: list[dict[str, object]] = []
    truncated = False
    for item in sessions:
        if not isinstance(item, dict):
            continue
        driver = item.get("driver")
        if not isinstance(driver, dict):
            continue
        origin_count = driver.get("blocked_origin_requests")
        mutation_count = driver.get("blocked_mutating_requests")
        if isinstance(origin_count, int) and not isinstance(origin_count, bool):
            origin_blocks += max(0, origin_count)
        if isinstance(mutation_count, int) and not isinstance(mutation_count, bool):
            mutation_blocks += max(0, mutation_count)
        truncated = truncated or driver.get("blocked_request_samples_truncated") is True
        entries = driver.get("blocked_request_samples")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            method = entry.get("method")
            resource_type = entry.get("resource_type")
            endpoint = entry.get("endpoint")
            path_id = entry.get("path_id")
            count = entry.get("count")
            if (
                method not in {"POST", "PUT", "PATCH", "DELETE", "OTHER"}
                or resource_type not in {"xhr", "fetch", "document", "other"}
                or not isinstance(endpoint, str)
                or not (
                    endpoint == "{redacted}"
                    or _DIAGNOSTIC_ENDPOINT.fullmatch(endpoint)
                )
                or not isinstance(path_id, str)
                or not _DIAGNOSTIC_PATH_ID.fullmatch(path_id)
                or not isinstance(count, int)
                or isinstance(count, bool)
                or count < 1
            ):
                continue
            if len(samples) >= MAX_BLOCKED_REQUEST_SAMPLES:
                truncated = True
                break
            samples.append(
                {
                    "method": method,
                    "resource_type": resource_type,
                    "endpoint": endpoint,
                    "path_id": path_id,
                    "count": count,
                }
            )
    return {
        "session_count": len(sessions),
        "blocked_origin_requests": origin_blocks,
        "blocked_mutating_requests": mutation_blocks,
        "blocked_request_samples": samples,
        "blocked_request_samples_truncated": truncated,
    }


async def _start_companion(
    *, allow_navigation_posts: bool = False
) -> tuple[asyncio.subprocess.Process, dict[str, object]]:
    env = dict(os.environ)
    env["PA_COMPANION_PORT"] = "0"
    env["PA_COMPANION_ALLOW_NAVIGATION_POSTS"] = "1" if allow_navigation_posts else "0"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "personal_assistant.infrastructure.browser.companion_entry",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=str(ROOT),
        env=env,
    )
    assert process.stdout is not None
    try:
        line = await asyncio.wait_for(
            process.stdout.readline(), timeout=HANDSHAKE_TIMEOUT_SECONDS
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        raise SystemExit("the companion did not print a handshake") from None
    if not line:
        code = await process.wait()
        raise SystemExit(f"the companion exited with code {code} before its handshake")
    handshake = json.loads(line.decode("utf-8").strip())
    if handshake.get("type") != "pa.desktop_companion.handshake":
        process.kill()
        await process.wait()
        raise SystemExit("unexpected companion handshake")
    return process, handshake


async def _stop_companion(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=8)
    except TimeoutError:
        process.kill()
        await process.wait()


async def _wait_for_login(
    client: object,
    session_id: str,
    *,
    timeout: float,
    log_path: Path,
    phase: str,
    sso_hosts: frozenset[str] = frozenset(),
    require_challenge: bool = False,
) -> dict[str, object]:
    """Wait for the human to finish SSO.

    ``require_challenge`` first waits until the challenge page (password input
    or SSO host) is visible, then until it is gone again.  Without it, the wait
    ends as soon as the current page is not a login page -- which is also true
    for an SPA shell that has not redirected yet.
    """

    deadline = time.monotonic() + timeout
    seen_challenge = not require_challenge
    last: dict[str, object] = {}
    while time.monotonic() < deadline:
        snapshot = dict(await client.snapshot(session_id, scan_text=False))  # type: ignore[attr-defined]
        current_url = str(snapshot.get("url", ""))
        host = urlsplit(current_url).hostname or ""
        on_sso_host = bool(host) and host in sso_hosts
        challenge = bool(snapshot.get("login_page")) or on_sso_host
        state = {
            "url": _safe_url(current_url),
            "login_page": challenge,
            "blocked_origin_requests": snapshot.get("blocked_origin_requests", 0),
        }
        if state != last:
            _log(
                log_path,
                {"phase": phase, "event": "waiting_for_user_login", **state},
            )
            last = state
        if not seen_challenge and challenge:
            seen_challenge = True
        if seen_challenge and not challenge and current_url:
            return snapshot
        await asyncio.sleep(POLL_SECONDS)
    raise SystemExit(
        "timed out waiting for the user to finish SSO; if the login page never "
        "opened, add the SSO origin (e.g. https://authserver.nju.edu.cn) to --origins"
    )


async def _await_choice(
    choice_file: Path, links: list[dict[str, object]], *, timeout: float, log_path: Path
) -> str:
    deadline = time.monotonic() + timeout
    _log(
        log_path,
        {
            "phase": "capture",
            "event": "awaiting_app_choice",
            "choice_file": str(choice_file),
            "links": links,
        },
    )
    while time.monotonic() < deadline:
        if choice_file.is_file():
            chosen = choice_file.read_text(encoding="utf-8").strip()
            if chosen:
                return chosen
        await asyncio.sleep(POLL_SECONDS)
    raise SystemExit(
        f"no --app-path given and no choice written to {choice_file}; portal links were logged"
    )


async def capture(args: argparse.Namespace) -> int:
    from personal_assistant.infrastructure.browser.companion_client import (
        LoopbackCompanionClient,
    )

    origins = _normalize_origins(args.origins)
    entry = args.entry or origins[0]
    entry_host = urlsplit(entry).hostname or ""
    sso_hosts = frozenset(
        host
        for host in (urlsplit(origin).hostname or "" for origin in origins)
        if host and host != entry_host
    )
    log_path = args.out.with_suffix(".log")
    process, handshake = await _start_companion()
    client = LoopbackCompanionClient(
        base_url=f"http://127.0.0.1:{handshake['port']}",
        root_capability=str(handshake["capability"]),
    )
    session_id = f"ehall-real-{secrets.token_hex(4)}"
    report: dict[str, object] = {
        "mode": "capture",
        "started_at": _now(),
        "origins": list(origins),
        "entry": _safe_url(entry),
        "origin_mode": args.origin_mode,
        "session_id": session_id,
        "read_only": True,
    }
    try:
        await client.create_session(
            session_id=session_id,
            purpose="真实 ehall 只读验收（不填写、不点击、不提交）",
            allowed_origins=origins,
            origin_mode=args.origin_mode,
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
        )
        first = dict(await client.navigate(session_id, entry))
        report["entry_snapshot"] = _bounded_snapshot(first)
        _log(
            log_path,
            {
                "phase": "capture",
                "event": "entry_opened",
                "url": _safe_url(str(first.get("url", ""))),
                "login_page": first.get("login_page"),
                "hint": "请在 Chromium 窗口内亲自完成 SSO（扫码/验证码）；脚本不会输入任何凭据",
            },
        )
        await _wait_for_login(
            client,
            session_id,
            timeout=args.login_timeout,
            log_path=log_path,
            phase="capture",
            sso_hosts=sso_hosts,
            require_challenge=True,
        )
        portal = dict(await client.navigate(session_id, entry))
        links = [dict(item) for item in portal.get("links", []) if isinstance(item, dict)]
        # SPA portals render their service cards asynchronously; poll the live
        # page (read-only snapshot, no navigation) until the list settles.
        portal_deadline = time.monotonic() + args.portal_wait
        while not links and time.monotonic() < portal_deadline:
            await asyncio.sleep(POLL_SECONDS)
            portal = dict(await client.snapshot(session_id, scan_text=False))
            links = [dict(item) for item in portal.get("links", []) if isinstance(item, dict)]
        portal_snapshot = _bounded_snapshot(portal)
        report["portal"] = {
            "url": portal_snapshot["url"],
            "title": portal_snapshot["title"],
            "links": links,
            "structure": portal_snapshot["structure"],
        }
        _log(
            log_path,
            {
                "phase": "capture",
                "event": "portal_read",
                "url": portal_snapshot["url"],
                "link_count": len(links),
                "headings": portal_snapshot["structure"]["headings"],
                "actions": [
                    item.get("label")
                    for item in portal_snapshot.get("actions", [])
                    if isinstance(item, dict)
                ][:60],
                "control_names": [
                    item.get("name")
                    for item in portal_snapshot["structure"]["controls"]
                    if isinstance(item, dict)
                ][:40],
            },
        )
        captures: list[dict[str, object]] = []
        while True:
            chosen = args.app_path or await _await_choice(
                args.choice_file, links, timeout=args.choice_timeout, log_path=log_path
            )
            args.choice_file.unlink(missing_ok=True)
            if chosen.upper() == "DONE":
                break
            if chosen.upper() == "CURRENT":
                # The human opened the transaction page themselves; only read
                # the current page.  No navigation, no reload, no click, no fill.
                await client.adopt_opened_page(session_id)
                app = dict(await client.snapshot(session_id, scan_text=False))
                if _safe_url(str(app.get("url", ""))) == portal_snapshot["url"]:
                    raise SystemExit(
                        "the browser is still on the portal; open a transaction first"
                    )
            else:
                if not chosen.startswith("/") and not chosen.startswith("http"):
                    chosen = "/" + chosen
                app = dict(await client.navigate(session_id, urljoin(entry, chosen)))
            snapshot = _bounded_snapshot(app)
            fingerprint = _fingerprint(app)
            captured = {
                "path": _capture_path(str(app.get("url", ""))),
                "url": snapshot["url"],
                "title": snapshot["title"],
                "fingerprint": fingerprint,
                "scan_incomplete": snapshot["scan_incomplete"],
                "actions": snapshot["actions"],
                "structure": snapshot["structure"],
                "requires_adapter_navigation": bool(
                    urlsplit(str(app.get("url", ""))).query
                ),
            }
            captures.append(captured)
            report["captures"] = captures
            report["app"] = captured
            report["finished_at"] = _now()
            diagnostics = dict(await client.diagnostics())
            report["diagnostics"] = _diagnostic_summary(diagnostics)
            _write_report(args.out, report)
            _log(
                log_path,
                {
                    "phase": "capture",
                    "event": "captured",
                    "capture_index": len(captures) - 1,
                    "app_url": captured["url"],
                    "fingerprint": fingerprint,
                    "controls": [
                        item.get("name")
                        for item in captured["structure"]["controls"]
                        if isinstance(item, dict)
                    ][:30],
                    "actions": [
                        item.get("label")
                        for item in captured["actions"]
                        if isinstance(item, dict)
                    ][:30],
                    "report": str(args.out),
                },
            )
            print(json.dumps(captured, ensure_ascii=False, indent=2)[:4000])
            print(f"capture {len(captures)} written to {args.out}")
            print(f"fingerprint: {fingerprint}")
            if args.app_path:
                break
        if not captures:
            raise SystemExit("no page was captured")
        return 0
    finally:
        with contextlib.suppress(Exception):
            await client.close_session(session_id)
        await client.aclose()
        await _stop_companion(process)


def _field_specs(app: dict[str, object]) -> list[object]:
    from personal_assistant.core.browser import AdapterFieldSpec

    structure = app["structure"]
    assert isinstance(structure, dict)
    specs: list[object] = []
    seen: set[str] = set()
    for control in structure.get("controls", []):
        if not isinstance(control, dict):
            continue
        name = str(control.get("name") or control.get("element_id") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        kind = "select" if control.get("tag") == "select" else "text"
        specs.append(
            AdapterFieldSpec(
                field_id=name,
                label=name,
                kind=kind,
                required=bool(control.get("required", False)),
                max_length=int(control.get("max_length") or 0),
                locator=str(control.get("locator", "")),
            )
        )
    return specs


def _first_real_option(options: object) -> str:
    if not isinstance(options, (tuple, list)):
        return ""
    return next(
        (
            str(item)
            for item in options
            if str(item).strip() not in {"", "0", "-1", "请选择", "请选择...", "请选择……"}
        ),
        "",
    )


def _option_value_by_label(control: object, label: str) -> str:
    if not isinstance(control, dict):
        return ""
    values = control.get("options")
    labels = control.get("option_labels")
    if not isinstance(values, (tuple, list)) or not isinstance(labels, (tuple, list)):
        return ""
    matches = [
        str(value)
        for value, candidate in zip(values, labels, strict=False)
        if str(candidate).strip() == label
    ]
    return matches[0] if len(matches) == 1 else ""


def _volunteer_dropdown_status(controls: object) -> tuple[bool, bool]:
    if not isinstance(controls, (tuple, list)) or len(controls) < 9:
        return False, False

    def selected(display_index: int, native_index: int) -> bool:
        display = controls[display_index]
        native = controls[native_index]
        if not isinstance(display, dict) or not isinstance(native, dict):
            return False
        shown = str(display.get("value", "")).strip()
        value = str(native.get("value", "")).strip()
        return bool(shown and value and shown not in {"请选择", "请选择...", "请选择……"})

    return selected(5, 7), selected(6, 8)


def _volunteer_match_flags(
    controls: object, expected: dict[str, str]
) -> dict[str, bool]:
    positions = {
        "name": 0,
        "start": 1,
        "end": 2,
        "team_role": 3,
        "hours": 4,
        "college": 7,
        "reviewer": 8,
    }
    if not isinstance(controls, (tuple, list)):
        return {key: False for key in expected if key in positions}
    return {
        key: (
            len(controls) > positions[key]
            and isinstance(controls[positions[key]], dict)
            and str(controls[positions[key]].get("value", "")) == value
        )
        for key, value in expected.items()
        if key in positions
    }


def _volunteer_sample_values(college: str) -> dict[str, str]:
    return {
        "name": "自动化测试请勿提交",
        "start": "2026-09-27 09:00",
        "end": "2026-09-28",
        "team_role": "无",
        "hours": "1.0",
        "college": college,
    }


def _volunteer_descriptor(
    app: dict[str, object], *, origin: str, fingerprint: str,
    adapter_version: str = "1.0.0",
):
    """Human-reviewed field map for the one real F07 acceptance form."""

    from personal_assistant.core.browser import AdapterFieldSpec, TransactionAdapterDescriptor
    from personal_assistant.domain.enums import RiskLevel

    structure = app.get("structure")
    controls = structure.get("controls") if isinstance(structure, dict) else None
    expected = (
        ("ctl:0:0", "input", "data.mc", False, "name", "名称", True, 20),
        ("ctl:0:1", "input", "data.kssj", False, "start", "开始时间", True, 0),
        ("ctl:0:2", "input", "data.jssj", False, "end", "结束时间", True, 0),
        ("ctl:0:3", "input", "preview-bdx", False, "team_role", "团队职务", True, 0),
        ("ctl:0:4", "input", "preview-bdx", False, "hours", "服务时长", True, 0),
        ("ctl:0:5", "input", "", True, "college_display", "审核学院显示值", False, 0),
        ("ctl:0:6", "input", "", True, "reviewer_display", "审核人显示值", False, 0),
        ("ctl:1:0", "select", "data.shxy.id", False, "college", "审核学院", True, 0),
        ("ctl:1:1", "select", "", False, "reviewer", "审核人", False, 0),
    )
    if not isinstance(controls, list) or len(controls) != len(expected):
        raise SystemExit("real volunteer form control count changed; do not fill")
    specs = []
    for observed, row in zip(controls, expected, strict=True):
        if not isinstance(observed, dict):
            raise SystemExit("real volunteer form control is malformed")
        locator, tag, name, readonly, field_id, label, required, max_length = row
        if (
            observed.get("locator") != locator
            or observed.get("tag") != tag
            or observed.get("name") != name
            or bool(observed.get("readonly")) != readonly
            or bool(observed.get("disabled"))
        ):
            raise SystemExit("real volunteer form controls changed; do not fill")
        specs.append(
            AdapterFieldSpec(
                field_id=field_id,
                locator=locator,
                label=label,
                kind="select" if tag == "select" else "text",
                required=required,
                max_length=max_length,
                pattern=r"\d+(?:\.\d)?" if field_id == "hours" else "",
            )
        )
    return TransactionAdapterDescriptor(
        extension_id=EXTENSION_ID,
        extension_version=EXTENSION_VERSION,
        adapter_id="nju.ehall.volunteer",
        adapter_version=adapter_version,
        display_name="第二课堂：志愿服务经历（申请）",
        allowed_origins=(origin,),
        allowed_paths=("/tw/xssq/xssq/create",),
        declared_risk=RiskLevel.EXTERNAL_WRITE,
        transaction_ids=("second_class.volunteer",),
        fields=tuple(specs),
        actions=(),
        allowed_page_fingerprints=(fingerprint,),
        consequences="页面显示“提交”按钮；具体提交后果和写入目标尚未核实。",
    )


def _final_action(app: dict[str, object], origins: tuple[str, ...], target_path: str):
    from personal_assistant.core.browser import AdapterActionSpec
    from personal_assistant.domain.enums import RiskLevel

    for index, action in enumerate(app.get("actions", [])):
        if not isinstance(action, dict):
            continue
        if str(action.get("kind", "")) not in {"submit", "button"}:
            continue
        return AdapterActionSpec(
            action_id=f"live.{index}",
            locator=str(action.get("locator", "")),
            label=str(action.get("label", "提交"))[:200],
            kind="submit",
            risk=RiskLevel.EXTERNAL_WRITE,
            final=True,
            method="POST",
            target_origin=origins[0],
            target_path=target_path,
        )
    return None


def _form_target_path(app: dict[str, object]) -> str:
    structure = app["structure"]
    assert isinstance(structure, dict)
    for form in structure.get("forms", []):
        if not isinstance(form, dict):
            continue
        action = str(form.get("action", "")).strip()
        method = str(form.get("method", "")).lower()
        if not action or method not in {"post", "get"}:
            continue
        path = urlsplit(action).path or "/"
        if path.startswith("/") and "?" not in path and "#" not in path and ".." not in path:
            return path
    return ""


async def preview(args: argparse.Namespace) -> int:
    from personal_assistant.core.browser import (
        BrowserSessionBroker,
        FieldChange,
        FieldValueSource,
        FillPlan,
        TransactionAdapterDescriptor,
    )
    from personal_assistant.domain.enums import RiskLevel
    from personal_assistant.infrastructure.browser.companion_client import (
        LoopbackCompanionClient,
    )
    from personal_assistant.infrastructure.memory.browser import (
        InMemoryBrowserAdapterStore,
        InMemoryBrowserSessionStore,
    )

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    app = report["app"]
    origins = tuple(report["origins"])
    entry = str(report["entry"])
    app_path = str(app["path"])
    fingerprint = str(app["fingerprint"])
    if app.get("requires_adapter_navigation"):
        raise SystemExit(
            "the captured transaction URL has dynamic query parameters; "
            "build a verified adapter navigation instead of replaying it"
        )
    target_path = args.target_path or _form_target_path(app)
    if not target_path:
        raise SystemExit(
            "the captured page declares no static form action; pass --target-path "
            "with the verified submission path before previewing"
        )
    final = _final_action(app, origins, target_path)
    if final is None:
        raise SystemExit("the captured page declares no submit action; nothing to preview")
    fields = _field_specs(app)
    if not fields:
        raise SystemExit("the captured page declares no named controls; nothing to fill")
    values: dict[str, str] = {}
    if args.values:
        values = json.loads(Path(args.values).read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            raise SystemExit("--values must be a JSON object of field name -> value")
    missing = [
        spec.field_id
        for spec in fields
        if spec.required and not str(values.get(spec.field_id, "")).strip()  # type: ignore[attr-defined]
    ]
    if missing:
        raise SystemExit(
            "missing required values in --values: " + ", ".join(sorted(missing))
        )

    descriptor = TransactionAdapterDescriptor(
        extension_id=EXTENSION_ID,
        extension_version=EXTENSION_VERSION,
        adapter_id=ADAPTER_ID,
        adapter_version=ADAPTER_VERSION,
        display_name=str(app.get("title") or "真实事务预览"),
        allowed_origins=origins,
        allowed_paths=(app_path,),
        declared_risk=RiskLevel.EXTERNAL_WRITE,
        transaction_ids=(TRANSACTION_ID,),
        fields=tuple(fields),
        actions=(final,),
        login_paths=(),
        discovery_path="/",
        receipt_locator="text:回执号",
        allowed_page_fingerprints=(fingerprint,),
        consequences="本步骤只生成权威预览；提交在本脚本中始终关闭。",
    )

    entry_host = urlsplit(entry).hostname or ""
    sso_hosts = frozenset(
        host
        for host in (urlsplit(origin).hostname or "" for origin in origins)
        if host and host != entry_host
    )
    log_path = Path(args.report).with_suffix(".preview.log")
    process, handshake = await _start_companion()
    client = LoopbackCompanionClient(
        base_url=f"http://127.0.0.1:{handshake['port']}",
        root_capability=str(handshake["capability"]),
    )
    broker = BrowserSessionBroker(
        companion=client,
        sessions=InMemoryBrowserSessionStore(),
        adapters=InMemoryBrowserAdapterStore(),
        allowed_origins=frozenset(origins),
        origin_mode=str(report.get("origin_mode", "allowlist")),
        submit_enabled=False,
        id_factory=lambda: f"ehall-real-{secrets.token_hex(4)}",
        nonce_factory=lambda: secrets.token_urlsafe(24),
    )
    record = await broker.register_adapter(descriptor)
    _log(log_path, {"phase": "preview", "event": "adapter_pinned", "adapter_version": record.adapter_version})
    try:
        session = await broker.create_session(
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            extension_version=EXTENSION_VERSION,
            purpose="真实 ehall 填写至预览（不提交）",
        )
        session_id = session.session_id
        first = await broker.navigate(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id=ADAPTER_ID,
            transaction_id="",
            url=entry,
        )
        _log(
            log_path,
            {
                "phase": "preview",
                "event": "entry_opened",
                "login_page": not first.authenticated,
                "hint": "请在 Chromium 窗口内亲自完成 SSO；脚本不会输入任何凭据",
            },
        )
        deadline = time.monotonic() + args.login_timeout
        seen_challenge = False
        while time.monotonic() < deadline:
            live = await broker.snapshot(session_id, extension_id=EXTENSION_ID)
            host = urlsplit(live.url).hostname or ""
            login_page = (not live.authenticated) or (bool(host) and host in sso_hosts)
            if login_page:
                seen_challenge = True
            if seen_challenge and not login_page and live.url:
                break
            await asyncio.sleep(POLL_SECONDS)
        else:
            raise SystemExit("timed out waiting for the user to finish SSO")

        snapshot = await broker.navigate(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id=ADAPTER_ID,
            transaction_id=TRANSACTION_ID,
            url=str(app["url"]),
        )
        await broker.record_discovery(session_id, extension_id=EXTENSION_ID, app_count=1)
        await broker.record_preparation(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id=ADAPTER_ID,
            adapter_version=ADAPTER_VERSION,
            app_id=ADAPTER_ID,
            transaction_id=TRANSACTION_ID,
            page_fingerprint=snapshot.fingerprint,
            planned_fields=len(fields),
        )
        live = {item.field_id: item for item in snapshot.fields}
        changes = []
        for spec in fields:
            if spec.field_id not in live:
                raise SystemExit(f"required control {spec.field_id!r} is missing on the live page")
            changes.append(
                FieldChange(
                    field_id=spec.field_id,
                    locator=spec.locator,
                    label=spec.label,
                    old_value=live[spec.field_id].value,
                    new_value=str(values.get(spec.field_id, "")),
                    source=FieldValueSource.USER_INPUT,
                )
            )
        plan = FillPlan(
            adapter_id=ADAPTER_ID,
            adapter_version=ADAPTER_VERSION,
            transaction_id=TRANSACTION_ID,
            expected_origin=origins[0],
            expected_page_fingerprint=snapshot.fingerprint,
            app_id=ADAPTER_ID,
            consequences="真实站点预览验收：仅本地填写，不提交。",
            fields=tuple(changes),
        )
        preview_result = await broker.execute_fill(
            session_id, task_id=TASK_ID, extension_id=EXTENSION_ID, plan=plan
        )
        payload = {
            "mode": "preview",
            "finished_at": _now(),
            "session_id": session_id,
            "state": "PREVIEW_READY",
            "origin": preview_result.origin,
            "page_fingerprint": preview_result.page_fingerprint,
            "canonical_payload_hash": preview_result.canonical_payload_hash,
            "risk": preview_result.risk.value,
            "fields": [
                {
                    "field_id": item.field_id,
                    "old_value": item.old_value,
                    "new_value": item.new_value,
                }
                for item in preview_result.fields
            ],
            "missing_fields": list(preview_result.missing_fields),
            "submit_enabled": False,
        }
        _write_report(Path(args.out), payload)
        _log(log_path, {"phase": "preview", "event": "preview_ready", **payload})
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print("\nsubmit stays disabled: this harness has no submit path")
        return 0
    finally:
        with contextlib.suppress(Exception):
            await broker.cancel_session(session_id, extension_id=EXTENSION_ID)
        await broker.aclose()
        await _stop_companion(process)


async def preview_current_volunteer(args: argparse.Namespace) -> int:
    """Fill the reviewed real nested form through Broker; never submit."""

    from personal_assistant.core.approvals.canonicalize import canonical_sha256
    from personal_assistant.core.browser import (
        BrowserSessionBroker,
        FieldChange,
        FieldValueSource,
        FillPlan,
        page_structure_document,
    )
    from personal_assistant.core.browser.errors import ProhibitedTransactionError
    from personal_assistant.infrastructure.browser.companion_client import (
        LoopbackCompanionClient,
    )
    from personal_assistant.infrastructure.memory.browser import (
        InMemoryBrowserAdapterStore,
        InMemoryBrowserSessionStore,
    )

    origin = "https://youth.nju.edu.cn"
    path = "/tw/xssq/xssq/create"
    origins = _normalize_origins(args.origins)
    process, handshake = await _start_companion(allow_navigation_posts=True)
    client = LoopbackCompanionClient(
        base_url=f"http://127.0.0.1:{handshake['port']}",
        root_capability=str(handshake["capability"]),
        command_timeout_seconds=35,
    )
    broker = BrowserSessionBroker(
        companion=client,
        sessions=InMemoryBrowserSessionStore(),
        adapters=InMemoryBrowserAdapterStore(),
        allowed_origins=frozenset(origins),
        origin_mode="open",
        submit_enabled=False,
        id_factory=lambda: f"ehall-volunteer-{secrets.token_hex(4)}",
        nonce_factory=lambda: secrets.token_urlsafe(24),
    )
    session_id = ""
    try:
        session = await broker.create_session(
            task_id=TASK_ID,
            extension_id=EXTENSION_ID,
            extension_version=EXTENSION_VERSION,
            purpose="真实第二课堂表单的受监督提交前测试（不提交）",
        )
        session_id = session.session_id
        await client.navigate(session_id, args.entry)
        print(
            "Chromium 已打开。请自行登录并进入：第二课堂 → "
            "第二课堂成绩单（在线办理） → 志愿服务经历（申请）。"
        )
        print("停在申请表，勿点“提交”；完成后由操作端继续。", flush=True)
        await asyncio.to_thread(input, "已到申请表时按回车扫描：")
        adopted = await client.adopt_opened_page(session_id)
        selected = await client.select_frame(session_id, origin=origin, path=path)
        raw = dict(await client.snapshot(session_id, scan_text=True))
        if _safe_url(str(raw.get("url", ""))) != origin + path:
            raise SystemExit("the selected frame is not the reviewed volunteer form")
        fingerprint = _fingerprint(raw)
        structure = raw.get("structure")
        if not isinstance(structure, dict):
            raise SystemExit("the form structure is missing")
        document = page_structure_document(
            controls=structure.get("controls", ()),
            headings=structure.get("headings", ()),
            links=raw.get("links", ()),
            forms=structure.get("forms", ()),
            hidden_fields=structure.get("hidden_fields", ()),
        )
        descriptor = _volunteer_descriptor(raw, origin=origin, fingerprint=fingerprint)
        observation = {
            "captured_at": _now(),
            "supervised": True,
            "submitted": False,
            "url": origin + path,
            "adopted_new_tab": bool(adopted.get("adopted")),
            "selected_frame": selected.get("path"),
            "fingerprint": fingerprint,
            "structure_hashes": {
                key: canonical_sha256(value) for key, value in document.items()
            },
            "controls": [
                {
                    "locator": item.get("locator"),
                    "tag": item.get("tag"),
                    "type": item.get("type"),
                    "name": item.get("name"),
                    "readonly": item.get("readonly"),
                    "disabled": item.get("disabled"),
                    "option_count": len(item.get("options", ())),
                }
                for item in structure.get("controls", ())
                if isinstance(item, dict)
            ],
            "actions": [
                {
                    "locator": item.get("locator"),
                    "label": item.get("label"),
                    "tag": item.get("tag"),
                    "html_type": item.get("html_type"),
                }
                for item in raw.get("actions", ())
                if isinstance(item, dict)
            ],
            "forms": [
                {**item, "action": _safe_url(str(item.get("action", "")))}
                for item in structure.get("forms", ())
                if isinstance(item, dict)
            ],
        }
        observation_path = args.out.with_suffix(".observation.json")
        _write_report(observation_path, observation)
        print(f"已采集脱敏结构：{observation_path}", flush=True)
        print(f"页面指纹：{fingerprint}", flush=True)
        review = await asyncio.to_thread(
            input, "审核结构后输入页面指纹前 8 位以继续试填："
        )
        if review.strip() != fingerprint[:8]:
            raise SystemExit("page review did not match; no fields were filled")
        await broker.register_adapter(descriptor)
        snapshot, assessment = await broker.snapshot(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id=descriptor.adapter_id,
            transaction_id=descriptor.transaction_ids[0],
        )
        if assessment.prohibited:
            raise SystemExit("the form is prohibited by the supervised risk assessment")
        await broker.record_discovery(session_id, extension_id=EXTENSION_ID, app_count=1)
        await broker.record_preparation(
            session_id,
            extension_id=EXTENSION_ID,
            adapter_id=descriptor.adapter_id,
            adapter_version=descriptor.adapter_version,
            app_id=descriptor.adapter_id,
            transaction_id=descriptor.transaction_ids[0],
            page_fingerprint=snapshot.fingerprint,
            planned_fields=6,
        )
        college_control = structure["controls"][7]
        college = _option_value_by_label(college_control, "文学院")
        if not college:
            raise SystemExit("文学院 option was not uniquely observed; no fields were filled")
        values = _volunteer_sample_values(college)
        changes = []
        for spec in descriptor.fields:
            if not spec.required:
                continue
            live = snapshot.field_by_id(spec.field_id)
            if live is None:
                raise SystemExit(f"required field {spec.field_id} is missing")
            changes.append(
                FieldChange(
                    field_id=spec.field_id,
                    locator=spec.locator,
                    label=spec.label,
                    old_value=live.value,
                    new_value=values[spec.field_id],
                    source=FieldValueSource.TEMPLATE,
                )
            )
        plan = FillPlan(
            adapter_id=descriptor.adapter_id,
            adapter_version=descriptor.adapter_version,
            transaction_id=descriptor.transaction_ids[0],
            expected_origin=origin,
            expected_page_fingerprint=snapshot.fingerprint,
            app_id=descriptor.adapter_id,
            consequences=descriptor.consequences,
            fields=tuple(changes),
        )
        try:
            first_preview = None
            try:
                first_preview = await broker.execute_fill(
                    session_id, task_id=TASK_ID, extension_id=EXTENSION_ID,
                    plan=plan,
                )
            except ProhibitedTransactionError as exc:
                if exc.reason != "UNKNOWN_PAGE_VERSION":
                    raise
            post_college = dict(await client.snapshot(session_id, scan_text=True))
            post_structure = post_college.get("structure")
            post_controls = (
                post_structure.get("controls")
                if isinstance(post_structure, dict)
                else None
            )
            college_displayed, _ = _volunteer_dropdown_status(post_controls)
            if not college_displayed:
                raise RuntimeError("COLLEGE_NOT_VISIBLE")
            assert isinstance(post_controls, list)
            if not all(_volunteer_match_flags(post_controls, values).values()):
                raise RuntimeError("INITIAL_FIELD_MISMATCH")
            reviewer = _first_real_option(post_controls[8].get("options", ()))
            if not reviewer:
                raise RuntimeError("DEPENDENT_REVIEWER_OPTIONS_UNAVAILABLE")
            second_fingerprint = _fingerprint(post_college)
            second_descriptor = _volunteer_descriptor(
                post_college,
                origin=origin,
                fingerprint=second_fingerprint,
                adapter_version="1.0.1",
            )
            _write_report(
                args.out.with_name(args.out.stem + ".after-college.observation.json"),
                {
                    "captured_at": _now(),
                    "fingerprint": second_fingerprint,
                    "selected_frame": path,
                    "submitted": False,
                    "controls": [
                        {
                            "locator": item.get("locator"),
                            "tag": item.get("tag"),
                            "name": item.get("name"),
                            "readonly": item.get("readonly"),
                            "option_count": len(item.get("options", ())),
                        }
                        for item in post_controls
                    ],
                },
            )
            print(f"学院选择后页面指纹：{second_fingerprint}", flush=True)
            review_next = await asyncio.to_thread(
                input, "核对结构后输入新指纹前 8 位以继续审核人："
            )
            if review_next.strip() != second_fingerprint[:8]:
                raise RuntimeError("DEPENDENT_VERSION_NOT_REVIEWED")
            await broker.register_adapter(second_descriptor)
            await broker.record_preparation(
                session_id,
                extension_id=EXTENSION_ID,
                adapter_id=second_descriptor.adapter_id,
                adapter_version=second_descriptor.adapter_version,
                app_id=second_descriptor.adapter_id,
                transaction_id=second_descriptor.transaction_ids[0],
                page_fingerprint=second_fingerprint,
                planned_fields=7,
            )
            second_snapshot, second_assessment = await broker.snapshot(
                session_id,
                extension_id=EXTENSION_ID,
                adapter_id=second_descriptor.adapter_id,
                transaction_id=second_descriptor.transaction_ids[0],
            )
            if second_assessment.prohibited:
                raise RuntimeError("DEPENDENT_FORM_PROHIBITED")
            values["reviewer"] = reviewer
            second_changes = []
            for spec in second_descriptor.fields:
                if spec.field_id not in values:
                    continue
                live = second_snapshot.field_by_id(spec.field_id)
                if live is None:
                    raise RuntimeError("DEPENDENT_FIELD_MISSING")
                second_changes.append(
                    FieldChange(
                        field_id=spec.field_id,
                        locator=spec.locator,
                        label=spec.label,
                        old_value=live.value,
                        new_value=values[spec.field_id],
                        source=FieldValueSource.TEMPLATE,
                    )
                )
            second_plan = FillPlan(
                adapter_id=second_descriptor.adapter_id,
                adapter_version=second_descriptor.adapter_version,
                transaction_id=second_descriptor.transaction_ids[0],
                expected_origin=origin,
                expected_page_fingerprint=second_snapshot.fingerprint,
                app_id=second_descriptor.adapter_id,
                consequences=second_descriptor.consequences,
                fields=tuple(second_changes),
            )
            preview_result = None
            try:
                preview_result = await broker.execute_fill(
                    session_id, task_id=TASK_ID, extension_id=EXTENSION_ID,
                    plan=second_plan,
                )
            except ProhibitedTransactionError as exc:
                if exc.reason != "UNKNOWN_PAGE_VERSION":
                    raise
            final_raw = dict(await client.snapshot(session_id, scan_text=True))
            final_structure = final_raw.get("structure")
            final_controls = (
                final_structure.get("controls")
                if isinstance(final_structure, dict)
                else None
            )
            if _volunteer_dropdown_status(final_controls) != (True, True):
                raise RuntimeError("REVIEWER_NOT_VISIBLE")
            if not all(_volunteer_match_flags(final_controls, values).values()):
                raise RuntimeError("FINAL_FIELD_MISMATCH")
            if preview_result is None:
                final_fingerprint = _fingerprint(final_raw)
                final_descriptor = _volunteer_descriptor(
                    final_raw, origin=origin, fingerprint=final_fingerprint,
                    adapter_version="1.0.2",
                )
                assert isinstance(final_controls, list)
                _write_report(
                    args.out.with_name(args.out.stem + ".after-reviewer.observation.json"),
                    {
                        "captured_at": _now(),
                        "fingerprint": final_fingerprint,
                        "selected_frame": path,
                        "submitted": False,
                        "controls": [
                            {
                                "locator": item.get("locator"),
                                "tag": item.get("tag"),
                                "name": item.get("name"),
                                "readonly": item.get("readonly"),
                                "option_count": len(item.get("options", ())),
                            }
                            for item in final_controls
                        ],
                    },
                )
                print(f"审核人选择后页面指纹：{final_fingerprint}", flush=True)
                final_review = await asyncio.to_thread(
                    input, "核对结构后输入新指纹前 8 位以生成预览："
                )
                if final_review.strip() != final_fingerprint[:8]:
                    raise RuntimeError("FINAL_VERSION_NOT_REVIEWED")
                await broker.register_adapter(final_descriptor)
                await broker.record_preparation(
                    session_id,
                    extension_id=EXTENSION_ID,
                    adapter_id=final_descriptor.adapter_id,
                    adapter_version=final_descriptor.adapter_version,
                    app_id=final_descriptor.adapter_id,
                    transaction_id=final_descriptor.transaction_ids[0],
                    page_fingerprint=final_fingerprint,
                    planned_fields=7,
                )
                final_snapshot, final_assessment = await broker.snapshot(
                    session_id,
                    extension_id=EXTENSION_ID,
                    adapter_id=final_descriptor.adapter_id,
                    transaction_id=final_descriptor.transaction_ids[0],
                )
                if final_assessment.prohibited:
                    raise RuntimeError("FINAL_FORM_PROHIBITED")
                final_changes = []
                for spec in final_descriptor.fields:
                    if spec.field_id not in values:
                        continue
                    live = final_snapshot.field_by_id(spec.field_id)
                    if live is None or live.value != values[spec.field_id]:
                        raise RuntimeError("FINAL_FIELD_MISMATCH")
                    final_changes.append(
                        FieldChange(
                            field_id=spec.field_id,
                            locator=spec.locator,
                            label=spec.label,
                            old_value=live.value,
                            new_value=live.value,
                            source=FieldValueSource.TEMPLATE,
                        )
                    )
                final_plan = FillPlan(
                    adapter_id=final_descriptor.adapter_id,
                    adapter_version=final_descriptor.adapter_version,
                    transaction_id=final_descriptor.transaction_ids[0],
                    expected_origin=origin,
                    expected_page_fingerprint=final_snapshot.fingerprint,
                    app_id=final_descriptor.adapter_id,
                    consequences=final_descriptor.consequences,
                    fields=tuple(final_changes),
                )
                preview_result = await broker.execute_fill(
                    session_id, task_id=TASK_ID, extension_id=EXTENSION_ID,
                    plan=final_plan,
                )
        except Exception as exc:
            failed_locator = ""
            live_match: dict[str, bool] = {}
            live_dropdowns = (False, False)
            live_fingerprint = ""
            live_hidden_names: list[str] = []
            with contextlib.suppress(Exception):
                diagnostic_raw = dict(await client.snapshot(session_id))
                diagnostic_structure = diagnostic_raw.get("structure")
                if isinstance(diagnostic_structure, dict):
                    diagnostic_controls = diagnostic_structure.get("controls")
                    live_dropdowns = _volunteer_dropdown_status(diagnostic_controls)
                    if "values" in locals():
                        live_match = _volunteer_match_flags(diagnostic_controls, values)
                    live_hidden_names = [
                        str(item.get("name", ""))[:100]
                        for item in diagnostic_structure.get("hidden_fields", ())
                        if isinstance(item, dict)
                    ][:32]
                    live_fingerprint = _fingerprint(diagnostic_raw)
            with contextlib.suppress(Exception):
                diagnostics = dict(await client.diagnostics())
                for item in diagnostics.get("sessions", ()):
                    if isinstance(item, dict) and item.get("session_id") == session_id:
                        driver = item.get("driver")
                        if isinstance(driver, dict):
                            failed_locator = str(driver.get("last_fill_locator", ""))
            diagnostics_summary = {}
            with contextlib.suppress(Exception):
                diagnostics_summary = _diagnostic_summary(dict(await client.diagnostics()))
            _write_report(
                args.out,
                {
                    "captured_at": _now(),
                    "mode": "preview-current-volunteer",
                    "supervised": True,
                    "state": (
                        str(exc)
                        if isinstance(exc, RuntimeError)
                        and str(exc) in {
                            "COLLEGE_NOT_VISIBLE",
                            "INITIAL_FIELD_MISMATCH",
                            "DEPENDENT_REVIEWER_OPTIONS_UNAVAILABLE",
                            "DEPENDENT_FORM_PROHIBITED",
                            "DEPENDENT_FIELD_MISSING",
                            "REVIEWER_NOT_VISIBLE",
                            "DEPENDENT_VERSION_NOT_REVIEWED",
                            "FINAL_VERSION_NOT_REVIEWED",
                            "FINAL_FORM_PROHIBITED",
                            "FINAL_FIELD_MISMATCH",
                        }
                        else "FILL_FAILED"
                    ),
                    "submitted": False,
                    "error_type": type(exc).__name__,
                    "error_reason": (
                        str(getattr(exc, "reason", ""))
                        if re.fullmatch(r"[A-Z_]{1,80}", str(getattr(exc, "reason", "")))
                        else ""
                    ),
                    "last_fill_locator": failed_locator,
                    "live_match": live_match,
                    "live_dropdowns_displayed": list(live_dropdowns),
                    "live_fingerprint": live_fingerprint,
                    "hidden_field_names": live_hidden_names,
                    "visible_college": bool(locals().get("college_displayed", False)),
                    "reviewer_option_count": (
                        len(post_controls[8].get("options", ()))
                        if isinstance(locals().get("post_controls"), list)
                        and len(post_controls) > 8
                        and isinstance(post_controls[8], dict)
                        else 0
                    ),
                    "blocked_mutating_requests": diagnostics_summary.get(
                        "blocked_mutating_requests", 0
                    ),
                    "acceptance_complete": False,
                },
            )
            print(f"试填未完成；诊断已保存到 {args.out}", flush=True)
            await asyncio.to_thread(input, "请核对当前页面；按回车关闭，勿提交：")
            raise
        initial_old = {item.field_id: item.old_value for item in changes}
        payload = {
            "captured_at": _now(),
            "mode": "preview-current-volunteer",
            "supervised": True,
            "state": "PREVIEW_READY",
            "submitted": False,
            "sample_values": True,
            "page_fingerprint": preview_result.page_fingerprint,
            "canonical_payload_hash": preview_result.canonical_payload_hash,
            "risk": preview_result.risk.value,
            "visible_college_and_reviewer": True,
            "fields": [
                {
                    "field_id": item.field_id,
                    "label": item.label,
                    "old_value": (
                        "" if not initial_old.get(item.field_id, item.old_value)
                        else "[预填值已隐藏]"
                    ),
                    "new_value": (
                        "[审核人选项值已遮盖]"
                        if item.field_id == "reviewer"
                        else item.new_value
                    ),
                    "source": item.source.value,
                }
                for item in preview_result.fields
            ],
            "missing_fields": list(preview_result.missing_fields),
            "attachments": [
                {"name": item.name, "sha256": item.sha256}
                for item in preview_result.attachments
            ],
            "consequences": preview_result.consequences,
            "target_bound": bool(preview_result.target_action_id),
            "acceptance_complete": False,
            "remaining_gaps": [
                "real submit target is not verified",
                "material and post-submit consequences are not verified on the live page",
            ],
        }
        _write_report(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
        await asyncio.to_thread(input, "请在 Chromium 核对试填值；按回车关闭，勿提交：")
        return 0
    finally:
        if session_id:
            with contextlib.suppress(Exception):
                await broker.cancel_session(session_id, extension_id=EXTENSION_ID)
        await broker.aclose()
        await _stop_companion(process)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)

    capture_parser = sub.add_parser("capture", help="read-only portal/app capture")
    capture_parser.add_argument("--origins", default=DEFAULT_ORIGIN)
    capture_parser.add_argument(
        "--origin-mode", choices=("allowlist", "open"), default="open"
    )
    capture_parser.add_argument("--entry", default="")
    capture_parser.add_argument("--app-path", default="")
    capture_parser.add_argument("--choice-file", type=Path, default=Path("ehall_choice.txt"))
    capture_parser.add_argument("--login-timeout", type=float, default=DEFAULT_LOGIN_TIMEOUT)
    capture_parser.add_argument("--choice-timeout", type=float, default=DEFAULT_CHOICE_TIMEOUT)
    capture_parser.add_argument("--portal-wait", type=float, default=30.0)
    capture_parser.add_argument("--out", type=Path, default=Path("ehall_capture.json"))

    preview_parser = sub.add_parser("preview", help="fill to PREVIEW_READY (no submit)")
    preview_parser.add_argument("--report", type=Path, required=True)
    preview_parser.add_argument("--values", type=Path, default=None)
    preview_parser.add_argument("--target-path", default="")
    preview_parser.add_argument("--login-timeout", type=float, default=DEFAULT_LOGIN_TIMEOUT)
    preview_parser.add_argument("--out", type=Path, default=Path("ehall_preview.json"))

    volunteer_parser = sub.add_parser(
        "preview-current-volunteer",
        help="supervised nested volunteer form dry run; never submit",
    )
    volunteer_parser.add_argument(
        "--origins",
        default="https://ehall.nju.edu.cn,https://authserver.nju.edu.cn,https://youth.nju.edu.cn",
    )
    volunteer_parser.add_argument(
        "--entry", default="https://ehall.nju.edu.cn/ywtb-portal/index.html"
    )
    volunteer_parser.add_argument(
        "--out", type=Path, default=Path("ehall_volunteer_preview.json")
    )

    args = parser.parse_args()
    if args.mode == "capture":
        return asyncio.run(capture(args))
    if args.mode == "preview-current-volunteer":
        return asyncio.run(preview_current_volunteer(args))
    return asyncio.run(preview(args))


if __name__ == "__main__":
    raise SystemExit(main())
