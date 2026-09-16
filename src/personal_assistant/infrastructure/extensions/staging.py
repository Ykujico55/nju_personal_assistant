"""Copy or unpack an extension artifact into a controlled staging directory.

Staging is deliberately data-only: files are copied, archives are extracted with
path-traversal protection, symlinks are rejected, and nothing is imported, built,
installed or executed.  The staged tree is the artifact that later gets hashed,
confirmed and installed.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import shutil
import zipfile
from pathlib import Path

from personal_assistant.core.extensions.errors import (
    ExtensionError,
    ManifestValidationError,
)
from personal_assistant.core.extensions.lifecycle import StagedArtifact
from personal_assistant.core.extensions.manifest import (
    IGNORED_ARTIFACT_PARTS,
    compute_artifact_hash,
)

_REMOTE_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:")
_ARCHIVE_SUFFIXES = {".zip", ".whl"}
_DEFAULT_MAX_EXTRACT_BYTES = 256 * 1024 * 1024


class LocalArtifactStager:
    def __init__(
        self,
        root: Path,
        *,
        max_extract_bytes: int = _DEFAULT_MAX_EXTRACT_BYTES,
    ) -> None:
        self._root = Path(root)
        self._max_extract_bytes = max_extract_bytes

    @property
    def root(self) -> Path:
        return self._root

    async def stage(self, source: str) -> StagedArtifact:
        return await asyncio.to_thread(self._stage_blocking, source)

    async def discard(self, staged: StagedArtifact) -> None:
        await asyncio.to_thread(self._discard_blocking, staged)

    # ------------------------------------------------------------------ blocking

    def _stage_blocking(self, source: str) -> StagedArtifact:
        text = source.strip()
        if not text:
            raise ManifestValidationError("extension source must not be empty")
        if _REMOTE_SCHEME.match(text) or text.startswith("git+"):
            raise ManifestValidationError(
                "remote extension sources are not supported; pin a local artifact"
            )
        path = Path(text)
        if not path.exists():
            raise ManifestValidationError(f"extension source does not exist: {text}")
        self._root.mkdir(parents=True, exist_ok=True)
        target = self._root / f"stage_{secrets.token_hex(12)}"
        try:
            if path.is_dir():
                self._copy_directory(path, target)
            elif path.is_file() and path.suffix.lower() in _ARCHIVE_SUFFIXES:
                self._extract_archive(path, target)
            else:
                raise ManifestValidationError(
                    "unsupported extension source; provide a local directory or a "
                    "local .zip/.whl archive"
                )
        except BaseException:
            shutil.rmtree(target, ignore_errors=True)
            raise
        return StagedArtifact(
            source=text,
            root=target,
            artifact_hash=compute_artifact_hash(target),
        )

    def _copy_directory(self, source: Path, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=False)
        for entry in sorted(source.rglob("*")):
            relative = entry.relative_to(source)
            if any(part in IGNORED_ARTIFACT_PARTS for part in relative.parts):
                continue
            if entry.is_symlink():
                raise ManifestValidationError(
                    f"extension artifacts may not contain symlinks: {relative.as_posix()}"
                )
            if entry.is_dir():
                (target / relative).mkdir(parents=True, exist_ok=True)
            elif entry.is_file():
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(entry, destination)

    def _extract_archive(self, archive_path: Path, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=False)
        total = 0
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            if len(infos) > 100_000:
                raise ManifestValidationError("archive contains too many entries")
            for info in infos:
                name = info.filename.replace("\\", "/")
                parts = [part for part in name.split("/") if part not in ("", ".")]
                if (
                    not parts
                    or name.startswith("/")
                    or _WINDOWS_ABSOLUTE.match(name)
                    or ".." in parts
                ):
                    raise ManifestValidationError(
                        f"archive contains an unsafe path: {info.filename!r}"
                    )
                if any(part in IGNORED_ARTIFACT_PARTS for part in parts):
                    # Never staged, never hashed, never installed.
                    continue
                total += info.file_size
                if total > self._max_extract_bytes:
                    raise ManifestValidationError("archive exceeds the extraction size limit")
                destination = target.joinpath(*parts)
                if info.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as stream, destination.open("wb") as output:
                    shutil.copyfileobj(stream, output)

    def _discard_blocking(self, staged: StagedArtifact) -> None:
        root = self._root.resolve()
        target = Path(staged.root).resolve()
        if target == root or not target.is_relative_to(root):
            raise ExtensionError(
                "refusing to delete a path outside the extension staging root"
            )
        if target.exists():
            shutil.rmtree(target)


__all__ = ["LocalArtifactStager"]
