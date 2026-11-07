"""Runtime acknowledgements and status reads share the configuration file lock."""

from __future__ import annotations

import asyncio
import errno
import os
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, BinaryIO

import pytest

from personal_assistant.core.extensions.config import MAX_CONFIG_BYTES, ExtensionConfigError
from personal_assistant.infrastructure.extensions import config_store
from personal_assistant.infrastructure.extensions.config_store import FileExtensionConfigStore

EXTENSION_ID = "example.echo"
INSTANCE_ID = "a" * 32


async def _register_runtime(store: FileExtensionConfigStore) -> Mapping[str, Any]:
    await store.save(EXTENSION_ID, {"prefix": "old-public-fixture"})
    saved = await store.status(EXTENSION_ID)
    target = await store.apply(EXTENSION_ID, saved["saved_digest"])
    await store.report_runtime(
        EXTENSION_ID,
        INSTANCE_ID,
        loaded_digest=target["desired_digest"],
        generation=target["generation"],
        state="RUNNING",
    )
    return target


async def test_runtime_publication_and_status_read_cannot_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer, reader = FileExtensionConfigStore(tmp_path), FileExtensionConfigStore(tmp_path)
    publication_started = threading.Event()
    release_publication = threading.Event()
    reader_at_boundary = threading.Event()
    overlapping_read = threading.Event()
    tasks: list[asyncio.Task[Any]] = []
    results: list[Any] = []
    runtime_path = tmp_path / ".runtime" / EXTENSION_ID / f"{INSTANCE_ID}.json"
    mutation_lock_path = tmp_path / ".locks" / f"{EXTENSION_ID}.json.lock"
    real_replace, real_try_lock, real_read = os.replace, config_store._try_lock, reader._read

    def blocked_replace(source: Any, destination: Any) -> None:
        if Path(destination) == runtime_path:
            publication_started.set()
            if not release_publication.wait(5):
                raise TimeoutError("test did not release runtime publication")
        real_replace(source, destination)

    def observed_lock(stream: BinaryIO) -> bool:
        acquired = real_try_lock(stream)
        if (
            Path(stream.name) == mutation_lock_path
            and publication_started.is_set()
            and not release_publication.is_set()
            and not acquired
        ):
            # The real OS lock, not a sleep, proves that the reader has reached
            # the writer's publication boundary without entering the JSON read.
            reader_at_boundary.set()
        return acquired

    def observed_read(
        path: Path, *, max_bytes: int = MAX_CONFIG_BYTES
    ) -> Mapping[str, Any]:
        if path == runtime_path and not release_publication.is_set():
            overlapping_read.set()
            reader_at_boundary.set()
        return real_read(path, max_bytes=max_bytes)

    try:
        await _register_runtime(writer)
        await writer.save(EXTENSION_ID, {"prefix": "new-public-fixture"})
        saved = await writer.status(EXTENSION_ID)
        target = await writer.apply(EXTENSION_ID, saved["saved_digest"])
        monkeypatch.setattr(os, "replace", blocked_replace)
        monkeypatch.setattr(config_store, "_try_lock", observed_lock)
        monkeypatch.setattr(reader, "_read", observed_read)
        tasks.append(
            asyncio.create_task(
                writer.report_runtime(
                    EXTENSION_ID,
                    INSTANCE_ID,
                    loaded_digest=target["desired_digest"],
                    generation=target["generation"],
                    state="RUNNING",
                )
            )
        )
        assert await asyncio.to_thread(publication_started.wait, 5), "publisher never started"
        tasks.append(asyncio.create_task(reader.status(EXTENSION_ID)))
        assert await asyncio.to_thread(reader_at_boundary.wait, 5), "reader never reached boundary"
    finally:
        release_publication.set()
        # Retrieve every task result even when a barrier/assertion fails. Never
        # leave a to_thread publisher behind while tmp_path is being cleaned up.
        results = list(await asyncio.gather(*tasks, return_exceptions=True))
        await writer.release_runtime(INSTANCE_ID)
    for result in results:
        if isinstance(result, BaseException):
            raise result
    assert not overlapping_read.is_set(), "status read overlapped runtime JSON publication"
    status = results[1]
    assert status["state"] == "APPLIED"
    assert len(status["runtimes"]) == 1
    assert status["runtimes"][0]["generation"] == target["generation"]
    assert status["runtimes"][0]["loaded_digest"] == target["desired_digest"]


async def test_runtime_status_preserves_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer, reader = FileExtensionConfigStore(tmp_path), FileExtensionConfigStore(tmp_path)
    runtime_path = tmp_path / ".runtime" / EXTENSION_ID / f"{INSTANCE_ID}.json"
    real_open = Path.open

    def denied_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == runtime_path:
            raise PermissionError(errno.EACCES, "fixture access denied", str(path))
        return real_open(path, *args, **kwargs)

    try:
        await _register_runtime(writer)
        monkeypatch.setattr(Path, "open", denied_open)
        with pytest.raises(ExtensionConfigError, match="unreadable") as raised:
            await reader.status(EXTENSION_ID)
        assert isinstance(raised.value.__cause__, PermissionError)
        assert raised.value.__cause__.errno == errno.EACCES
    finally:
        await writer.release_runtime(INSTANCE_ID)


@pytest.mark.parametrize("stored", ["{broken-json", "[]"])
async def test_runtime_status_preserves_invalid_json_error(tmp_path: Path, stored: str) -> None:
    writer, reader = FileExtensionConfigStore(tmp_path), FileExtensionConfigStore(tmp_path)
    runtime_path = tmp_path / ".runtime" / EXTENSION_ID / f"{INSTANCE_ID}.json"
    try:
        await _register_runtime(writer)
        runtime_path.write_text(stored, encoding="utf-8")
        with pytest.raises(ExtensionConfigError, match="unreadable|must be a JSON object"):
            await reader.status(EXTENSION_ID)
    finally:
        await writer.release_runtime(INSTANCE_ID)
