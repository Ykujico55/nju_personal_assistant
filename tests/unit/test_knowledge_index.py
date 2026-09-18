"""F05: reconciliation, atomic version switching and deletion propagation.

These unit tests drive the extension pipeline against an in-memory store double;
the real PostgreSQL + pgvector behavior is covered by the integration suite.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from personal_knowledge.embedding import DeterministicTestEmbeddingProvider
from personal_knowledge.index import KnowledgeIndex, source_id_for
from personal_knowledge.models import Chunk, Locator
from personal_knowledge.paths import parse_roots, resolve_roots
from personal_knowledge.store import MAX_CHUNK_BATCH_BYTES, KnowledgeStore

from tests.unit.fake_knowledge_store import FakeKnowledgeStore

MIGRATIONS = (
    {
        "version": 1,
        "path": "migrations/0001_knowledge_index.sql",
        "checksum": hashlib.sha256(b"test").hexdigest(),
        "description": "test",
    },
)


class IndexReconcileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa_f05_index_"))
        self.root = self.tmp / "notes"
        self.root.mkdir()
        self.store = FakeKnowledgeStore()
        self.embedder = DeterministicTestEmbeddingProvider(dim=16)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def index(
        self,
        roots: list[Path] | None = None,
        *,
        embedder: object | None = None,
        root_keys: list[str] | None = None,
    ) -> KnowledgeIndex:
        paths = roots or [self.root]
        keys = root_keys or [f"root-{number}" for number in range(len(paths))]
        specs = parse_roots(
            [
                {"path": str(path), "key": key}
                for path, key in zip(paths, keys, strict=True)
            ]
        )
        return KnowledgeIndex(
            self.store,  # type: ignore[arg-type]
            resolve_roots(specs),
            embedder or self.embedder,  # type: ignore[arg-type]
            migrations=MIGRATIONS,
            max_file_bytes=1024 * 1024,
            extension_id="personal.knowledge",
            extension_version="0.1.0",
        )

    def write(self, relative: str, content: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        return path

    def source_row(self, relative: str) -> dict:
        source_id = source_id_for("root-0", relative)
        return self.store.sources[source_id]

    async def test_add_builds_one_active_version_with_chunks(self) -> None:
        self.write("a.md", "# Title\n\nAlpha content here.\n")
        index = self.index()
        report = await index.reconcile()
        self.assertEqual(1, report.added)
        self.assertEqual(1, report.versions_built)
        self.assertEqual([1], self.store.migrations_applied)
        row = self.source_row("a.md")
        self.assertIsNotNone(row["active_version"])
        chunks = [key for key in self.store.chunks if key[0] == row["source_id"]]
        self.assertGreaterEqual(len(chunks), 1)
        self.assertEqual(1, len([key for key in self.store.versions if key[0] == row["source_id"]]))

    async def test_repeated_scan_is_idempotent(self) -> None:
        self.write("a.md", "# Title\n\nAlpha content here.\n")
        index = self.index()
        await index.reconcile()
        before = (dict(self.store.sources), dict(self.store.versions), dict(self.store.chunks))
        second = await index.reconcile()
        self.assertEqual(1, second.unchanged)
        self.assertEqual(0, second.versions_built)
        self.assertEqual(before, (self.store.sources, self.store.versions, self.store.chunks))

    async def test_modified_file_switches_version_atomically(self) -> None:
        path = self.write("a.md", "# Title\n\nAlpha content here.\n")
        index = self.index()
        await index.reconcile()
        first_version = self.source_row("a.md")["active_version"]
        path.write_text("# Title\n\nBeta content changed.\n", encoding="utf-8", newline="\n")
        report = await index.reconcile()
        self.assertEqual(1, report.modified)
        row = self.source_row("a.md")
        self.assertNotEqual(first_version, row["active_version"])
        versions = [key for key in self.store.versions if key[0] == row["source_id"]]
        self.assertEqual(1, len(versions))
        self.assertEqual(row["active_version"], versions[0][1])

    async def test_delete_propagates_and_keeps_a_tombstone(self) -> None:
        path = self.write("a.md", "# Title\n\nAlpha content here.\n")
        index = self.index()
        await index.reconcile()
        source_id = source_id_for("root-0", "a.md")
        path.unlink()
        report = await index.reconcile()
        self.assertEqual(1, report.deleted)
        self.assertNotIn(source_id, self.store.sources)
        self.assertIn(source_id, self.store.tombstones)
        self.assertEqual([], [key for key in self.store.chunks if key[0] == source_id])
        self.assertEqual([], [key for key in self.store.versions if key[0] == source_id])
        self.assertIn(
            "knowledge.file_deleted", [event["event_type"] for event in self.store.events]
        )

    async def test_rename_keeps_source_identity_and_version(self) -> None:
        path = self.write("a.md", "# Title\n\nAlpha content here.\n")
        index = self.index()
        await index.reconcile()
        source_id = source_id_for("root-0", "a.md")
        active = self.store.sources[source_id]["active_version"]
        path.rename(self.root / "renamed.md")
        report = await index.reconcile()
        self.assertEqual(1, report.moved)
        row = self.store.sources[source_id]
        self.assertEqual("renamed.md", row["relative_path"])
        self.assertEqual(active, row["active_version"])
        self.assertEqual(1, len([key for key in self.store.versions if key[0] == source_id]))

    async def test_rename_across_media_types_rebuilds_with_the_new_extractor(self) -> None:
        path = self.write("a.txt", "# Heading\n\nBody shared bytes.\n")
        index = self.index()
        await index.reconcile()
        source_id = source_id_for("root-0", "a.txt")
        previous_version = self.store.sources[source_id]["active_version"]

        path.rename(self.root / "a.md")
        report = await index.reconcile()

        row = self.store.sources[source_id]
        self.assertEqual(1, report.moved)
        self.assertEqual(1, report.modified)
        self.assertEqual(1, report.versions_built)
        self.assertEqual("text/markdown", row["media_type"])
        self.assertNotEqual(previous_version, row["active_version"])

    async def test_concurrent_stale_move_only_advances_generation_once(self) -> None:
        class BarrierStore(FakeKnowledgeStore):
            def __init__(self) -> None:
                super().__init__()
                self.arrived = 0
                self.ready = asyncio.Event()

            async def move_source(self, **kwargs: object) -> int | None:
                self.arrived += 1
                if self.arrived == 2:
                    self.ready.set()
                await asyncio.wait_for(self.ready.wait(), timeout=5)
                return await super().move_source(**kwargs)

        store = BarrierStore()
        self.store = store
        path = self.write("a.md", "# Title\n\nConcurrent move.\n")
        first = self.index()
        await first.reconcile()
        source_id = source_id_for("root-0", "a.md")
        generation = store.sources[source_id]["generation"]
        path.rename(self.root / "b.md")

        left, right = self.index(), self.index()
        reports = await asyncio.gather(left.reconcile(), right.reconcile())

        self.assertEqual(generation + 1, store.sources[source_id]["generation"])
        self.assertEqual(1, sum(report.moved for report in reports))
        moved_events = [
            event for event in store.events if event["event_type"] == "knowledge.file_moved"
        ]
        self.assertEqual(1, len(moved_events))

    async def test_concurrent_stale_delete_only_commits_one_transition(self) -> None:
        class BarrierStore(FakeKnowledgeStore):
            def __init__(self) -> None:
                super().__init__()
                self.arrived = 0
                self.ready = asyncio.Event()

            async def delete_source(self, **kwargs: object) -> int | None:
                self.arrived += 1
                if self.arrived == 2:
                    self.ready.set()
                await asyncio.wait_for(self.ready.wait(), timeout=5)
                return await super().delete_source(**kwargs)

        store = BarrierStore()
        self.store = store
        path = self.write("a.md", "# Title\n\nConcurrent deletion.\n")
        await self.index().reconcile()
        source_id = source_id_for("root-0", "a.md")
        previous_generation = int(store.sources[source_id]["generation"])
        path.unlink()

        reports = await asyncio.gather(self.index().reconcile(), self.index().reconcile())

        self.assertEqual(1, sum(report.deleted for report in reports))
        self.assertEqual(previous_generation + 1, store.tombstones[source_id]["generation"])
        deleted_events = [
            event for event in store.events if event["event_type"] == "knowledge.file_deleted"
        ]
        self.assertEqual(1, len(deleted_events))

    async def test_delete_and_readd_keep_generation_strictly_monotonic(self) -> None:
        path = self.write("a.md", "# Title\n\nFirst generation.\n")
        index = self.index()
        await index.reconcile()
        source_id = source_id_for("root-0", "a.md")
        first_generation = int(self.store.sources[source_id]["generation"])

        path.unlink()
        await index.reconcile()
        deleted_generation = int(self.store.tombstones[source_id]["generation"])
        self.assertEqual(first_generation + 1, deleted_generation)

        self.write("a.md", "# Title\n\nReappeared generation.\n")
        await index.reconcile()
        self.assertEqual(
            deleted_generation + 1,
            int(self.store.sources[source_id]["generation"]),
        )

    async def test_renamed_path_can_be_reused_by_a_distinct_new_source(self) -> None:
        original = self.write("a.md", "# Original\n\nAlpha identity.\n")
        index = self.index()
        await index.reconcile()
        original_id = source_id_for("root-0", "a.md")
        original.rename(self.root / "b.md")
        await index.reconcile()

        self.write("a.md", "# New\n\nBeta path reuse keyword.\n")
        report = await index.reconcile()
        self.assertEqual(1, report.added)
        by_path = {row["relative_path"]: row for row in self.store.sources.values()}
        self.assertEqual({"a.md", "b.md"}, set(by_path))
        self.assertEqual(original_id, by_path["b.md"]["source_id"])
        self.assertNotEqual(original_id, by_path["a.md"]["source_id"])
        outcome = await index.search("beta path reuse keyword")
        self.assertFalse(outcome.unknown)
        self.assertEqual("a.md", outcome.results[0].relative_path)

    async def test_move_between_roots_keeps_source_identity(self) -> None:
        other = self.tmp / "archive"
        other.mkdir()
        path = self.write("a.md", "# Title\n\nAlpha content here.\n")
        index = self.index([self.root, other])
        await index.reconcile()
        source_id = source_id_for("root-0", "a.md")
        target = other / "a.md"
        path.rename(target)
        report = await index.reconcile()
        self.assertEqual(1, report.moved)
        row = self.store.sources[source_id]
        self.assertEqual("root-1", row["root_key"])
        self.assertEqual(1, len([key for key in self.store.versions if key[0] == source_id]))

    async def test_same_content_in_two_paths_is_two_sources(self) -> None:
        content = "# Title\n\nAlpha content here.\n"
        self.write("a.md", content)
        self.write("b.md", content)
        index = self.index()
        report = await index.reconcile()
        self.assertEqual(2, report.added)
        self.assertEqual(2, len(self.store.sources))

    async def test_build_failure_keeps_previous_version_and_hash(self) -> None:
        path = self.write("a.md", "# Title\n\nAlpha content here.\n")
        index = self.index()
        await index.reconcile()
        source_id = source_id_for("root-0", "a.md")
        previous_version = self.store.sources[source_id]["active_version"]
        previous_hash = self.store.sources[source_id]["content_hash"]
        self.store.fail_activation = True
        path.write_text("# Title\n\nBeta content changed.\n", encoding="utf-8", newline="\n")
        report = await index.reconcile()
        self.assertEqual(0, report.versions_built)
        self.assertEqual(1, len(report.errors))
        row = self.store.sources[source_id]
        self.assertEqual(previous_version, row["active_version"])
        self.assertEqual(previous_hash, row["content_hash"])
        self.assertTrue(
            all(
                self.store.chunks[key]["text"] != "" for key in self.store.chunks
            )
        )
        self.assertEqual(
            1, len([key for key in self.store.versions if key[0] == source_id])
        )

    async def test_extraction_failure_is_recorded_and_does_not_break_index(self) -> None:
        path = self.write("a.md", "# Title\n\nAlpha content here.\n")
        index = self.index()
        await index.reconcile()
        source_id = source_id_for("root-0", "a.md")
        previous_hash = self.store.sources[source_id]["content_hash"]
        path.write_bytes(b"\xff\xfe\x00\x01binary")
        report = await index.reconcile()
        self.assertEqual(1, len(report.errors))
        self.assertEqual(previous_hash, self.store.sources[source_id]["content_hash"])

    async def test_concurrent_reconciliation_produces_one_active_version(self) -> None:
        for number in range(4):
            self.write(f"file-{number}.md", f"# T{number}\n\nContent {number}.\n")
        first = self.index()
        second = self.index()
        await asyncio.gather(first.reconcile(), second.reconcile())
        for number in range(4):
            source_id = source_id_for("root-0", f"file-{number}.md")
            row = self.store.sources[source_id]
            versions = [key for key in self.store.versions if key[0] == source_id]
            self.assertEqual(1, len(versions))
            self.assertEqual(versions[0][1], row["active_version"])

    async def test_embedding_more_than_provider_batch_limit_is_chunked(self) -> None:
        class RecordingStore(FakeKnowledgeStore):
            def __init__(self) -> None:
                super().__init__()
                self.heartbeat_calls = 0

            async def heartbeat_version(self, **kwargs: object) -> bool:
                self.heartbeat_calls += 1
                return await super().heartbeat_version(**kwargs)

        class RecordingEmbedder(DeterministicTestEmbeddingProvider):
            def __init__(self) -> None:
                super().__init__(dim=16)
                self.batch_sizes: list[int] = []

            async def embed(self, texts: list[str]) -> list[list[float]]:
                self.batch_sizes.append(len(texts))
                return await super().embed(texts)

        content = "\n\n".join(f"paragraph {number} unique" for number in range(700))
        self.write("large.txt", content)
        store = RecordingStore()
        self.store = store
        embedder = RecordingEmbedder()
        report = await self.index(embedder=embedder).reconcile()
        self.assertEqual(1, report.versions_built)
        self.assertGreater(len(embedder.batch_sizes), 1)
        self.assertLessEqual(max(embedder.batch_sizes), 16)
        self.assertGreaterEqual(store.heartbeat_calls, len(embedder.batch_sizes))

    async def test_scans_do_not_modify_source_files(self) -> None:
        path = self.write("a.md", "# Title\n\nAlpha content here.\n")
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        index = self.index()
        await index.reconcile()
        await index.reconcile()
        self.assertEqual(before, (path.read_bytes(), path.stat().st_mtime_ns))

    async def test_detect_changes_does_not_mutate_the_index(self) -> None:
        self.write("a.md", "# Title\n\nAlpha content here.\n")
        index = self.index()
        events = await index.detect_changes()
        self.assertEqual(1, events)
        self.assertEqual({}, self.store.sources)
        self.assertEqual([], list(self.store.chunks))

    async def test_detect_changes_reports_a_rename_as_one_move(self) -> None:
        path = self.write("a.md", "# Title\n\nRename event.\n")
        index = self.index()
        await index.reconcile()
        before = len(self.store.events)
        path.rename(self.root / "b.md")

        self.assertEqual(1, await index.detect_changes())
        emitted = self.store.events[before:]
        self.assertEqual(["knowledge.file_moved"], [row["event_type"] for row in emitted])

        # The authoritative reconciliation describes the same transition and
        # therefore reuses the event dedupe key instead of adding a duplicate.
        await index.reconcile()
        self.assertEqual(before + 1, len(self.store.events))

    async def test_detect_readd_uses_tombstone_generation_and_dedupes_reconcile(self) -> None:
        path = self.write("a.md", "# Title\n\nInitial event.\n")
        index = self.index()
        await index.reconcile()
        source_id = source_id_for("root-0", "a.md")
        path.unlink()
        await index.reconcile()
        deleted_generation = int(self.store.tombstones[source_id]["generation"])
        before = len(self.store.events)
        self.write("a.md", "# Title\n\nReappeared event.\n")

        self.assertEqual(1, await index.detect_changes())
        added = self.store.events[-1]
        self.assertEqual("knowledge.file_added", added["event_type"])
        self.assertEqual(
            deleted_generation + 1,
            int(str(added["dedupe_key"]).split("|")[3]),
        )
        await index.reconcile()
        self.assertEqual(before + 1, len(self.store.events))

    async def test_detect_reused_old_path_selects_the_collision_identity(self) -> None:
        path = self.write("a.md", "# Title\n\nOriginal identity.\n")
        index = self.index()
        await index.reconcile()
        original_id = source_id_for("root-0", "a.md")
        path.rename(self.root / "b.md")
        await index.reconcile()
        self.write("a.md", "# Title\n\nDistinct replacement.\n")
        before = len(self.store.events)

        self.assertEqual(1, await index.detect_changes())
        event = self.store.events[-1]
        self.assertEqual("knowledge.file_added", event["event_type"])
        self.assertNotEqual(original_id, event["source_id"])
        await index.reconcile()
        self.assertEqual(before + 1, len(self.store.events))

    async def test_unsupported_and_oversized_files_are_reported(self) -> None:
        self.write("big.txt", "x" * 2048)
        self.root.joinpath("binary.bin").write_bytes(b"\x00\x01\x02")
        index = KnowledgeIndex(
            self.store,  # type: ignore[arg-type]
            resolve_roots(parse_roots([{"path": str(self.root), "key": "root-0"}])),
            self.embedder,
            migrations=MIGRATIONS,
            max_file_bytes=1024,
            extension_id="personal.knowledge",
            extension_version="0.1.0",
        )
        report = await index.reconcile()
        self.assertEqual(0, report.added)
        self.assertGreaterEqual(len(report.errors), 1)

    async def test_fts_search_returns_verified_current_evidence(self) -> None:
        self.write("a.md", "# Title\n\nAlpha unique keyword.\n")
        index = self.index()
        await index.reconcile()
        outcome = await index.search("unique keyword")
        self.assertFalse(outcome.unknown)
        self.assertEqual(1, len(outcome.results))
        evaluated = await index.evaluate(outcome.results, outcome.scores)
        self.assertEqual("CURRENT", evaluated[0].status.value)
        self.assertEqual(outcome.results[0].text, evaluated[0].text)

    async def test_changed_source_is_not_reported_as_current(self) -> None:
        path = self.write("a.md", "# Title\n\nAlpha unique keyword.\n")
        index = self.index()
        await index.reconcile()
        outcome = await index.search("unique keyword")
        path.write_text("# Title\n\nDifferent now.\n", encoding="utf-8", newline="\n")
        evaluated = await index.evaluate(outcome.results, outcome.scores)
        self.assertEqual("STALE", evaluated[0].status.value)
        self.assertEqual("", evaluated[0].text, "stale evidence must not leak its body")
        self.assertTrue(
            any(event["event_type"] == "knowledge.file_modified" for event in self.store.events)
        )

    async def test_stale_evaluation_and_reconcile_share_one_transition_event(self) -> None:
        path = self.write("a.md", "# Title\n\nOriginal evidence.\n")
        index = self.index()
        await index.reconcile()
        outcome = await index.search("original evidence")
        before = len(self.store.events)
        path.write_text("# Title\n\nUpdated evidence.\n", encoding="utf-8", newline="\n")

        evaluated = await index.evaluate(outcome.results, outcome.scores)
        self.assertEqual("STALE", evaluated[0].status.value)
        self.assertEqual(before + 1, len(self.store.events))
        await index.reconcile()
        self.assertEqual(before + 1, len(self.store.events))

    async def test_deleted_source_is_not_reported_as_current(self) -> None:
        path = self.write("a.md", "# Title\n\nAlpha unique keyword.\n")
        index = self.index()
        await index.reconcile()
        outcome = await index.search("unique keyword")
        path.unlink()
        evaluated = await index.evaluate(outcome.results, outcome.scores)
        self.assertEqual("DELETED", evaluated[0].status.value)
        self.assertEqual("", evaluated[0].text)

    async def test_search_without_matches_is_unknown(self) -> None:
        self.write("a.md", "# Title\n\nAlpha content.\n")
        index = self.index()
        await index.reconcile()
        outcome = await index.search("nonexistentterm")
        self.assertTrue(outcome.unknown)
        self.assertEqual((), outcome.results)

    async def test_invalid_query_and_filters_are_rejected(self) -> None:
        index = self.index()
        with self.assertRaises(ValueError):
            await index.search("")
        with self.assertRaises(ValueError):
            await index.search("ok", limit=0)
        with self.assertRaises(ValueError):
            await index.search("ok", limit=51)
        with self.assertRaises(ValueError):
            await index.search("ok", filters={"unknown": ["x"]})

    async def test_first_build_failure_is_retried_without_content_change(self) -> None:
        self.write("a.md", "# Title\n\nUnique retry keyword.\n")
        index = self.index()
        self.store.fail_activation = True
        first = await index.reconcile()
        self.assertEqual(0, first.versions_built)
        source_id = source_id_for("root-0", "a.md")
        self.assertIsNone(self.store.sources[source_id]["active_version"])
        self.store.fail_activation = False
        second = await index.reconcile()
        self.assertEqual(1, second.versions_built)
        self.assertIsNotNone(self.store.sources[source_id]["active_version"])
        outcome = await index.search("unique retry keyword")
        self.assertFalse(outcome.unknown)

    async def test_removed_root_becomes_unsearchable_and_is_reconciled(self) -> None:
        self.write("a.md", "# Title\n\nUnique removed-root keyword.\n")
        first = self.index()
        await first.reconcile()
        source_id = source_id_for("root-0", "a.md")
        archive = self.tmp / "archive"
        archive.mkdir()
        second = self.index(roots=[archive], root_keys=["archive"])
        outcome = await second.search("unique removed-root keyword")
        self.assertTrue(outcome.unknown)
        report = await second.reconcile()
        self.assertEqual(1, report.deleted)
        self.assertEqual({}, self.store.sources)
        self.assertIn(source_id, self.store.tombstones)
        chunks = [key for key in self.store.chunks if key[0] == source_id]
        self.assertEqual([], chunks)

    async def test_cancelled_build_leaves_no_candidate_behind(self) -> None:
        class SlowEmbedder(DeterministicTestEmbeddingProvider):
            def __init__(self) -> None:
                super().__init__(dim=16)
                self.started = asyncio.Event()

            async def embed(self, texts: list[str]) -> list[list[float]]:
                self.started.set()
                await asyncio.sleep(30)
                return await super().embed(texts)

        self.write("a.md", "# Title\n\nCancelled build keyword.\n")
        embedder = SlowEmbedder()
        index = self.index(embedder=embedder)
        task = asyncio.create_task(index.reconcile())
        await asyncio.wait_for(embedder.started.wait(), timeout=5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        building = [key for key, row in self.store.versions.items() if row["state"] == "BUILDING"]
        self.assertEqual([], building)
        self.assertEqual([], list(self.store.chunks))

    async def test_activation_does_not_delete_other_building_candidates(self) -> None:
        self.write("a.md", "# Title\n\nActivation keyword.\n")
        index = self.index()
        await index.reconcile()
        source_id = source_id_for("root-0", "a.md")
        for version_id in ("b" * 64, "c" * 64):
            await self.store.begin_version(
                source_id=source_id,
                version_id=version_id,
                content_hash="1" * 64,
                extractor_name="line-paragraph",
                extractor_version="1.0.0",
                embedding_provider="none",
                embedding_model="",
                embedding_dim=0,
                embedding_version="none",
                built_by="other-run",
            )
        previous_active = self.store.sources[source_id]["active_version"]
        previous_generation = self.store.sources[source_id]["generation"]
        await self.store.activate_version(
            source_id=source_id,
            version_id="b" * 64,
            content_hash="1" * 64,
            size_bytes=1,
            mtime_ns=1,
            chunk_count=0,
            diagnostics=[],
            expected_active_version=previous_active,
            expected_root_key="root-0",
            expected_relative_path="a.md",
            expected_generation=previous_generation,
            built_by="other-run",
        )
        self.assertEqual("b" * 64, self.store.sources[source_id]["active_version"])
        self.assertIn((source_id, "c" * 64), self.store.versions)
        self.assertEqual("BUILDING", self.store.versions[(source_id, "c" * 64)]["state"])
        with self.assertRaises(RuntimeError):
            await self.store.activate_version(
                source_id=source_id,
                version_id="d" * 64,
                content_hash="2" * 64,
                size_bytes=1,
                mtime_ns=1,
                chunk_count=0,
                diagnostics=[],
                expected_active_version="b" * 64,
                built_by="other-run",
            )
        with self.assertRaises(RuntimeError):
            # A superseded build may not overwrite a newer active version.
            await self.store.activate_version(
                source_id=source_id,
                version_id="c" * 64,
                content_hash="2" * 64,
                size_bytes=1,
                mtime_ns=1,
                chunk_count=0,
                diagnostics=[],
                expected_active_version="z" * 64,
                built_by="other-run",
            )
        self.assertEqual("b" * 64, self.store.sources[source_id]["active_version"])

    async def test_activation_rejects_a_source_moved_after_the_build_snapshot(self) -> None:
        self.write("a.md", "# Title\n\nActivation snapshot.\n")
        index = self.index()
        await index.reconcile()
        source_id = source_id_for("root-0", "a.md")
        source = await self.store.get_source(source_id)
        assert source is not None
        version_id = "f" * 64
        self.assertTrue(
            await self.store.begin_version(
                source_id=source_id,
                version_id=version_id,
                content_hash="2" * 64,
                extractor_name="markdown-line-paragraph",
                extractor_version="1.0.0",
                embedding_provider="none",
                embedding_model="",
                embedding_dim=0,
                embedding_version="none",
                built_by="snapshot-run",
            )
        )
        moved = await self.store.move_source(
            source_id=source_id,
            root_key="root-0",
            relative_path="b.md",
            media_type="text/markdown",
            size_bytes=1,
            mtime_ns=1,
            expected_root_key=source.root_key,
            expected_relative_path=source.relative_path,
            expected_generation=source.generation,
        )
        self.assertIsNotNone(moved)
        with self.assertRaises(RuntimeError):
            await self.store.activate_version(
                source_id=source_id,
                version_id=version_id,
                content_hash="2" * 64,
                size_bytes=1,
                mtime_ns=1,
                chunk_count=0,
                diagnostics=[],
                expected_active_version=source.active_version,
                expected_root_key=source.root_key,
                expected_relative_path=source.relative_path,
                expected_generation=source.generation,
                built_by="snapshot-run",
            )
        self.assertEqual("b.md", self.store.sources[source_id]["relative_path"])
        self.assertNotEqual(version_id, self.store.sources[source_id]["active_version"])

    async def test_orphan_building_versions_are_cleaned_on_reconcile(self) -> None:
        self.write("a.md", "# Title\n\nOrphan keyword.\n")
        source_id = source_id_for("root-0", "a.md")
        await self.store.insert_source(
            source_id=source_id,
            root_key="root-0",
            relative_path="a.md",
            media_type="text/markdown",
            size_bytes=1,
            mtime_ns=1,
            content_hash="1" * 64,
        )
        await self.store.begin_version(
            source_id=source_id,
            version_id="e" * 64,
            content_hash="1" * 64,
            extractor_name="line-paragraph",
            extractor_version="1.0.0",
            embedding_provider="none",
            embedding_model="",
            embedding_dim=0,
            embedding_version="none",
            built_by="dead-run",
        )
        self.store.versions[(source_id, "e" * 64)]["heartbeat_at"] = (
            time.monotonic() - 4000
        )
        index = self.index()
        await index.reconcile()
        self.assertNotIn((source_id, "e" * 64), self.store.versions)

    async def test_embedding_identity_change_triggers_rebuild(self) -> None:
        self.write("a.md", "# Title\n\nIdentity keyword.\n")
        first = self.index(embedder=DeterministicTestEmbeddingProvider(dim=16, model="model-a"))
        await first.reconcile()
        source_id = source_id_for("root-0", "a.md")
        version_a = self.store.sources[source_id]["active_version"]
        second = self.index(embedder=DeterministicTestEmbeddingProvider(dim=8, model="model-b"))
        report = await second.reconcile()
        self.assertEqual(1, report.versions_built)
        version_b = self.store.sources[source_id]["active_version"]
        self.assertNotEqual(version_a, version_b)
        row = self.store.versions[(source_id, version_b)]
        self.assertEqual("model-b", row["embedding_model"])
        self.assertEqual(8, row["embedding_dim"])
        third = self.index(embedder=DeterministicTestEmbeddingProvider(dim=8, model="model-b"))
        again = await third.reconcile()
        self.assertEqual(1, again.unchanged)
        self.assertEqual(0, again.versions_built)

    async def test_vector_arm_is_disabled_until_the_new_identity_is_reconciled(self) -> None:
        self.write("a.md", "# Title\n\nIdentity gate keyword.\n")
        first = self.index(
            embedder=DeterministicTestEmbeddingProvider(dim=16, model="model-a")
        )
        await first.reconcile()
        second = self.index(
            embedder=DeterministicTestEmbeddingProvider(dim=16, model="model-b")
        )
        outcome = await second.search("identity gate keyword")
        self.assertFalse(outcome.unknown)
        self.assertEqual("disabled", outcome.vector_mode)
        self.assertEqual("model-b", self.store.vector_identities[-1].model)

    async def test_repeated_hash_transition_emits_a_new_event_each_time(self) -> None:
        path = self.write("a.md", "# Title\n\nState A.\n")
        index = self.index()
        initial = await index.reconcile()
        self.assertEqual(1, initial.events)
        counts: list[int] = []
        for body in (
            "# Title\n\nState B.\n",
            "# Title\n\nState A.\n",
            "# Title\n\nState B.\n",
        ):
            path.write_text(body, encoding="utf-8", newline="\n")
            counts.append((await index.reconcile()).events)
        self.assertEqual([1, 1, 1], counts)
        self.assertEqual(4, len(self.store.events))


class KnowledgeStoreBatchingTests(unittest.IsolatedAsyncioTestCase):
    async def test_vector_bytes_are_included_in_transaction_frame_budget(self) -> None:
        class RecordingData:
            def __init__(self) -> None:
                self.transaction_sizes: list[int] = []

            async def transaction(self, statements: object) -> dict[str, object]:
                encoded = json.dumps(
                    statements, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
                self.transaction_sizes.append(len(encoded))
                return {"results": []}

            async def execute(
                self, statement: str, parameters: object
            ) -> dict[str, object]:
                del statement, parameters
                return {"rowcount": 1, "rows": []}

        data = RecordingData()
        store = KnowledgeStore(data)  # type: ignore[arg-type]
        chunks = [
            Chunk(
                ordinal=index,
                text=f"chunk-{index}",
                locator=Locator(kind="line_range", start=index + 1, end=index + 1),
                content_hash=f"{index:064x}",
            )
            for index in range(20)
        ]
        embeddings = [[0.123456789] * 8192 for _ in chunks]

        await store.insert_chunks(
            source_id="source",
            version_id="a" * 64,
            chunks=chunks,
            embeddings=embeddings,
            built_by="batch-run",
        )

        self.assertGreater(len(data.transaction_sizes), 1)
        self.assertLessEqual(
            max(data.transaction_sizes), MAX_CHUNK_BATCH_BYTES + 32 * 1024
        )


if __name__ == "__main__":
    unittest.main()
