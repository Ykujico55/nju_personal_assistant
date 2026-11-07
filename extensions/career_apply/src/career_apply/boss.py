"""Visible BOSS conversation actions, invoked only by the host's approved R2 path.

No browser is launched here. The user explicitly supplies a loopback CDP endpoint
and logs in personally. The adapter disconnects its client without closing the
user's browser. UI selectors require separate live-site verification.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import ipaddress
import json
import re
import sys
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from .domain import Job, ResumeRef, boss_job_id, canonical_digest, normalize_boss_url

_SHA = re.compile(r"[a-f0-9]{64}\Z")
_RECEIPT = re.compile(r"boss-dom:[A-Za-z0-9_-]{1,128}\Z")


class BossNeedsUser(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ResumeArtifacts(Protocol):
    async def read(self, artifact_id: str) -> bytes: ...


def _resume_media(filename: str) -> str:
    media = {
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    if Path(filename).suffix.lower() not in media:
        raise BossNeedsUser("BOSS_RESUME_FORMAT_UNSUPPORTED")
    return media[Path(filename).suffix.lower()]


class BossPagePort(Protocol):
    async def call(self, method: str, argument: Mapping[str, Any]) -> Mapping[str, Any]: ...


def _endpoint(value: str) -> str:
    if not isinstance(value, str) or any(char.isspace() for char in value):
        raise ValueError("CDP endpoint must be literal loopback HTTP with an explicit port")
    try:
        parsed = urlsplit(value)
        address = ipaddress.ip_address(parsed.hostname or "")
        if (
            parsed.scheme != "http"
            or not address.is_loopback
            or parsed.port is None
            or not 1 <= parsed.port <= 65535
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError()
    except ValueError:
        raise ValueError(
            "CDP endpoint must be literal loopback HTTP with an explicit port"
        ) from None
    return value.rstrip("/")


def _target(value: Job | Mapping[str, Any]) -> dict[str, str]:
    material = value.as_dict() if isinstance(value, Job) else value
    if not isinstance(material, Mapping):
        raise BossNeedsUser("BOSS_TARGET_INVALID")
    fields: dict[str, str] = {}
    for name in ("url", "job_id", "company", "recruiter"):
        item = material.get(name)
        if (
            not isinstance(item, str)
            or not item
            or len(item) > 2048
            or any(ord(char) < 32 or ord(char) == 127 for char in item)
        ):
            raise BossNeedsUser("BOSS_TARGET_INVALID")
        fields[name] = item
    try:
        fields["url"] = normalize_boss_url(fields["url"])
        if boss_job_id(fields["url"]) != fields["job_id"]:
            raise ValueError()
    except ValueError:
        raise BossNeedsUser("BOSS_TARGET_INVALID") from None
    return fields


class BossAdapter:
    def __init__(
        self,
        endpoint: str | None,
        *,
        port: BossPagePort | None = None,
        timeout_seconds: float = 15.0,
        script_root: Path | None = None,
        artifacts: ResumeArtifacts | None = None,
    ) -> None:
        if isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 60:
            raise ValueError("browser timeout must be positive and at most 60 seconds")
        selected = _endpoint(endpoint) if endpoint else None
        self._port = port or (
            PlaywrightBossPort(selected, script_root=script_root) if selected else None
        )
        self._timeout = timeout_seconds
        self._artifacts = artifacts
        # Only a worker-lifetime guard. Cross-process authority remains the host Outbox.
        self._attempts: dict[str, tuple[str, dict[str, Any]]] = {}

    async def _call(self, method: str, argument: Mapping[str, Any]) -> Mapping[str, Any]:
        if self._port is None:
            raise BossNeedsUser("BOSS_BROWSER_DISABLED")
        async with asyncio.timeout(self._timeout):
            return await self._port.call(method, copy.deepcopy(dict(argument)))

    async def _resume(self, value: Mapping[str, Any] | None) -> tuple[dict[str, Any], bytes]:
        if not isinstance(value, Mapping) or self._artifacts is None:
            raise BossNeedsUser("BOSS_RESUME_MATERIAL_UNVERIFIED")
        try:
            name = value.get("filename", value.get("name"))
            sha256, size = value.get("sha256"), value.get("size_bytes")
            if not isinstance(name, str) or not isinstance(sha256, str) or type(size) is not int:
                raise ValueError()
            reference = ResumeRef(
                filename=name,
                sha256=sha256,
                size_bytes=size,
                media_type=value.get("media_type", _resume_media(name)),
                artifact_id=value.get("artifact_id", ""),
            )
            if (
                not reference.artifact_id
                or reference.size_bytes > 512 * 1024
                or reference.media_type != _resume_media(reference.filename)
            ):
                raise ValueError()
            data = await self._artifacts.read(reference.artifact_id)
            if (
                not isinstance(data, bytes)
                or len(data) != reference.size_bytes
                or hashlib.sha256(data).hexdigest() != reference.sha256
            ):
                raise ValueError()
            return reference.as_dict(), data
        except Exception:
            raise BossNeedsUser("BOSS_RESUME_MATERIAL_UNVERIFIED") from None

    async def prepare(
        self,
        job: Job | Mapping[str, Any],
        kind: str,
        message: str = "",
        resume_label: str = "",
        *,
        resume: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        target = _target(job)
        material = None
        if kind == "send_resume":
            material, _ = await self._resume(resume)
            if message or (resume_label and resume_label != material["filename"]):
                raise BossNeedsUser("BOSS_RESUME_MESSAGE_SEPARATE")
        elif kind != "message":
            raise BossNeedsUser(
                "BOSS_GREETING_REQUIRES_USER"
                if kind == "greet"
                else "BOSS_RESUME_MATERIAL_UNVERIFIED"
            )
        if material is None and (
            not isinstance(message, str)
            or not message.strip()
            or len(message) > 2000
            or any(ord(char) < 32 and char not in "\r\n\t" for char in message)
            or "\x7f" in message
            or resume_label
        ):
            raise BossNeedsUser("BOSS_MESSAGE_INVALID")
        try:
            observed = await self._call(
                "prepare_upload" if material else "prepare",
                {
                    "target": target,
                    "kind": kind,
                    "text": message,
                    "resume_label": "",
                    "resume": (
                        {
                            key: material[key]
                            for key in (
                                "filename",
                                "sha256",
                                "size_bytes",
                                "media_type",
                            )
                        }
                        if material
                        else None
                    ),
                },
            )
        except BossNeedsUser:
            raise
        except Exception:
            raise BossNeedsUser("BOSS_BROWSER_UNAVAILABLE") from None
        if not isinstance(observed, Mapping) or observed.get("status") != "READY":
            raise BossNeedsUser("BOSS_PAGE_REQUIRES_USER")
        digest = observed.get("snapshot_fingerprint")
        consequences = observed.get("consequences")
        if (
            observed.get("target") != target
            or observed.get("text") != message
            or not isinstance(digest, str)
            or not _SHA.fullmatch(digest)
            or not isinstance(consequences, list)
            or not consequences
            or any(not isinstance(item, str) or not item.strip() for item in consequences)
        ):
            raise BossNeedsUser("BOSS_PREVIEW_INVALID")
        detail = "\n".join(consequences)
        if len(detail) > 4000:
            raise BossNeedsUser("BOSS_PREVIEW_INVALID")
        envelope: dict[str, Any] = {
            "target": target,
            "operation": {"kind": "message", "text": message, "snapshot_fingerprint": digest},
            "consequences": detail,
            "attachments": [],
        }
        if material:
            envelope["operation"].update(kind="send_resume", resume_label=material["filename"])
            envelope["attachments"] = [
                {
                    "artifact_id": material["artifact_id"],
                    "name": material["filename"],
                    "sha256": material["sha256"],
                    "size_bytes": material["size_bytes"],
                }
            ]
        return {"local_action_id": "boss_" + canonical_digest(envelope), **envelope}

    async def review(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, Mapping) or set(arguments) != {
            "local_action_id",
            "target",
            "operation",
            "consequences",
            "attachments",
        }:
            raise BossNeedsUser("BOSS_APPROVAL_BINDING_INVALID")
        operation = arguments.get("operation")
        if not isinstance(operation, Mapping) or set(operation) != (
            {"kind", "text", "snapshot_fingerprint", "resume_label"}
            if operation.get("kind") == "send_resume"
            else {"kind", "text", "snapshot_fingerprint"}
        ):
            raise BossNeedsUser("BOSS_APPROVAL_BINDING_INVALID")
        target = arguments.get("target")
        if not isinstance(target, Mapping) or set(target) != {
            "url",
            "job_id",
            "company",
            "recruiter",
        }:
            raise BossNeedsUser("BOSS_APPROVAL_BINDING_INVALID")
        kind, text = operation.get("kind"), operation.get("text")
        if not isinstance(kind, str) or not isinstance(text, str):
            raise BossNeedsUser("BOSS_APPROVAL_BINDING_INVALID")
        resume = None
        if kind == "send_resume":
            attachments = arguments.get("attachments")
            if (
                not isinstance(attachments, list)
                or len(attachments) != 1
                or not isinstance(attachments[0], Mapping)
            ):
                raise BossNeedsUser("BOSS_APPROVAL_BINDING_INVALID")
            resume = dict(attachments[0])
        current = await self.prepare(
            target, kind, text, operation.get("resume_label", ""), resume=resume
        )
        if current != dict(arguments):
            raise BossNeedsUser("BOSS_APPROVAL_BINDING_CHANGED")
        return current

    async def execute(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        try:
            identifier = arguments.get("local_action_id")
            binding = canonical_digest(dict(arguments))
            if not isinstance(identifier, str):
                raise BossNeedsUser("BOSS_APPROVAL_BINDING_INVALID")
            previous = self._attempts.get(identifier)
            if previous is not None:
                if previous[0] != binding:
                    raise BossNeedsUser("BOSS_APPROVAL_BINDING_CHANGED")
                return copy.deepcopy(previous[1])
            approved = await self.review(arguments)
        except BossNeedsUser as exc:
            return {"status": "NEEDS_USER", "state": "NEEDS_USER", "diagnostic_code": exc.code}
        # Recheck after the readonly await: two concurrent calls must not both dispatch.
        previous = self._attempts.get(identifier)
        if previous is not None:
            return copy.deepcopy(previous[1]) if previous[0] == binding else self._unknown()
        if len(self._attempts) >= 100:
            return {
                "status": "NEEDS_USER",
                "state": "NEEDS_USER",
                "diagnostic_code": "BOSS_WORKER_ACTION_LIMIT",
            }
        self._attempts[identifier] = (binding, self._unknown())
        try:
            if approved["operation"]["kind"] == "send_resume":
                _, data = await self._resume(approved["attachments"][0])
                result = await self._call("execute_upload", {**approved, "_resume_bytes": data})
            else:
                result = await self._call("execute", approved)
        except (Exception, asyncio.CancelledError):
            return self._unknown()
        if not isinstance(result, Mapping):
            return self._unknown()
        if result.get("status") == "NEEDS_USER":
            return {
                "status": "NEEDS_USER",
                "state": "NEEDS_USER",
                "diagnostic_code": "BOSS_PAGE_REQUIRES_USER",
            }
        receipt = result.get("receipt")
        if (
            result.get("status") != "SUCCEEDED"
            or result.get("state") != ("RESUME_SENT" if approved["attachments"] else "MESSAGE_SENT")
            or not isinstance(receipt, Mapping)
            or receipt.get("verified") is not True
            or not isinstance(receipt.get("reference_id"), str)
            or not _RECEIPT.fullmatch(receipt["reference_id"])
        ):
            return self._unknown()
        confirmed = {
            "status": "SUCCEEDED",
            "state": "RESUME_SENT" if approved["attachments"] else "MESSAGE_SENT",
            "receipt": {
                "reference_id": receipt["reference_id"],
                "verified": True,
                "snapshot_fingerprint": approved["operation"]["snapshot_fingerprint"],
            },
        }
        self._attempts[identifier] = (binding, confirmed)
        return copy.deepcopy(confirmed)

    @staticmethod
    def _unknown() -> dict[str, Any]:
        return {
            "status": "UNKNOWN",
            "state": "UNKNOWN",
            "diagnostic_code": "BOSS_RESULT_UNCONFIRMED",
        }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        raise BossNeedsUser("BOSS_CDP_REDIRECT_FORBIDDEN")


def _debugger_url(endpoint: str) -> str:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(endpoint + "/json/version", timeout=5) as response:
        data = response.read(65_537)
    if len(data) > 65_536:
        raise BossNeedsUser("BOSS_CDP_RESPONSE_INVALID")
    version = json.loads(data)
    websocket = version.get("webSocketDebuggerUrl") if isinstance(version, dict) else None
    if not isinstance(websocket, str):
        raise BossNeedsUser("BOSS_CDP_RESPONSE_INVALID")
    expected, parsed = urlsplit(endpoint), urlsplit(websocket)
    if (
        parsed.scheme != "ws"
        or parsed.hostname != expected.hostname
        or parsed.port != expected.port
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or re.fullmatch(r"/devtools/browser(?:/[A-Za-z0-9_-]{1,128})?", parsed.path) is None
    ):
        raise BossNeedsUser("BOSS_CDP_RESPONSE_INVALID")
    return websocket


class PlaywrightBossPort:
    def __init__(self, endpoint: str, *, script_root: Path | None = None) -> None:
        self._endpoint = _endpoint(endpoint)
        payload = Path(__file__).resolve().parents[2] / "browser"
        complete_payload = all(
            (payload / name).is_file() for name in ("boss-dom.js", "boss-action.js")
        )
        self._scripts = script_root or (
            payload if complete_payload else Path(sys.prefix) / "share" / "career-apply" / "browser"
        )

    async def call(self, method: str, argument: Mapping[str, Any]) -> Mapping[str, Any]:
        if method not in {"prepare", "execute", "prepare_upload", "execute_upload"}:
            raise BossNeedsUser("BOSS_OPERATION_UNSUPPORTED")
        # Playwright is an explicitly enabled optional browser runtime, never installed here.
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise BossNeedsUser("BOSS_BROWSER_RUNTIME_UNAVAILABLE") from None
        websocket = await asyncio.to_thread(_debugger_url, self._endpoint)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(websocket, timeout=5000)
            pages = []
            for context in browser.contexts:
                for page in context.pages:
                    parsed = urlsplit(page.url)
                    if (
                        parsed.scheme == "https"
                        and parsed.hostname == "www.zhipin.com"
                        and parsed.port in (None, 443)
                        and parsed.username is None
                        and parsed.password is None
                    ):
                        pages.append(page)
            if len(pages) != 1:
                raise BossNeedsUser("BOSS_PAGE_NOT_UNIQUE")
            page = pages[0]
            await page.evaluate((self._scripts / "boss-dom.js").read_text(encoding="utf-8"))
            if method == "prepare":
                result = await page.evaluate(
                    "(a) => globalThis.CareerBossDOM.prepareAction(a.target,a.kind,a.text,"
                    "a.resume_label)",
                    dict(argument),
                )
            else:
                await page.evaluate((self._scripts / "boss-action.js").read_text(encoding="utf-8"))
                public = dict(argument)
                data = public.pop("_resume_bytes", None)
                if method == "prepare_upload":
                    result = await page.evaluate(
                        "a => CareerBossAction.prepareResume(a.target,a.resume)",
                        public,
                    )
                elif method == "execute_upload":
                    handle = await page.evaluate_handle(
                        "a => CareerBossAction.uploadInput(a)",
                        public,
                    )
                    try:
                        element = handle.as_element()
                        if element is None or not isinstance(data, bytes):
                            return {"status": "NEEDS_USER", "state": "NEEDS_USER"}
                        name = public["attachments"][0]["name"]
                        await element.set_input_files(
                            {
                                "name": name,
                                "mimeType": _resume_media(name),
                                "buffer": data,
                            }
                        )
                        result = await page.evaluate(
                            "a => CareerBossAction.finishUpload(a)",
                            public,
                        )
                    finally:
                        await handle.dispose()
                else:
                    result = await page.evaluate(
                        "(a) => globalThis.CareerBossAction.execute(a)",
                        public,
                    )
            # Exiting Playwright disconnects this client; never browser.close() here.
            if not isinstance(result, Mapping):
                raise BossNeedsUser("BOSS_BROWSER_RESPONSE_INVALID")
            return dict(result)


__all__ = ["BossAdapter", "BossNeedsUser", "BossPagePort", "PlaywrightBossPort"]
