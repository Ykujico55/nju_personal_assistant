"""File-backed store for generic, non-secret extension configuration.

Configuration is small, agent-owned JSON: it lives under the managed extension
root (``PA_EXTENSION_ROOT/config``) instead of a database so both development
and production use the same non-secret path.  Credentials never belong here;
they are referenced as ``SecretHandle`` values and resolved by host brokers.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from personal_assistant.core.extensions.config import (
    MAX_CONFIG_BYTES,
    MAX_CONFIG_SCHEMA_BYTES,
    ExtensionConfigError,
    validate_extension_config_schema,
)
from personal_assistant.core.extensions.manifest import ExtensionManifest

_SAFE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$")


def load_extension_config_schema(
    manifest: ExtensionManifest,
) -> dict[str, Any] | None:
    """Read and validate one installed manifest schema through a bounded snapshot."""

    reference = manifest.config_schema
    if not reference:
        return None
    try:
        root = Path(manifest.root).resolve(strict=True)
        path = (root / reference).resolve(strict=True)
        path.relative_to(root)
        if not path.is_file() or path.stat().st_size > MAX_CONFIG_SCHEMA_BYTES:
            raise ExtensionConfigError("extension config schema is unavailable or too large")
        with path.open("rb") as stream:
            raw = stream.read(MAX_CONFIG_SCHEMA_BYTES + 1)
        if len(raw) > MAX_CONFIG_SCHEMA_BYTES:
            raise ExtensionConfigError("extension config schema is too large")
        decoded = json.loads(raw.decode("utf-8"))
    except ExtensionConfigError:
        raise
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ExtensionConfigError("extension config schema is unreadable") from exc
    return validate_extension_config_schema(decoded)


class FileExtensionConfigStore:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._save_locks: dict[str, asyncio.Lock] = {}

    async def get(self, extension_id: str) -> Mapping[str, Any]:
        path = self._path(extension_id)
        return await asyncio.to_thread(self._read, path)

    async def save(self, extension_id: str, config: Mapping[str, Any]) -> None:
        path = self._path(extension_id)
        try:
            encoded = json.dumps(
                dict(config),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ExtensionConfigError("config must be strict JSON") from exc
        if len(encoded.encode("utf-8")) > MAX_CONFIG_BYTES:
            raise ExtensionConfigError("config exceeds the size limit")
        lock = self._save_locks.setdefault(extension_id, asyncio.Lock())
        async with lock:
            await asyncio.to_thread(self._write, path, encoded)

    def _path(self, extension_id: str) -> Path:
        if not isinstance(extension_id, str) or _SAFE_ID_RE.fullmatch(extension_id) is None:
            raise ExtensionConfigError("invalid extension id for configuration")
        return self._root / f"{extension_id}.json"

    @staticmethod
    def _read(path: Path) -> Mapping[str, Any]:
        if not path.is_file():
            return {}
        try:
            with path.open("rb") as stream:
                raw = stream.read(MAX_CONFIG_BYTES + 1)
            if len(raw) > MAX_CONFIG_BYTES:
                raise ExtensionConfigError("stored extension config exceeds the size limit")
            data = json.loads(
                raw.decode("utf-8"),
                parse_constant=lambda _value: (_ for _ in ()).throw(
                    ValueError("non-finite JSON number")
                ),
            )
        except ExtensionConfigError:
            raise
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise ExtensionConfigError("stored extension config is unreadable") from exc
        if not isinstance(data, dict):
            raise ExtensionConfigError("stored extension config must be a JSON object")
        return data

    @staticmethod
    def _write(path: Path, encoded: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


__all__ = ["FileExtensionConfigStore", "load_extension_config_schema"]
