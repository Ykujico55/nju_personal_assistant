"""Reconciliation, side-by-side version builds and hybrid retrieval.

Correctness rules enforced here:

* the raw file bytes are the source of truth and are never modified;
* ``knowledge_sources.content_hash`` only advances when a new version is fully
  built and atomically activated, so a failed rebuild can never make stale
  chunks look current;
* every mutation is idempotent, so concurrent or repeated reconciliation
  converges to exactly one active version per source;
* citations re-verify the current file hash before being reported as CURRENT.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import uuid
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, TypeVar

from .chunking import chunk_document
from .embedding import (
    MAX_EMBEDDING_TEXTS,
    EmbeddingIdentity,
    EmbeddingProvider,
    EmbeddingUnavailable,
)
from .extractors import EXTRACTOR_VERSION, extract_document, extractor_name_for
from .models import (
    Chunk,
    ChunkRecord,
    EvidenceStatus,
    ExtractedDocument,
    ExtractionDiagnostic,
    ExtractionDiagnosticCode,
    ExtractionError,
    ReconcileReport,
    SearchOutcome,
    SearchResult,
)
from .paths import AuthorizedRoot, PathSafetyError, WalkedFile, hash_bytes, walk_root
from .search import ChunkKey, rrf_fuse
from .store import KnowledgeStore, chunk_record

ARM_LIMIT_FACTOR = 4
MIN_ARM_LIMIT = 20
MAX_ARM_LIMIT = 200
MAX_FILTER_VALUES = 64
ALLOWED_FILTERS = frozenset({"root_keys", "media_types", "source_ids"})
ORPHAN_BUILD_SECONDS = 3600
BUILD_EMBEDDING_BATCH = 16

EVENT_ADDED = "knowledge.file_added"
EVENT_MODIFIED = "knowledge.file_modified"
EVENT_MOVED = "knowledge.file_moved"
EVENT_DELETED = "knowledge.file_deleted"

_T = TypeVar("_T")


async def _shield[T](awaitable: Awaitable[T]) -> T:
    """Run cleanup to completion even when the surrounding task is cancelled."""

    task = asyncio.ensure_future(awaitable)
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


@dataclass(frozen=True, slots=True)
class ScannedFile:
    root_key: str
    relative_path: str
    media_type: str
    size_bytes: int
    mtime_ns: int
    content_hash: str


@dataclass(slots=True)
class _Diff:
    added: list[ScannedFile] = field(default_factory=list)
    modified: list[tuple[ScannedFile, Any]] = field(default_factory=list)
    moved: list[ScannedFile] = field(default_factory=list)
    unchanged: int = 0
    deleted: list[Any] = field(default_factory=list)


class KnowledgeIndex:
    def __init__(
        self,
        store: KnowledgeStore,
        roots: Sequence[AuthorizedRoot],
        embedder: EmbeddingProvider,
        *,
        migrations: Sequence[Mapping[str, Any]],
        max_file_bytes: int,
        include_hidden: bool = False,
        extension_id: str,
        extension_version: str,
    ) -> None:
        self.store = store
        self.roots = tuple(roots)
        self._roots_by_key = {root.key: root for root in self.roots}
        self.embedder = embedder
        self.migrations = tuple(migrations)
        self.max_file_bytes = max_file_bytes
        self.include_hidden = include_hidden
        self.extension_id = extension_id
        self.extension_version = extension_version
        self._ready = False

    @property
    def embedding_identity(self) -> EmbeddingIdentity:
        return self.embedder.identity

    @property
    def vector_mode(self) -> str:
        return "disabled" if self.embedder.identity.provider == "none" else "enabled"

    async def ensure_ready(self) -> None:
        if self._ready:
            return
        await self.store.migrate(self.migrations)
        self._ready = True

    # -- reconciliation -----------------------------------------------------

    async def scan(self) -> tuple[list[ScannedFile], list[ExtractionDiagnostic]]:
        errors: list[ExtractionDiagnostic] = []
        scanned: list[ScannedFile] = []
        for root in self.roots:
            result = walk_root(root, include_hidden=self.include_hidden)
            for relative, code, message in result.diagnostics:
                errors.append(ExtractionDiagnostic(code=code, message=message, path=relative))
            for walked in result.files:
                entry = self._scan_file(root, walked, errors)
                if entry is not None:
                    scanned.append(entry)
        scanned.sort(key=lambda item: (item.root_key, item.relative_path))
        return scanned, errors

    def _scan_file(
        self,
        root: AuthorizedRoot,
        walked: WalkedFile,
        errors: list[ExtractionDiagnostic],
    ) -> ScannedFile | None:
        if walked.size_bytes > self.max_file_bytes:
            errors.append(
                ExtractionDiagnostic(
                    code=ExtractionDiagnosticCode.FILE_TOO_LARGE,
                    message="file exceeds the configured size limit",
                    path=f"{root.key}/{walked.relative_path}",
                )
            )
            return None
        try:
            data = root.read_bytes(walked.relative_path, max_bytes=self.max_file_bytes)
        except PathSafetyError as exc:
            errors.append(
                ExtractionDiagnostic(
                    code=ExtractionDiagnosticCode.READ_ERROR,
                    message=str(exc),
                    path=f"{root.key}/{walked.relative_path}",
                )
            )
            return None
        except OSError:
            errors.append(
                ExtractionDiagnostic(
                    code=ExtractionDiagnosticCode.READ_ERROR,
                    message="file could not be read",
                    path=f"{root.key}/{walked.relative_path}",
                )
            )
            return None
        return ScannedFile(
            root_key=root.key,
            relative_path=walked.relative_path,
            media_type=walked.media_type,
            size_bytes=walked.size_bytes,
            mtime_ns=walked.mtime_ns,
            content_hash=hash_bytes(data),
        )

    async def detect_changes(self) -> int:
        """Append change events without mutating the index (latency only)."""

        await self.ensure_ready()
        scanned, _errors = await self.scan()
        stored = await self.store.fetch_sources([root.key for root in self.roots])
        diff = self._diff(
            scanned,
            stored,
            mode="incremental",
            authorized_keys={root.key for root in self.roots},
        )
        events = 0
        for entry in diff.added:
            identity = await self._added_event_identity(entry)
            if identity is None:
                # Both deterministic identities are occupied by other live
                # sources.  Reconciliation will claim a stable fallback before
                # publishing the authoritative event.
                continue
            source_id, generation = identity
            events += await self._event(
                EVENT_ADDED,
                entry,
                previous_hash=None,
                source_id=source_id,
                generation=generation,
            )
        for entry, source in diff.modified:
            events += await self._event(
                EVENT_MODIFIED,
                entry,
                previous_hash=source.content_hash,
                source_id=source.source_id,
                generation=source.generation + 1,
            )
        for entry, source in diff.moved:
            events += await self._event(
                EVENT_MOVED,
                entry,
                previous_hash=entry.content_hash,
                source_id=source.source_id,
                generation=source.generation + 1,
            )
        for source in diff.deleted:
            events += await self._event(
                EVENT_DELETED,
                None,
                previous_hash=source.content_hash,
                source_id=source.source_id,
                root_key=source.root_key,
                relative_path=source.relative_path,
                generation=source.generation + 1,
            )
        return events

    async def _added_event_identity(
        self, entry: ScannedFile
    ) -> tuple[str, int] | None:
        candidate = source_id_for(entry.root_key, entry.relative_path)
        state = await self.store.source_identity_generation(candidate)
        if state is not None and state[0] == "active":
            candidate = collision_source_id_for(
                entry.root_key, entry.relative_path, entry.content_hash
            )
            state = await self.store.source_identity_generation(candidate)
            if state is not None and state[0] == "active":
                return None
        generation = (state[1] if state is not None else 0) + 1
        return candidate, generation

    async def reconcile(self, *, mode: str = "incremental") -> ReconcileReport:
        if mode not in {"incremental", "full"}:
            raise ValueError("mode must be 'incremental' or 'full'")
        await self.ensure_ready()
        run_id = uuid.uuid4().hex
        # A crashed or cancelled run may have left BUILDING candidates behind;
        # clean only stale ones so concurrent live builds are never disturbed.
        await self.store.discard_orphan_building_versions(
            run_id=run_id, max_age_seconds=ORPHAN_BUILD_SECONDS
        )
        errors: list[ExtractionDiagnostic] = []
        scanned, scan_errors = await self.scan()
        errors.extend(scan_errors)
        # Sources outside the currently authorized roots are no longer readable
        # and must be removed, even when reconciliation does not scan their root.
        stored = await self.store.fetch_all_sources()
        authorized_keys = {root.key for root in self.roots}
        diff = self._diff(scanned, stored, mode=mode, authorized_keys=authorized_keys)
        for entry, source in diff.unchanged_candidates:
            if await self._needs_rebuild(source, entry, mode=mode):
                diff.modified.append((entry, source))
            else:
                diff.unchanged += 1
        report = ReconcileReport(
            mode=mode,
            roots=len(self.roots),
            scanned=len(scanned),
            added=len(diff.added),
            modified=len(diff.modified),
            moved=len(diff.moved),
            deleted=len(diff.deleted),
            unchanged=diff.unchanged,
        )
        moved_snapshots: dict[str, Any] = {}
        for entry, source in diff.moved:
            generation = await self.store.move_source(
                source_id=source.source_id,
                root_key=entry.root_key,
                relative_path=entry.relative_path,
                media_type=entry.media_type,
                size_bytes=entry.size_bytes,
                mtime_ns=entry.mtime_ns,
                expected_root_key=source.root_key,
                expected_relative_path=source.relative_path,
                expected_generation=source.generation,
            )
            if generation is None:
                # Another reconciliation already moved or otherwise advanced
                # this source.  A stale observation must not create a second
                # generation or duplicate transition event.
                report.moved -= 1
                continue
            moved_snapshots[source.source_id] = replace(
                source,
                root_key=entry.root_key,
                relative_path=entry.relative_path,
                media_type=entry.media_type,
                size_bytes=entry.size_bytes,
                mtime_ns=entry.mtime_ns,
                generation=generation,
            )
            report.events += await self._event(
                EVENT_MOVED,
                entry,
                previous_hash=entry.content_hash,
                source_id=source.source_id,
                generation=generation,
            )
        for entry in diff.added:
            await self._build(entry, None, report, errors, run_id=run_id)
        for entry, source in diff.modified:
            await self._build(
                entry,
                moved_snapshots.get(source.source_id, source),
                report,
                errors,
                run_id=run_id,
            )
        for source in diff.deleted:
            generation = await self.store.delete_source(
                source_id=source.source_id,
                reason_hash=source.content_hash,
                expected_root_key=source.root_key,
                expected_relative_path=source.relative_path,
                expected_generation=source.generation,
            )
            if generation is None:
                report.deleted -= 1
                continue
            report.events += await self._event(
                EVENT_DELETED,
                None,
                previous_hash=source.content_hash,
                source_id=source.source_id,
                root_key=source.root_key,
                relative_path=source.relative_path,
                generation=generation,
            )
        report.errors = tuple(errors)
        return report

    async def _needs_rebuild(
        self, source: Any, entry: ScannedFile, *, mode: str
    ) -> bool:
        if mode == "full":
            return True
        if not source.active_version:
            # A first build that failed (or was cancelled) must be retried even
            # when the file bytes did not change.
            return True
        identity = await self.store.active_version_identity(source.source_id)
        if not identity:
            return True
        if str(identity.get("extractor_name", "")) != extractor_name_for(entry.media_type):
            return True
        if str(identity.get("extractor_version", "")) != EXTRACTOR_VERSION:
            return True
        current = self.embedder.identity
        return (
            str(identity.get("embedding_provider", "")) != current.provider
            or str(identity.get("embedding_model", "")) != current.model
            or int(identity.get("embedding_dim", -1)) != current.dim
            or str(identity.get("embedding_version", "")) != current.version
        )

    def _diff(
        self,
        scanned: Sequence[ScannedFile],
        stored: Mapping[str, Any],
        *,
        mode: str,
        authorized_keys: set[str],
    ) -> _WorkingDiff:
        present = {(item.root_key, item.relative_path): item for item in scanned}
        stored_by_path = {
            (source.root_key, source.relative_path): source for source in stored.values()
        }
        working: dict[tuple[str, str], Any] = dict(stored_by_path)
        result = _WorkingDiff(mode=mode)
        vanished = [
            source
            for key, source in stored_by_path.items()
            if key not in present
        ]
        vanished_by_hash: dict[str, list[Any]] = {}
        for source in vanished:
            vanished_by_hash.setdefault(source.content_hash, []).append(source)
        for key in sorted(present):
            entry = present[key]
            if key in working:
                continue
            candidates = [
                candidate
                for candidate in vanished_by_hash.get(entry.content_hash, [])
                if candidate.source_id not in result.claimed
            ]
            if not candidates:
                continue
            chosen = min(candidates, key=lambda item: (item.root_key, item.relative_path))
            result.claimed.add(chosen.source_id)
            result.moved.append((entry, chosen))
            working.pop((chosen.root_key, chosen.relative_path), None)
            working[key] = replace(
                chosen,
                root_key=entry.root_key,
                relative_path=entry.relative_path,
                size_bytes=entry.size_bytes,
                mtime_ns=entry.mtime_ns,
            )
        for key in sorted(present):
            entry = present[key]
            if entry.root_key not in authorized_keys:
                # A single scan can only come from an authorized root.
                continue
            source = working.get(key)
            if source is None:
                result.added.append(entry)
                continue
            if source.content_hash != entry.content_hash:
                result.modified.append((entry, source))
                continue
            result.unchanged_candidates.append((entry, source))
        for source in vanished:
            if source.source_id in result.claimed:
                continue
            result.deleted.append(source)
        for source in stored.values():
            if source.root_key in authorized_keys:
                continue
            if source.source_id in result.claimed:
                continue
            if any(item.source_id == source.source_id for item in result.deleted):
                continue
            result.deleted.append(source)
        return result

    async def _build(
        self,
        entry: ScannedFile,
        source: Any,
        report: ReconcileReport,
        errors: list[ExtractionDiagnostic],
        *,
        run_id: str,
    ) -> None:
        root = self._roots_by_key.get(entry.root_key)
        if root is None:
            errors.append(
                ExtractionDiagnostic(
                    code=ExtractionDiagnosticCode.READ_ERROR,
                    message="authorized root is no longer configured",
                    path=entry.relative_path,
                )
            )
            return
        source_id: str | None = None
        version_id: str | None = None
        try:
            data = root.read_bytes(entry.relative_path, max_bytes=self.max_file_bytes)
        except (PathSafetyError, OSError) as exc:
            errors.append(
                ExtractionDiagnostic(
                    code=ExtractionDiagnosticCode.READ_ERROR,
                    message=str(exc) if isinstance(exc, PathSafetyError) else "read failed",
                    path=entry.relative_path,
                )
            )
            return
        content_hash = hash_bytes(data)
        if content_hash != entry.content_hash:
            errors.append(
                ExtractionDiagnostic(
                    code=ExtractionDiagnosticCode.READ_ERROR,
                    message="file changed while scanning; will retry next reconciliation",
                    path=entry.relative_path,
                )
            )
            return
        try:
            document = extract_document(
                data,
                entry.media_type,
                path=entry.relative_path,
                max_file_bytes=self.max_file_bytes,
            )
            chunks = chunk_document(document)
        except ExtractionError as exc:
            errors.append(
                ExtractionDiagnostic(code=exc.code, message=str(exc), path=entry.relative_path)
            )
            return
        except Exception:
            # An extractor defect must degrade to a typed diagnostic; it may
            # never take down the worker or the currently active version.
            errors.append(
                ExtractionDiagnostic(
                    code=ExtractionDiagnosticCode.BUILD_FAILED,
                    message="extractor failed on this file",
                    path=entry.relative_path,
                )
            )
            return
        if not chunks:
            errors.append(
                ExtractionDiagnostic(
                    code=ExtractionDiagnosticCode.EMPTY_FILE,
                    message="no chunks were produced",
                    path=entry.relative_path,
                )
            )
            return
        try:
            # Learn a real embedding dimension before persisting any identity,
            # so the version id and the stored model/dimension can never diverge
            # from the vectors that will be written.
            await self._ensure_embedding_dimension()
            version_id = compute_version_id(
                content_hash, document, self.embedder.identity
            )
            if (
                source is not None
                and source.active_version == version_id
                and source.content_hash == content_hash
            ):
                return
            if source is None:
                claimed = None
                for candidate in (
                    source_id_for(entry.root_key, entry.relative_path),
                    collision_source_id_for(
                        entry.root_key, entry.relative_path, content_hash
                    ),
                    uuid.uuid4().hex,
                ):
                    claimed = await self.store.insert_source(
                        source_id=candidate,
                        root_key=entry.root_key,
                        relative_path=entry.relative_path,
                        media_type=entry.media_type,
                        size_bytes=entry.size_bytes,
                        mtime_ns=entry.mtime_ns,
                        content_hash=content_hash,
                    )
                    if claimed is not None:
                        break
                if claimed is None:
                    raise RuntimeError("source identity could not be claimed")
                source, created = claimed
                source_id = source.source_id
                if (
                    source.active_version == version_id
                    and source.content_hash == content_hash
                ):
                    return
                event_type = EVENT_ADDED if created else EVENT_MODIFIED
                previous_hash = None if created else source.content_hash
            else:
                source_id = source.source_id
                event_type = EVENT_MODIFIED
                previous_hash = source.content_hash
            identity = self.embedder.identity
            owns_candidate = await self.store.begin_version(
                source_id=source_id,
                version_id=version_id,
                content_hash=content_hash,
                extractor_name=document.extractor_name,
                extractor_version=document.extractor_version,
                embedding_provider=identity.provider,
                embedding_model=identity.model,
                embedding_dim=identity.dim,
                embedding_version=identity.version,
                built_by=run_id,
            )
            if not owns_candidate:
                return
            embeddings = await self._embed_chunks(
                chunks,
                source_id=source_id,
                version_id=version_id,
                built_by=run_id,
            )
            await self.store.insert_chunks(
                source_id=source_id,
                version_id=version_id,
                chunks=chunks,
                embeddings=embeddings,
                built_by=run_id,
            )
            generation = await self.store.activate_version(
                source_id=source_id,
                version_id=version_id,
                content_hash=content_hash,
                size_bytes=entry.size_bytes,
                mtime_ns=entry.mtime_ns,
                chunk_count=len(chunks),
                diagnostics=[item.as_json() for item in document.diagnostics],
                expected_active_version=(
                    source.active_version if source is not None else None
                ),
                expected_root_key=source.root_key,
                expected_relative_path=source.relative_path,
                expected_generation=source.generation,
                built_by=run_id,
            )
        except BaseException as exc:
            # Cancellation and unexpected failures must both remove the
            # candidate under a shielded cleanup before the error propagates.
            if source_id is not None and version_id is not None:
                await self._abort(source_id, version_id, run_id)
            if isinstance(exc, asyncio.CancelledError):
                raise
            if isinstance(exc, Exception):
                errors.append(self._build_error(exc, entry))
                return
            raise
        report.versions_built += 1
        report.events += await self._event(
            event_type,
            ScannedFile(
                root_key=entry.root_key,
                relative_path=entry.relative_path,
                media_type=entry.media_type,
                size_bytes=entry.size_bytes,
                mtime_ns=entry.mtime_ns,
                content_hash=content_hash,
            ),
            previous_hash=previous_hash,
            source_id=source_id,
            generation=generation,
        )

    async def _ensure_embedding_dimension(self) -> None:
        identity = self.embedder.identity
        if identity.provider == "none" or identity.dim > 0:
            return
        await self.embedder.embed(["embedding dimension probe"])

    def _build_error(self, exc: Exception, entry: ScannedFile) -> ExtractionDiagnostic:
        if isinstance(exc, EmbeddingUnavailable):
            return ExtractionDiagnostic(
                code=ExtractionDiagnosticCode.BUILD_FAILED,
                message=f"embedding unavailable: {exc.code}",
                path=entry.relative_path,
            )
        return ExtractionDiagnostic(
            code=ExtractionDiagnosticCode.BUILD_FAILED,
            message="index build failed and was rolled back",
            path=entry.relative_path,
        )

    async def _abort(self, source_id: str, version_id: str, run_id: str) -> None:
        # The cleanup must complete even under repeated cancellation, otherwise a
        # BUILDING candidate could be left behind with no active version.
        with contextlib.suppress(Exception):
            await _shield(
                self.store.fail_version(
                    source_id=source_id, version_id=version_id, built_by=run_id
                )
            )

    async def _embed_chunks(
        self,
        chunks: Sequence[Chunk],
        *,
        source_id: str,
        version_id: str,
        built_by: str,
    ) -> list[list[float] | None]:
        if self.embedder.identity.provider == "none":
            return [None] * len(chunks)
        vectors: list[list[float]] = []
        texts = [chunk.text for chunk in chunks]
        batch_size = min(BUILD_EMBEDDING_BATCH, MAX_EMBEDDING_TEXTS)
        for start in range(0, len(texts), batch_size):
            vectors.extend(
                await self.embedder.embed(texts[start : start + batch_size])
            )
            if not await self.store.heartbeat_version(
                source_id=source_id, version_id=version_id, built_by=built_by
            ):
                raise RuntimeError("embedding build lost candidate ownership")
        dim = self.embedder.identity.dim
        if len(vectors) != len(chunks):
            raise EmbeddingUnavailable(
                "EMBEDDING_PROTOCOL_ERROR", "embedding count does not match chunk count"
            )
        for vector in vectors:
            if dim and len(vector) != dim:
                raise EmbeddingUnavailable(
                    "EMBEDDING_DIMENSION_CHANGED", "embedding dimension changed mid-build"
                )
        return list(vectors)

    async def _event(
        self,
        event_type: str,
        entry: ScannedFile | None,
        *,
        previous_hash: str | None,
        source_id: str | None,
        generation: int,
        root_key: str | None = None,
        relative_path: str | None = None,
    ) -> int:
        resolved_root = entry.root_key if entry is not None else (root_key or "")
        resolved_path = (
            entry.relative_path if entry is not None else (relative_path or "")
        )
        resolved_source = (
            source_id
            if source_id is not None
            else source_id_for(resolved_root, resolved_path)
        )
        current_hash = entry.content_hash if entry is not None else None
        dedupe_key = "|".join(
            (
                event_type,
                resolved_root,
                resolved_path,
                str(generation),
                resolved_source,
                current_hash or "-",
                previous_hash or "-",
            )
        )
        inserted = await self.store.append_event(
            event_type=event_type,
            source_id=resolved_source,
            dedupe_key=dedupe_key,
            root_key=resolved_root,
            relative_path=resolved_path,
            content_hash=current_hash,
            previous_hash=previous_hash,
        )
        return 1 if inserted else 0

    # -- retrieval ----------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        filters: Mapping[str, Sequence[str]] | None = None,
    ) -> SearchOutcome:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if len(query) > 512:
            raise ValueError("query exceeds the maximum length")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        normalized_filters = _normalize_filters(
            filters, authorized_roots={root.key for root in self.roots}
        )
        await self.ensure_ready()
        if not normalized_filters.get("root_keys"):
            # No authorized roots (or every requested root was unauthorized):
            # the authorization boundary wins immediately, before any query.
            return SearchOutcome(
                results=(), scores=(), unknown=True, vector_mode=self.vector_mode
            )
        arm_limit = min(max(limit * ARM_LIMIT_FACTOR, MIN_ARM_LIMIT), MAX_ARM_LIMIT)
        fts_rows = await self.store.search_fts(
            query=query, limit=arm_limit, filters=normalized_filters
        )
        rankings: list[list[ChunkKey]] = []
        fts_keys = _row_keys(fts_rows)
        rankings.append(fts_keys)
        vector_mode = "disabled"
        if self.embedder.identity.provider != "none":
            try:
                vectors = await self.embedder.embed([query])
                if vectors:
                    vector_rows = await self.store.search_vector(
                        vector=vectors[0],
                        limit=arm_limit,
                        filters=normalized_filters,
                        identity=self.embedder.identity,
                    )
                    vector_keys = _row_keys(vector_rows)
                    if vector_keys:
                        rankings.append(vector_keys)
                        vector_mode = "enabled"
            except EmbeddingUnavailable:
                vector_mode = "disabled"
        fused = rrf_fuse(rankings)[:limit]
        keys = [key for key, _score in fused]
        if not keys:
            return SearchOutcome(results=(), scores=(), unknown=True, vector_mode=vector_mode)
        rows = await self.store.fetch_chunks(keys)
        by_key: dict[ChunkKey, Mapping[str, Any]] = {}
        for row in rows:
            key = _row_key(row)
            if key is not None:
                by_key[key] = row
        ordered = [by_key[key] for key in keys if key in by_key]
        records = [chunk_record(row) for row in ordered]
        scores = [score for key, score in fused if key in by_key]
        return SearchOutcome(
            results=tuple(records),
            scores=tuple(scores),
            unknown=not records,
            vector_mode=vector_mode,
        )

    async def evaluate(
        self, records: Sequence[ChunkRecord], scores: Sequence[float]
    ) -> list[SearchResult]:
        """Re-verify every candidate against the current source bytes.

        Non-CURRENT results deliberately expose no chunk text; the caller may
        only use the body of a verified CURRENT source.
        """

        evaluated: list[SearchResult] = []
        for index, record in enumerate(records):
            status, observed_hash = await self._verify_with_hash(record)
            if status != EvidenceStatus.CURRENT:
                await self.note_stale(record, status, observed_hash=observed_hash)
            score = scores[index] if index < len(scores) else 0.0
            evaluated.append(SearchResult(record=record, status=status, score=score))
        return evaluated

    async def verify(self, record: Any) -> EvidenceStatus:
        status, _observed_hash = await self._verify_with_hash(record)
        return status

    async def _verify_with_hash(
        self, record: Any
    ) -> tuple[EvidenceStatus, str | None]:
        root = self._roots_by_key.get(record.root_key)
        if root is None:
            return EvidenceStatus.DELETED, None
        try:
            data = root.read_bytes(record.relative_path, max_bytes=self.max_file_bytes)
        except (PathSafetyError, OSError):
            return EvidenceStatus.DELETED, None
        observed_hash = hash_bytes(data)
        status = (
            EvidenceStatus.CURRENT
            if observed_hash == record.source_hash
            else EvidenceStatus.STALE
        )
        return status, observed_hash

    async def note_stale(
        self,
        record: Any,
        status: EvidenceStatus,
        *,
        observed_hash: str | None = None,
    ) -> int:
        if status == EvidenceStatus.CURRENT:
            return 0
        source = await self.store.get_source(record.source_id)
        generation = source.generation + 1 if source is not None else 1
        if status == EvidenceStatus.DELETED:
            return await self._event(
                EVENT_DELETED,
                None,
                previous_hash=record.source_hash,
                source_id=record.source_id,
                root_key=record.root_key,
                relative_path=record.relative_path,
                generation=generation,
            )
        return await self._event(
            EVENT_MODIFIED,
            ScannedFile(
                root_key=record.root_key,
                relative_path=record.relative_path,
                media_type=record.media_type,
                size_bytes=0,
                mtime_ns=0,
                content_hash=observed_hash or "",
            ),
            previous_hash=record.source_hash,
            source_id=record.source_id,
            generation=generation,
        )


@dataclass(slots=True)
class _WorkingDiff:
    mode: str
    added: list[ScannedFile] = field(default_factory=list)
    modified: list[tuple[ScannedFile, Any]] = field(default_factory=list)
    moved: list[tuple[ScannedFile, Any]] = field(default_factory=list)
    deleted: list[Any] = field(default_factory=list)
    unchanged_candidates: list[tuple[ScannedFile, Any]] = field(default_factory=list)
    unchanged: int = 0
    claimed: set[str] = field(default_factory=set)


def _normalize_filters(
    filters: Mapping[str, Sequence[str]] | None,
    *,
    authorized_roots: set[str],
) -> dict[str, list[str]]:
    if filters is None:
        requested: list[str] = []
    else:
        if not isinstance(filters, Mapping):
            raise ValueError("filters must be an object")
        unknown = set(filters) - ALLOWED_FILTERS
        if unknown:
            raise ValueError(f"unknown filters: {sorted(unknown)}")
        requested = _clean_filter_values(filters.get("root_keys"), "root_keys")
    # The currently authorized roots are always part of the query boundary,
    # independent of any caller-provided filter.
    allowed = [key for key in requested if key in authorized_roots]
    if requested and not allowed:
        return {}
    normalized: dict[str, list[str]] = {"root_keys": allowed or sorted(authorized_roots)}
    if not filters:
        return normalized
    for name in ("media_types", "source_ids"):
        cleaned = _clean_filter_values(filters.get(name), name)
        if cleaned:
            normalized[name] = cleaned
    return normalized


def _clean_filter_values(values: object, name: str) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"filter {name} must be an array")
    if len(values) > MAX_FILTER_VALUES:
        raise ValueError(f"filter {name} has too many values")
    cleaned: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value or len(value) > 128:
            raise ValueError(f"filter {name} contains an invalid value")
        cleaned.append(value)
    return cleaned


def source_id_for(root_key: str, relative_path: str) -> str:
    return hashlib.sha256(
        f"{root_key}\0{relative_path}".encode()
    ).hexdigest()


def collision_source_id_for(
    root_key: str, relative_path: str, content_hash: str
) -> str:
    """Stable fallback when a moved source still owns the path-derived id."""

    return hashlib.sha256(
        f"{root_key}\0{relative_path}\0{content_hash}".encode()
    ).hexdigest()


def compute_version_id(
    content_hash: str, document: ExtractedDocument, identity: EmbeddingIdentity
) -> str:
    payload = (
        f"{content_hash}\0{document.extractor_name}:{document.extractor_version}"
        f"\0{identity.key()}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _row_keys(rows: Sequence[Mapping[str, Any]]) -> list[ChunkKey]:
    keys: list[ChunkKey] = []
    for row in rows:
        key = _row_key(row)
        if key is not None:
            keys.append(key)
    return keys


def _row_key(row: Mapping[str, Any]) -> ChunkKey | None:
    source_id = row.get("source_id")
    version_id = row.get("version_id")
    ordinal = row.get("ordinal")
    if not isinstance(source_id, str) or not isinstance(version_id, str):
        return None
    if isinstance(ordinal, bool) or not isinstance(ordinal, int):
        return None
    return (source_id, version_id, ordinal)


__all__ = [
    "EVENT_ADDED",
    "EVENT_DELETED",
    "EVENT_MODIFIED",
    "EVENT_MOVED",
    "KnowledgeIndex",
    "ScannedFile",
    "compute_version_id",
    "source_id_for",
]
