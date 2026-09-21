"""Static transaction adapter documents shipped inside the extension artifact.

The host receives a structural descriptor; the deployment-specific origin comes
from the non-secret host configuration channel, never from page content.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .models import EhallError

ADAPTER_FILES = ("proof.json", "transcript.json")


def load_adapter_documents(package_root: Path) -> tuple[dict[str, Any], ...]:
    directory = package_root / "adapters"
    documents: list[dict[str, Any]] = []
    for name in ADAPTER_FILES:
        path = directory / name
        if not path.is_file():
            raise EhallError("EHALL_ADAPTER_MISSING", f"adapter file missing: {name}")
        try:
            document = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            raise EhallError("EHALL_ADAPTER_INVALID", f"adapter file is invalid: {name}") from exc
        if not isinstance(document, dict):
            raise EhallError("EHALL_ADAPTER_INVALID", f"adapter file is not an object: {name}")
        documents.append(document)
    return tuple(documents)


def build_descriptor(
    document: Mapping[str, Any],
    *,
    extension_id: str,
    extension_version: str,
    origin: str,
    allowed_paths: Sequence[str] = (),
) -> dict[str, Any]:
    paths = list(document.get("allowed_paths", ()))
    for extra in allowed_paths:
        if extra not in paths:
            paths.append(extra)
    actions = []
    for item in document.get("actions", ()):
        action = dict(item)
        if item.get("method") or item.get("target_path"):
            action["target_origin"] = str(item.get("target_origin") or origin)
        actions.append(action)
    descriptor: dict[str, Any] = {
        "extension_id": extension_id,
        "extension_version": extension_version,
        "adapter_id": str(document["adapter_id"]),
        "adapter_version": str(document["adapter_version"]),
        "display_name": str(document.get("display_name", document["adapter_id"])),
        "allowed_origins": [origin],
        "allowed_paths": paths,
        "declared_risk": str(document.get("declared_risk", "EXTERNAL_WRITE")),
        "transaction_ids": list(document.get("transaction_ids", ())),
        "fields": list(document.get("fields", ())),
        "actions": actions,
        "login_paths": list(document.get("login_paths", ())),
        "forbidden_terms": list(document.get("forbidden_terms", ())),
        "discovery_path": str(document.get("discovery_path", "")),
        "tracking_path": str(document.get("tracking_path", "")),
        "receipt_locator": str(document.get("receipt_locator", "")),
        "receipt_pattern": str(document.get("receipt_pattern", "")),
        "allowed_page_fingerprints": list(
            document.get("allowed_page_fingerprints", ())
        ),
        "consequences": str(document.get("consequences", "")),
    }
    return descriptor


def receipt_reference(document: Mapping[str, Any], excerpt: str) -> str:
    pattern = str(document.get("receipt_pattern", ""))
    if pattern:
        try:
            found = re.search(pattern, excerpt)
        except re.error:
            found = None
        if found is not None:
            return found.group(0)
    return " ".join(excerpt.split())[:200]


def tracking_query(document: Mapping[str, Any]) -> str:
    query = str(document.get("tracking_query", "")).strip()
    if query:
        return query
    locator = str(document.get("receipt_locator", ""))
    return locator[5:] if locator.startswith("text:") else ""


__all__ = [
    "ADAPTER_FILES",
    "build_descriptor",
    "load_adapter_documents",
    "receipt_reference",
    "tracking_query",
]
