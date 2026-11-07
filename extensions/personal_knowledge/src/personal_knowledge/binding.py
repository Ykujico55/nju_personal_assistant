"""Bind an index to resolved roots without exposing local paths in evidence."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence

from .paths import AuthorizedRoot
from .store import KnowledgeStore

INDEX_BINDING_KEY = "index.binding.v1"
DEFAULT_MAX_FILE_BYTES = 16 * 1024 * 1024


def binding_fingerprint(
    roots: Sequence[AuthorizedRoot], *, max_file_bytes: int, include_hidden: bool
) -> str:
    material = {
        "roots": sorted((root.key, os.path.normcase(str(root.path))) for root in roots),
        "max_file_bytes": max_file_bytes,
        "include_hidden": include_hidden,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()


async def read_index_status(store: KnowledgeStore, fingerprint: str) -> dict[str, str]:
    try:
        meta = await store.get_meta(INDEX_BINDING_KEY)
    except Exception:
        # A missing schema or unavailable data capability cannot prove readiness.
        return {"index_state": "UNAVAILABLE", "index_reason": "INDEX_METADATA_UNAVAILABLE"}
    if not meta or meta.get("schema") != 1:
        return {"index_state": "PENDING", "index_reason": "INDEX_BINDING_MISSING"}
    if meta.get("binding_hash") != fingerprint:
        return {"index_state": "PENDING", "index_reason": "ROOT_BINDING_CHANGED"}
    if meta.get("state") != "READY":
        return {"index_state": "PENDING", "index_reason": "INDEX_SCAN_INCOMPLETE"}
    return {"index_state": "READY", "index_reason": "INDEX_READY"}
