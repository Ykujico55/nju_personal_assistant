"""Read one authorized document without consulting or changing its index."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from personal_assistant_sdk import Evidence, RuntimeContext, to_jsonable
from personal_assistant_sdk.rpc import RpcResponse, encode_frame

from .binding import DEFAULT_MAX_FILE_BYTES
from .extractors import extract_document
from .models import ExtractionError
from .paths import (
    DEFAULT_IGNORED_DIRECTORY_NAMES,
    RootConfigurationError,
    RootSpec,
    _is_junction,
    media_type_for,
    parse_root_specs,
    safe_join,
)
from .workspace import workspace_snapshot

# Default worker results use a 4 MiB frame. Reserve room for RPC and evidence metadata.
MAX_DOCUMENT_RESPONSE_BYTES = 3 * 1024 * 1024
MAX_CONTEXT_RESPONSE_BYTES = 4 * 1024 * 1024 - 16 * 1024
_IGNORED_NAMES = frozenset(os.path.normcase(name) for name in DEFAULT_IGNORED_DIRECTORY_NAMES)


class _ReadFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


def _result(status: str, **values: Any) -> dict[str, Any]:
    return {"status": status, "complete": status == "READ", "text": "", **values}


def _uri(root_key: str, relative: str) -> str:
    return f"knowledge://{root_key}/{quote(relative, safe='/')}"


def _parse_resource(resource: str) -> tuple[str | None, str]:
    if (not isinstance(resource, str) or not resource or len(resource) > 2000
            or any(ord(c) < 32 or ord(c) == 127 for c in resource)):
        raise _ReadFailure("RESOURCE_INVALID")
    if resource.startswith("knowledge://"):
        parsed = urlsplit(resource)
        if (not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", parsed.netloc)
                or "?" in resource or "#" in resource or not parsed.path.startswith("/")
                or re.search(r"%(?![A-Fa-f0-9]{2})", parsed.path)):
            raise _ReadFailure("RESOURCE_INVALID")
        try:
            relative = unquote(parsed.path[1:], errors="strict")
        except UnicodeError:
            raise _ReadFailure("RESOURCE_INVALID") from None
        root_key: str | None = parsed.netloc
    else:
        root_key, relative = None, resource
        if "/" in relative or "\\" in relative or ":" in relative:
            raise _ReadFailure("RESOURCE_INVALID")
    parts = relative.split("/")
    if (not relative or len(relative) > 1024
            or any(not part or part in {".", ".."} or ":" in part or "\\" in part
                   or any(ord(c) < 32 for c in part) for part in parts)):
        raise _ReadFailure("RESOURCE_INVALID")
    return root_key, relative


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
    # Windows path.stat and CRT fstat disagree on ctime (creation vs change time).
    # Compare file identity, length and write time consistently across both APIs.
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _root(spec: RootSpec) -> Path:
    raw = Path(os.path.abspath(Path(spec.path).expanduser()))
    try:
        # Check the un-resolved chain: resolving first would hide internal links.
        for node in (*reversed(raw.parents), raw):
            if node.is_symlink() or _is_junction(node):
                raise _ReadFailure("ROOT_UNAVAILABLE")
        resolved = raw.resolve(strict=True)
        if not resolved.is_dir():
            raise _ReadFailure("ROOT_UNAVAILABLE")
        return resolved
    except (OSError, RuntimeError):
        raise _ReadFailure("ROOT_UNAVAILABLE") from None


def _checked_path(spec: RootSpec, relative: str, include_hidden: bool) -> Path:
    root = _root(spec)
    candidate = root
    parts = relative.split("/")
    try:
        for index, part in enumerate(parts):
            if ((not include_hidden and part.startswith("."))
                    or (index < len(parts) - 1 and os.path.normcase(part) in _IGNORED_NAMES)):
                raise _ReadFailure("RESOURCE_NOT_ALLOWED")
            candidate = candidate / part
            if candidate.is_symlink() or _is_junction(candidate):
                raise _ReadFailure("RESOURCE_NOT_ALLOWED")
        if safe_join(root, relative) != candidate:
            raise _ReadFailure("RESOURCE_NOT_ALLOWED")
        if not candidate.is_file():
            raise _ReadFailure("NOT_FOUND")
        return candidate
    except (OSError, RuntimeError, ValueError):
        raise _ReadFailure("RESOURCE_NOT_ALLOWED") from None


def _read_current(
    spec: RootSpec, relative: str, *, include_hidden: bool, max_bytes: int,
) -> tuple[bytes, tuple[int, int, int, int]]:
    path = _checked_path(spec, relative, include_hidden)
    before = path.stat()
    if before.st_size > max_bytes:
        raise _ReadFailure("FILE_TOO_LARGE")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(before):
            raise _ReadFailure("FILE_CHANGED")
        data = stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise _ReadFailure("FILE_TOO_LARGE")
        if _identity(os.fstat(stream.fileno())) != _identity(opened):
            raise _ReadFailure("FILE_CHANGED")
    if _identity(_checked_path(spec, relative, include_hidden).stat()) != _identity(opened):
        raise _ReadFailure("FILE_CHANGED")
    return data, _identity(opened)


def read_document(runtime: RuntimeContext, resource: str) -> dict[str, Any]:
    """Return full extracted text or an explicit non-content result, never truncate."""
    try:
        root_key, relative = _parse_resource(resource)
        config = runtime.non_secret_config
        specs = parse_root_specs(config.get("roots", []))
        include_hidden = config.get("include_hidden", False)
        maximum = config.get("max_file_bytes", DEFAULT_MAX_FILE_BYTES)
        if (not isinstance(include_hidden, bool) or type(maximum) is not int
                or not 1024 <= maximum <= 100 * 1024 * 1024):
            raise _ReadFailure("CONFIGURATION_INVALID")
        if root_key is None:
            for configured in specs:
                _root(configured)
            snapshot = workspace_snapshot(config)
            # Also revalidate the un-resolved roots before publishing any candidate names.
            for configured in specs:
                _root(configured)
            if any(item["state"] != "AVAILABLE" for item in snapshot["roots"]):
                raise _ReadFailure("ROOT_UNAVAILABLE")
            candidates = [item for item in snapshot["files"]
                          if Path(item["relative_path"]).name == relative]
            choices = [{"source_uri": _uri(item["root_key"], item["relative_path"]),
                        "relative_path": item["relative_path"], "root_key": item["root_key"]}
                       for item in candidates]
            if not snapshot["complete"]:
                return _result("LOOKUP_INCOMPLETE", candidates=choices,
                               reasons=snapshot["truncation_reasons"])
            if len(candidates) > 1:
                return _result("AMBIGUOUS", candidates=choices)
            if not candidates:
                raise _ReadFailure("NOT_FOUND")
            root_key, relative = candidates[0]["root_key"], candidates[0]["relative_path"]
        spec = next((item for item in specs if item.key == root_key), None)
        if spec is None:
            raise _ReadFailure("RESOURCE_NOT_ALLOWED")
        source_uri = _uri(spec.key, relative)
        if len(source_uri) > 2000:
            raise _ReadFailure("RESOURCE_INVALID")
        # Validate authorization even for unsupported extensions before reporting format.
        _checked_path(spec, relative, include_hidden)
        media_type = media_type_for(relative)
        if media_type is None:
            return _result("UNSUPPORTED_FORMAT", source_uri=source_uri)
        data, identity = _read_current(
            spec, relative, include_hidden=include_hidden, max_bytes=maximum,
        )
        document = extract_document(data, media_type, path=relative, max_file_bytes=maximum)
        if _identity(_checked_path(spec, relative, include_hidden).stat()) != identity:
            raise _ReadFailure("FILE_CHANGED")
        if document.diagnostics:
            return _result("EXTRACTION_INCOMPLETE", source_uri=source_uri,
                           reasons=sorted({item.code.value for item in document.diagnostics}))
        locator = (
            {"kind": "page_range", "start": 1, "end": len(document.text.split("\f"))}
            if media_type == "application/pdf" else
            {"kind": "line_range", "start": 1, "end": len(document.text.split("\n"))}
        )
        output = _result(
            "READ", text=document.text, source_uri=source_uri, media_type=media_type,
            content_hash=hashlib.sha256(data).hexdigest(), locator=locator,
        )
        encoded = json.dumps(output, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_DOCUMENT_RESPONSE_BYTES:
            return _result("DOCUMENT_TOO_LARGE", source_uri=source_uri)
        return output
    except _ReadFailure as error:
        return _result(error.code)
    except RootConfigurationError as error:
        return _result("NOT_CONFIGURED" if error.code == "roots_empty" else "CONFIGURATION_INVALID")
    except ExtractionError as error:
        return _result(error.code.value)
    except (OSError, RuntimeError, ValueError):
        return _result("READ_FAILED")


def status_evidence(
    runtime: RuntimeContext, output: Mapping[str, Any], *, source_id: str,
) -> Evidence:
    text = json.dumps(dict(output), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    uri = f"knowledge-status://instance/{source_id}"
    return Evidence(
        text=text, source_id=source_id, source_uri=uri, content_hash=digest,
        metadata={
            "status": "CURRENT", "source_uri": uri, "source_version": digest,
            "content_hash": digest, "locator": {"kind": "operation_status"},
            "extension_id": runtime.extension_id, "extension_version": runtime.extension_version,
            "sensitivity": "SENSITIVE", "trust": "USER_SOURCE",
            "observed_at": datetime.now(UTC).isoformat(),
        },
    )


def document_evidence(runtime: RuntimeContext, resource: str) -> Evidence:
    output = read_document(runtime, resource)
    if output["status"] != "READ":
        return status_evidence(runtime, output, source_id="knowledge.document")
    uri, digest = output["source_uri"], output["content_hash"]
    return Evidence(
        text=output["text"],
        source_id="knowledge.document:" + hashlib.sha256(uri.encode()).hexdigest(),
        source_uri=uri, content_hash=digest,
        metadata={
            "status": "CURRENT", "source_uri": uri, "source_version": digest,
            "content_hash": digest, "locator": output["locator"], "complete": True,
            "extension_id": runtime.extension_id, "extension_version": runtime.extension_version,
            "sensitivity": "SENSITIVE", "trust": "USER_SOURCE",
            "observed_at": datetime.now(UTC).isoformat(),
        },
    )


def bounded_context(runtime: RuntimeContext, evidence: Sequence[Evidence]) -> tuple[Evidence, ...]:
    """Use the real SDK codec, failing explicitly instead of slicing any body."""
    result = tuple(evidence)
    encoded = encode_frame(RpcResponse(id="context-budget", result=to_jsonable(result)))
    if len(encoded) <= MAX_CONTEXT_RESPONSE_BYTES:
        return result
    return (status_evidence(
        runtime,
        {"kind": "knowledge_context_status", "status": "DOCUMENT_TOO_LARGE",
         "complete": False, "text": "", "reason": "CONTEXT_RESPONSE_LIMIT"},
        source_id="knowledge.context-status",
    ),)
