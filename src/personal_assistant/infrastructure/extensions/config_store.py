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
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

from personal_assistant.core.extensions.config import (
    MAX_CONFIG_BYTES,
    MAX_CONFIG_SCHEMA_BYTES,
    ExtensionConfigError,
    config_digest,
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
        self._lease_guard = threading.Lock()
        self._leases: dict[str, BinaryIO] = {}

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
            await asyncio.to_thread(self._save_draft, extension_id, path, encoded)

    def _save_draft(self, extension_id: str, path: Path, encoded: str) -> None:
        with self._mutation_lock(extension_id):
            applied = self._applied_path(extension_id)
            if not applied.exists():
                self._write(applied, json.dumps(self._active(extension_id), ensure_ascii=False))
            self._write(path, encoded)

    async def get_applied(self, extension_id: str) -> Mapping[str, Any]:
        return await asyncio.to_thread(self._active, extension_id)

    def _applied_path(self, extension_id: str) -> Path:
        return self._root / ".applied" / self._path(extension_id).name

    def _active(self, extension_id: str) -> dict[str, Any]:
        path = self._applied_path(extension_id)
        if not path.exists():
            config = dict(self._read(self._path(extension_id)))
            digest = config_digest(config)
            return {"config": config, "digest": digest, "generation": "legacy:" + digest[7:]}
        value = dict(self._read(path, max_bytes=MAX_CONFIG_BYTES + 2048))
        stored_config = value.get("config")
        stored_digest, generation = value.get("digest"), value.get("generation")
        if (
            not isinstance(stored_config, dict)
            or stored_digest != config_digest(stored_config)
            or not isinstance(generation, str)
            or not 1 <= len(generation) <= 128
        ):
            raise ExtensionConfigError("applied configuration is invalid")
        return {"config": stored_config, "digest": stored_digest, "generation": generation}

    @contextmanager
    def _mutation_lock(self, extension_id: str) -> Iterator[None]:
        path = self._root / ".locks" / (self._path(extension_id).name + ".lock")
        stream = _open_lock(path)
        try:
            deadline = time.monotonic() + 10
            while not _try_lock(stream):
                if time.monotonic() >= deadline:
                    raise ExtensionConfigError("configuration is busy")
                time.sleep(0.02)
            yield
        finally:
            stream.close()

    async def apply(self, extension_id: str, expected_digest: str) -> Mapping[str, Any]:
        await asyncio.to_thread(self._apply, extension_id, expected_digest)
        return await self.status(extension_id)

    def _apply(self, extension_id: str, expected_digest: str) -> None:
        with self._mutation_lock(extension_id):
            config = dict(self._read(self._path(extension_id)))
            digest = config_digest(config)
            if digest != expected_digest:
                raise ExtensionConfigError("CONFIG_REVISION_CONFLICT")
            active = self._active(extension_id)
            if active["digest"] != digest:
                active = {"config": config, "digest": digest, "generation": uuid.uuid4().hex}
                self._write(
                    self._applied_path(extension_id), json.dumps(active, ensure_ascii=False)
                )

    async def status(self, extension_id: str) -> Mapping[str, Any]:
        return await asyncio.to_thread(self._status, extension_id)

    def _status(self, extension_id: str) -> dict[str, Any]:
        with self._mutation_lock(extension_id):
            saved = config_digest(self._read(self._path(extension_id)))
            target = self._active(extension_id)
            runtimes = []
            for path in sorted((self._root / ".runtime" / extension_id).glob("*.json")):
                instance_id = path.stem
                if re.fullmatch(r"[0-9a-f]{32}", instance_id) is None:
                    continue
                lease = _open_lock(self._root / ".runtime" / (instance_id + ".lock"))
                try:
                    if _try_lock(lease):
                        continue  # OS released the owner's lease; it is not a live replica.
                finally:
                    lease.close()
                value = self._read(path, max_bytes=4096)
                if value.get("state") == "STOPPED":
                    continue
                runtimes.append(
                    {
                        "instance_id": instance_id,
                        "loaded_digest": value.get("loaded_digest"),
                        "generation": value.get("generation"),
                        "state": value.get("state"),
                        "error_code": value.get("error_code"),
                    }
                )
            state = "PENDING"
            if saved == target["digest"] and runtimes:
                state = "APPLYING"
                if any(
                    r["generation"] == target["generation"] and r["state"] == "FAILED"
                    for r in runtimes
                ):
                    state = "FAILED"
                elif all(
                    r["generation"] == target["generation"]
                    and r["loaded_digest"] == target["digest"]
                    and r["state"] == "RUNNING"
                    for r in runtimes
                ):
                    state = "APPLIED"
            return {
                "saved_digest": saved,
                "desired_digest": target["digest"],
                "generation": target["generation"],
                "state": state,
                "runtimes": runtimes,
            }

    async def report_runtime(
        self,
        extension_id: str,
        instance_id: str,
        *,
        loaded_digest: str | None,
        generation: str | None,
        state: str,
        error_code: str | None = None,
    ) -> None:
        self._path(extension_id)
        if (
            re.fullmatch(r"[0-9a-f]{32}", instance_id) is None
            or state not in {"RUNNING", "APPLYING", "FAILED", "STOPPED"}
            or error_code not in {None, "CONFIG_APPLY_FAILED", "CONFIG_UNAVAILABLE"}
            or (
                loaded_digest is not None
                and re.fullmatch(r"sha256:[0-9a-f]{64}", loaded_digest) is None
            )
            or (
                generation is not None
                and re.fullmatch(r"[A-Za-z0-9:._-]{1,128}", generation) is None
            )
        ):
            raise ExtensionConfigError("invalid runtime configuration report")
        await asyncio.to_thread(
            self._report,
            extension_id,
            instance_id,
            {
                "loaded_digest": loaded_digest,
                "generation": generation,
                "state": state,
                "error_code": error_code,
            },
        )

    def _report(self, extension_id: str, instance_id: str, value: Mapping[str, Any]) -> None:
        # Status readers must not open the runtime file during replacement on Windows.
        with self._mutation_lock(extension_id), self._lease_guard:
            if instance_id not in self._leases:
                stream = _open_lock(self._root / ".runtime" / (instance_id + ".lock"))
                if not _try_lock(stream):
                    stream.close()
                    raise ExtensionConfigError("runtime instance is already registered")
                self._leases[instance_id] = stream
            self._write(
                self._root / ".runtime" / extension_id / (instance_id + ".json"),
                json.dumps(value, ensure_ascii=False),
            )

    async def release_runtime(self, instance_id: str) -> None:
        await asyncio.to_thread(self._release, instance_id)

    def _release(self, instance_id: str) -> None:
        with self._lease_guard:
            stream = self._leases.pop(instance_id, None)
            if stream is not None:
                stream.close()

    def _path(self, extension_id: str) -> Path:
        if not isinstance(extension_id, str) or _SAFE_ID_RE.fullmatch(extension_id) is None:
            raise ExtensionConfigError("invalid extension id for configuration")
        return self._root / f"{extension_id}.json"

    @staticmethod
    def _read(path: Path, *, max_bytes: int = MAX_CONFIG_BYTES) -> Mapping[str, Any]:
        if not path.is_file():
            return {}
        try:
            with path.open("rb") as stream:
                raw = stream.read(max_bytes + 1)
            if len(raw) > max_bytes:
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


def _open_lock(path: Path) -> BinaryIO:
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    if path.stat().st_size == 0:
        stream.write(b"0")
        stream.flush()
    stream.seek(0)
    return stream


def _try_lock(stream: BinaryIO) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
        return True
    except OSError as exc:
        if exc.errno in {11, 13, 35, 36}:
            return False
        raise


__all__ = ["FileExtensionConfigStore", "load_extension_config_schema"]
