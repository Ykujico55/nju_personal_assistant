"""Temporary text candidates only; no generated candidate is imported or executed."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from plugin_developer.workspace import Workspace, WorkspaceError


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "managed"
        self.root.mkdir()
        self.workspace = Workspace(self.root)
        self.candidate = self.workspace.create("fixture.notes")

    def digest(self, text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def test_scaffold_is_sdk_only_and_review_has_stable_digest(self):
        shown = self.workspace.review(self.candidate)
        self.assertEqual("fixture.notes", shown["extension_id"])
        self.assertEqual("0.1.0", shown["version"])
        self.assertIn("extension.toml", [item["path"] for item in shown["files"]])
        self.assertEqual([], shown["diff"])
        self.assertEqual(
            shown["candidate_hash"], Workspace(self.root).review(self.candidate)["candidate_hash"]
        )
        source = self.workspace.read(self.candidate, "src/fixture_notes/worker.py")
        self.assertIn("personal_assistant_sdk", source)
        self.assertNotIn("personal_assistant.core", source)
        self.assertNotIn(str(self.root), json.dumps(shown))

    def test_explicit_version_and_random_candidate_ids(self):
        second = self.workspace.create("fixture.notes", "0.2.0")
        self.assertNotEqual(self.candidate, second)
        self.assertEqual("0.2.0", self.workspace.review(second)["version"])
        self.assertEqual(2, len(self.workspace.list_candidates()))
        for extension_id, version in [
            ("../bad", "0.1.0"),
            ("Fixture.bad", "0.1.0"),
            ("fixture.bad", "latest"),
        ]:
            with self.subTest(extension_id=extension_id), self.assertRaises(WorkspaceError):
                self.workspace.create(extension_id, version)

    def test_refuses_nonempty_unmanaged_root_and_relative_root(self):
        occupied = Path(self.temp.name) / "original-repository"
        occupied.mkdir()
        (occupied / "README.md").write_text("original", encoding="utf-8")
        with self.assertRaises(WorkspaceError):
            Workspace(occupied)
        with self.assertRaises(WorkspaceError):
            Workspace(Path("relative-root"))
        self.assertEqual("original", (occupied / "README.md").read_text())

    def test_new_and_existing_writes_require_explicit_compare_and_swap(self):
        result = self.workspace.write(self.candidate, "tests/test_ping.py", "def test_ok(): pass\n")
        self.assertEqual(self.digest("def test_ok(): pass\n"), result["sha256"])
        for stale in [None, "0" * 64]:
            with self.subTest(stale=stale), self.assertRaises(WorkspaceError):
                self.workspace.write(self.candidate, "tests/test_ping.py", "replaced\n", stale)
        changed = self.workspace.write(
            self.candidate, "tests/test_ping.py", "replaced\n", result["sha256"]
        )
        self.assertEqual(self.digest("replaced\n"), changed["sha256"])

    def test_diff_and_baseline_survive_restart(self):
        original = self.workspace.read(self.candidate, "README.md")
        old = self.workspace.review(self.candidate)["candidate_hash"]
        self.workspace.write(
            self.candidate, "README.md", original + "\nAdded details.\n", self.digest(original)
        )
        restarted = Workspace(self.root)
        shown = restarted.review(self.candidate)
        self.assertNotEqual(old, shown["candidate_hash"])
        self.assertEqual("README.md", shown["diff"][0]["path"])
        self.assertIn("+Added details.", shown["diff"][0]["diff"])

    def test_concurrent_cas_only_one_writer_wins(self):
        original = self.workspace.read(self.candidate, "README.md")

        def change(content):
            try:
                Workspace(self.root).write(
                    self.candidate, "README.md", content, self.digest(original)
                )
                return True
            except WorkspaceError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(change, ["first\n", "second\n"]))
        self.assertEqual(1, sum(results))

    def test_paths_are_relative_supported_text_and_cannot_reach_control(self):
        for path in [
            "../records/store.sqlite3",
            "/tmp/file.py",
            "C:\\secret.py",
            "//server/file.py",
            "file.txt:ads",
            ".git/config",
            "nested/../file.py",
            "file.py ",
            "CON.py",
            "image.png",
            "tests/.hidden.py",
            "tests//test.py",
            "tests/./test.py",
        ]:
            with self.subTest(path=path), self.assertRaises(WorkspaceError):
                self.workspace.write(self.candidate, path, "contents")
        with self.assertRaises(WorkspaceError):
            self.workspace.read("../records", "README.md")

    def test_case_only_aliases_are_rejected_on_every_platform(self):
        with self.assertRaises(WorkspaceError):
            self.workspace.write(self.candidate, "readme.md", "alias\n")
        self.assertNotEqual("alias\n", self.workspace.read(self.candidate, "README.md"))

    def test_binary_and_large_content_do_not_modify_candidate(self):
        baseline = self.workspace.review(self.candidate)["candidate_hash"]
        for text in ["bad\x00value", "x" * (512 * 1024 + 1)]:
            with self.subTest(size=len(text)), self.assertRaises(WorkspaceError):
                self.workspace.write(self.candidate, "large.txt", text)
        self.assertEqual(baseline, self.workspace.review(self.candidate)["candidate_hash"])

    def test_whole_candidate_size_limit_rejects_without_partial_write(self):
        for index in range(3):
            self.workspace.write(self.candidate, f"large-{index}.txt", "x" * (512 * 1024))
        before = self.workspace.review(self.candidate)["candidate_hash"]
        with self.assertRaises(WorkspaceError):
            self.workspace.write(self.candidate, "large-fourth.txt", "x" * (512 * 1024))
        self.assertEqual(before, self.workspace.review(self.candidate)["candidate_hash"])

    def test_tree_limit_rejects_without_partial_write(self):
        current = len(self.workspace.tree(self.candidate))
        for index in range(100 - current):
            self.workspace.write(self.candidate, f"file-{index}.txt", "text")
        before = self.workspace.review(self.candidate)["candidate_hash"]
        with self.assertRaises(WorkspaceError):
            self.workspace.write(self.candidate, "one-too-many.txt", "text")
        self.assertEqual(before, self.workspace.review(self.candidate)["candidate_hash"])

    def test_root_replacement_is_rejected(self):
        moved = self.root.with_name("moved")
        self.root.rename(moved)
        self.root.mkdir()
        with self.assertRaises(WorkspaceError):
            self.workspace.tree(self.candidate)

    def test_changed_manifest_identity_is_rejected(self):
        original = self.workspace.read(self.candidate, "extension.toml")
        with self.assertRaises(WorkspaceError):
            self.workspace.write(
                self.candidate,
                "extension.toml",
                original.replace("fixture.notes", "other.notes"),
                self.digest(original),
            )
        self.assertEqual(original, self.workspace.read(self.candidate, "extension.toml"))

    def test_export_is_exact_immutable_material_and_omits_control_records(self):
        shown = self.workspace.review(self.candidate)
        exported = self.workspace.export(self.candidate, shown["candidate_hash"])
        self.assertTrue(exported.is_relative_to(self.root / "releases"))
        actual = sorted(
            path.relative_to(exported).as_posix() for path in exported.rglob("*") if path.is_file()
        )
        self.assertEqual(sorted(item["path"] for item in shown["files"]), actual)
        self.assertFalse(any("records" in name or "sqlite" in name for name in actual))
        self.assertEqual(exported, self.workspace.export(self.candidate, shown["candidate_hash"]))
        self.workspace.write(self.candidate, "new.txt", "changed")
        with self.assertRaises(WorkspaceError):
            self.workspace.export(self.candidate, shown["candidate_hash"])
        self.assertFalse((exported / "new.txt").exists())

    def test_export_rejects_tampered_existing_release(self):
        shown = self.workspace.review(self.candidate)
        exported = self.workspace.export(self.candidate, shown["candidate_hash"])
        (exported / "README.md").write_text("tampered", encoding="utf-8")
        with self.assertRaises(WorkspaceError):
            self.workspace.export(self.candidate, shown["candidate_hash"])

    def test_static_symlink_cannot_escape(self):
        outside = Path(self.temp.name) / "outside.txt"
        outside.write_text("private", encoding="utf-8")
        link = self.workspace.candidate_path(self.candidate) / "linked.txt"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("OS symlink creation unavailable")
        with self.assertRaises(WorkspaceError):
            self.workspace.read(self.candidate, "linked.txt")
        with self.assertRaises(WorkspaceError):
            self.workspace.write(self.candidate, "linked.txt", "overwrite", self.digest("private"))
        self.assertEqual("private", outside.read_text())

    def test_errors_never_include_absolute_workspace(self):
        try:
            self.workspace.read(self.candidate, "missing.py")
        except WorkspaceError as error:
            self.assertNotIn(str(self.root), str(error))
        else:
            self.fail("missing file was accepted")

    def test_scaffold_has_real_discoverable_contract_tests(self):
        source = self.workspace.read(self.candidate, "tests/test_basic.py")
        self.assertIn("unittest.IsolatedAsyncioTestCase", source)
        self.assertIn("await worker.invoke", source)
        self.assertIn("Outcome.SUCCEEDED", source)
        self.assertNotIn("assert 1 + 1 == 2", source)
