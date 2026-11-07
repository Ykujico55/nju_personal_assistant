"""Bounded metadata overview of configured roots, without reading file bodies."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from personal_assistant_sdk import Evidence, RuntimeContext

from .paths import (
    DEFAULT_IGNORED_DIRECTORY_NAMES,
    PathSafetyError,
    RootConfigurationError,
    _is_junction,
    media_type_for,
    parse_root_specs,
    safe_join,
)

_FILE_REFERENCE_NOTE = (
    "knowledge-workspace://instance/overview 是目录清单的来源，"
    "不能作为 knowledge.read 的 resource；"
    "读取文件应使用 files[].source_uri，或唯一且含扩展名的精确文件名。"
)


def workspace_snapshot(
    config: Mapping[str, Any],
    *,
    root_keys: Sequence[str] | None = None,
    max_files: int | None = None,
    max_entries: int = 1000,
    max_depth: int = 8,
    max_seconds: float = 2.0,
) -> dict[str, Any]:
    if (
        (max_files is not None and (type(max_files) is not int or max_files < 1))
        or not 1 <= max_entries <= 5000
        or not 0 <= max_depth <= 16
        or not 0 < max_seconds <= 5
    ):
        raise ValueError("invalid workspace bounds")
    result: dict[str, Any] = {
        "kind": "workspace_metadata",
        "configured": False,
        "configuration_state": "NOT_CONFIGURED",
        "roots": [],
        "files": [],
        "complete": True,
        "truncated": False,
        "truncation_reasons": [],
        "index_state": "UNKNOWN",
        "note": (
            "目录元数据概览；未读取正文，未查询或重建索引，不能据此判断已建立索引。"
            + _FILE_REFERENCE_NOTE
        ),
    }
    try:
        specs = parse_root_specs(config.get("roots", []))
        if not isinstance(config.get("include_hidden", False), bool):
            raise RootConfigurationError("config_invalid", "invalid hidden setting")
    except RootConfigurationError as exc:
        if exc.code != "roots_empty":
            result["configuration_state"] = "INVALID"
        result["complete"] = False
        return result
    result["configured"] = True
    result["configuration_state"] = "CONFIGURED"
    selected = set(root_keys) if root_keys else None
    deadline = time.monotonic() + max_seconds
    inspected = 0

    def mark_truncated(reason: str) -> None:
        result["complete"], result["truncated"] = False, True
        if reason not in result["truncation_reasons"]:
            result["truncation_reasons"].append(reason)

    def scan_limit_reached() -> bool:
        if max_files is not None and len(result["files"]) >= max_files:
            mark_truncated("FILE_LIMIT")
        elif inspected >= max_entries:
            mark_truncated("ENTRY_SCAN_LIMIT")
        elif time.monotonic() >= deadline:
            mark_truncated("SCAN_TIMEOUT")
        else:
            return False
        return True

    for spec in specs:
        if selected is not None and spec.key not in selected:
            continue
        root_view: dict[str, Any] = {
            "root_key": spec.key,
            "label": spec.label,
            "configured": True,
            "state": "UNAVAILABLE",
        }
        result["roots"].append(root_view)
        raw = Path(spec.path).expanduser()
        try:
            if raw.is_symlink() or _is_junction(raw):
                raise OSError("root links are not followed")
            root = raw.resolve(strict=True)
            if not root.is_dir():
                raise OSError("root is not a directory")
        except (OSError, RuntimeError):
            result["complete"] = False
            continue
        root_view["state"] = "AVAILABLE"
        stack: list[tuple[Path, str, int]] = [(root, "", 0)]
        while stack:
            if scan_limit_reached():
                break
            directory, prefix, depth = stack.pop()
            try:
                if (
                    root.resolve(strict=True) != root
                    or directory.is_symlink()
                    or _is_junction(directory)
                ):
                    raise OSError("directory identity changed")
                if directory != root and safe_join(root, prefix.rstrip("/")) != directory:
                    raise OSError("directory moved outside root")
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if scan_limit_reached():
                            break
                        inspected += 1
                        if not config.get("include_hidden", False) and entry.name.startswith("."):
                            continue
                        relative = prefix + entry.name
                        if len(relative) > 1024:
                            mark_truncated("PATH_LENGTH_LIMIT")
                            continue
                        candidate = Path(entry.path)
                        if entry.is_symlink() or _is_junction(candidate):
                            continue
                        # Resolve every returned entry against the authorized root,
                        # including after a concurrent directory replacement.
                        if (
                            root.resolve(strict=True) != root
                            or safe_join(root, relative) != candidate
                        ):
                            raise OSError("entry identity changed")
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name in DEFAULT_IGNORED_DIRECTORY_NAMES:
                                continue
                            if depth >= max_depth:
                                mark_truncated("DEPTH_LIMIT")
                                continue
                            stack.append((candidate, relative + "/", depth + 1))
                        elif entry.is_file(follow_symlinks=False):
                            stat = entry.stat(follow_symlinks=False)
                            result["files"].append(
                                {
                                    "root_key": spec.key,
                                    "relative_path": relative,
                                    "source_uri": (
                                        f"knowledge://{quote(spec.key, safe='')}/"
                                        f"{quote(relative, safe='/')}"
                                    ),
                                    "size_bytes": stat.st_size,
                                    "media_type": media_type_for(relative)
                                    or "application/octet-stream",
                                    "index_state": "UNKNOWN",
                                }
                            )
            except (OSError, RuntimeError, PathSafetyError):
                root_view["state"] = "PARTIALLY_UNAVAILABLE"
                result["complete"] = False
    result["files"].sort(key=lambda item: (item["root_key"], item["relative_path"]))
    # Keep all safely observed metadata. Traversal budgets above bound work;
    # output size must not silently discard entries after a completed scan.
    return result


def workspace_evidence(
    runtime: RuntimeContext, *, root_keys: Sequence[str] | None = None,
    index_status: Mapping[str, str] | None = None,
) -> Evidence:
    snapshot = workspace_snapshot(runtime.non_secret_config, root_keys=root_keys)
    if index_status is not None:
        snapshot.update(index_status)
        snapshot["note"] = (
            "目录元数据概览；未读取正文。索引状态仅表示当前目录绑定的最近扫描结果，"
            "单个文件是否仍有效须检索时重新校验；PENDING 表示需要重新建立索引。"
            + _FILE_REFERENCE_NOTE
        )
    text = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    # This hash identifies the metadata snapshot, never any file's body.
    uri = "knowledge-workspace://instance/overview"
    return Evidence(
        text=text,
        source_id="knowledge.workspace",
        content_hash=digest,
        source_uri=uri,
        metadata={
            "status": "CURRENT",
            "snapshot_kind": "workspace_metadata",
            "source_uri": uri,
            "source_version": digest,
            "content_hash": digest,
            "locator": {"kind": "workspace_metadata"},
            "extension_id": runtime.extension_id,
            "extension_version": runtime.extension_version,
            "sensitivity": "SENSITIVE",
            "trust": "USER_SOURCE",
            "observed_at": datetime.now(UTC).isoformat(),
        },
    )
