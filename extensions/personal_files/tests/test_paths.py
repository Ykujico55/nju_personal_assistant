"""Real temporary files exercise the local file authorization boundary."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from personal_files.errors import FilesError
from personal_files.paths import MAX_FILE_BYTES, FileAccess


class FileAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "documents"
        self.root.mkdir()
        self.state = self.base / "control"
        self.state.mkdir()
        self.config = {
            "state_directory": str(self.state),
            "roots": [
                {"key": "docs", "label": "Documents", "path": str(self.root), "mode": "read_write"}
            ],
        }
        self.access = FileAccess(self.config)

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(FilesError) as failure:
            function(*args, **kwargs)
        self.assertEqual(code, failure.exception.code)
        self.assertNotIn(str(self.base), str(failure.exception))

    def test_configuration_shared_and_stable_without_absolute_paths_in_public_view(self):
        restarted = FileAccess(self.config)
        self.assertEqual(self.access.configuration_hash, restarted.configuration_hash)
        self.assertEqual(
            [{"key": "docs", "label": "Documents", "mode": "read_write"}], self.access.roots_view()
        )
        self.assertNotIn(str(self.root), json.dumps(self.access.roots_view()))
        readonly = {**self.config, "roots": [{**self.config["roots"][0], "mode": "read_only"}]}
        self.assertNotEqual(self.access.configuration_hash, FileAccess(readonly).configuration_hash)

    def test_utf8_bom_and_crlf_round_trip_and_cas(self):
        text = "\ufeff标题\r\n正文\r\n"
        first = self.access.write("docs", "notes.md", text, None)
        self.assertEqual(text.encode("utf-8"), (self.root / "notes.md").read_bytes())
        self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), first.sha256)
        self.assertEqual(first, self.access.snapshot("docs", "notes.md"))
        second = self.access.write("docs", "notes.md", text + "更新", first.sha256)
        self.assertEqual(text + "更新", second.content)
        self.assert_code(
            "VERSION_CONFLICT", self.access.write, "docs", "notes.md", "stale", first.sha256
        )
        self.assert_code("VERSION_CONFLICT", self.access.write, "docs", "notes.md", "create", None)
        self.assertEqual(second.content, self.access.snapshot("docs", "notes.md").content)

    def test_manual_change_before_last_cas_does_not_get_overwritten_and_temp_is_removed(self):
        first = self.access.write("docs", "notes.txt", "base", None)
        original = self.access.snapshot
        reads = 0

        def edit_before_recheck(*args, **kwargs):
            nonlocal reads
            reads += 1
            if reads == 2:
                (self.root / "notes.txt").write_bytes(b"human edit")
            return original(*args, **kwargs)

        with patch.object(self.access, "snapshot", side_effect=edit_before_recheck):
            self.assert_code(
                "VERSION_CONFLICT",
                self.access.write,
                "docs",
                "notes.txt",
                "agent edit",
                first.sha256,
            )
        self.assertEqual(b"human edit", (self.root / "notes.txt").read_bytes())
        self.assertEqual(["notes.txt"], [path.name for path in self.root.iterdir()])

    def test_create_race_never_overwrites_an_appearing_file(self):
        original = self.access.snapshot
        reads = 0

        def create_before_recheck(*args, **kwargs):
            nonlocal reads
            reads += 1
            if reads == 2:
                (self.root / "new.txt").write_bytes(b"human")
            return original(*args, **kwargs)

        with patch.object(self.access, "snapshot", side_effect=create_before_recheck):
            self.assert_code(
                "VERSION_CONFLICT", self.access.write, "docs", "new.txt", "agent", None
            )
        self.assertEqual(b"human", (self.root / "new.txt").read_bytes())
        self.assertEqual(["new.txt"], [path.name for path in self.root.iterdir()])

    def test_read_only_root_cannot_write_or_delete(self):
        self.access.write("docs", "notes.txt", "kept", None)
        self.config["roots"][0]["mode"] = "read_only"
        access = FileAccess(self.config)
        current = access.snapshot("docs", "notes.txt")
        self.assert_code("READ_ONLY", access.write, "docs", "notes.txt", "bad", current.sha256)
        self.assert_code("READ_ONLY", access.delete, "docs", "notes.txt", current.sha256)
        self.assertEqual("kept", current.content)

    def test_special_paths_and_control_names_rejected_but_normal_dot_file_allowed(self):
        for path in [
            "../out.txt",
            "/out.txt",
            "C:\\out.txt",
            "a//b.txt",
            "./a.txt",
            "a/../b",
            "file.txt:secret",
            "NUL.txt",
            "COM1",
            "CONIN$",
            "trailing.",
            "space ",
            ".git/config",
            ".ssh/id_rsa",
            ".env",
            ".env.production",
            "a\x00b",
        ]:
            with self.subTest(path=path):
                self.assert_code("PATH_INVALID", self.access.snapshot, "docs", path, True)
        dot = self.access.write("docs", ".notes.md", "fine", None)
        self.assertEqual("fine", dot.content)

    def test_snapshot_rejects_invalid_utf8_nul_and_oversized_files(self):
        for name, data, code in [
            ("binary.txt", b"one\x00two", "TEXT_REQUIRED"),
            ("invalid.txt", b"\xff", "TEXT_REQUIRED"),
            ("big.txt", b"a" * (MAX_FILE_BYTES + 1), "FILE_TOO_LARGE"),
        ]:
            (self.root / name).write_bytes(data)
            with self.subTest(name=name):
                self.assert_code(code, self.access.snapshot, "docs", name)
        self.assert_code("TEXT_REQUIRED", self.access.write, "docs", "a.txt", "\ud800", None)
        self.assert_code(
            "FILE_TOO_LARGE", self.access.write, "docs", "a.txt", "a" * (MAX_FILE_BYTES + 1), None
        )
        self.assertIsNone(self.access.snapshot("docs", "missing", True))
        self.assert_code("FILE_NOT_FOUND", self.access.snapshot, "docs", "missing")

    def test_root_replacement_invalidates_existing_access_and_configuration_hash(self):
        old_hash = self.access.configuration_hash
        self.root.rename(self.base / "old-documents")
        self.root.mkdir()
        self.assert_code("ROOT_CHANGED", self.access.snapshot, "docs", "new.txt", True)
        self.assertNotEqual(old_hash, FileAccess(self.config).configuration_hash)

    def test_state_directory_identity_and_nonoverlap_are_checked(self):
        self.state.rename(self.base / "old-control")
        self.state.mkdir()
        self.assert_code("STATE_CHANGED", self.access.validate_state)
        for state in [self.root, self.root / "control", self.base]:
            with self.subTest(state=state):
                self.assert_code(
                    "CONFIG_INVALID", FileAccess, {**self.config, "state_directory": str(state)}
                )
        self.assertFalse((self.root / "control").exists())

    def test_configuration_rejects_overlapping_roots(self):
        inner = self.root / "inner"
        inner.mkdir()
        for path in [self.root, inner]:
            config = {
                **self.config,
                "roots": [
                    self.config["roots"][0],
                    {"key": "other", "label": "Other", "path": str(path), "mode": "read_only"},
                ],
            }
            with self.subTest(path=path):
                self.assert_code("CONFIG_INVALID", FileAccess, config)

    def test_hardlink_target_rejected_for_read_write_and_delete(self):
        outside = self.base / "outside.txt"
        outside.write_bytes(b"protected")
        os.link(outside, self.root / "linked.txt")
        for operation, args in [
            (self.access.snapshot, ("docs", "linked.txt")),
            (self.access.write, ("docs", "linked.txt", "bad", "0" * 64)),
            (self.access.delete, ("docs", "linked.txt", "0" * 64)),
        ]:
            with self.subTest(operation=operation.__name__):
                self.assert_code("PATH_INVALID", operation, *args)
        self.assertEqual(b"protected", outside.read_bytes())

    def test_symlink_components_are_rejected_before_resolving(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_bytes(b"protected")
        link = self.root / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            # Windows normally requires Developer Mode for native symlinks;
            # junction and reparse checks are also covered with injected stats.
            self.skipTest("Creating a native symlink requires Windows Developer Mode")
        self.assert_code("PATH_INVALID", self.access.snapshot, "docs", "linked/secret.txt")
        self.assert_code("PATH_INVALID", self.access.write, "docs", "linked/new.txt", "bad", None)
        config = {**self.config, "roots": [{**self.config["roots"][0], "path": str(link)}]}
        self.assert_code("CONFIG_INVALID", FileAccess, config)

    def test_read_replacement_between_check_and_open_returns_no_content(self):
        self.access.write("docs", "notes.txt", "authorized", None)
        import personal_files.paths as paths

        original = paths._open_read

        def replace_then_open(path):
            replacement = self.root / "replacement.txt"
            replacement.write_bytes(b"replacement")
            os.replace(replacement, self.root / "notes.txt")
            return original(path)

        with patch.object(paths, "_open_read", side_effect=replace_then_open):
            self.assert_code("FILE_CHANGED", self.access.snapshot, "docs", "notes.txt")

    def test_parent_replacement_before_commit_is_rejected(self):
        parent = self.root / "folder"
        parent.mkdir()
        original = self.access.snapshot
        reads = 0

        def move_parent(*args, **kwargs):
            nonlocal reads
            reads += 1
            if reads == 2:
                parent.rename(self.root / "old-folder")
                parent.mkdir()
            return original(*args, **kwargs)

        with patch.object(self.access, "snapshot", side_effect=move_parent):
            self.assert_code(
                "PATH_CHANGED", self.access.write, "docs", "folder/new.txt", "must not appear", None
            )
        self.assertEqual([], list(parent.iterdir()))
        self.assertEqual([], list((self.root / "old-folder").iterdir()))

    def test_list_is_paginated_and_omits_sensitive_entries(self):
        for name in ["a.txt", "b.md", ".notes.txt", ".env"]:
            (self.root / name).write_bytes(b"text")
        (self.root / "folder").mkdir()
        (self.root / ".git").mkdir()
        first = self.access.list("docs", limit=2)
        second = self.access.list("docs", offset=first["next_offset"], limit=2)
        self.assertEqual(2, first["next_offset"])
        self.assertIsNone(second["next_offset"])
        entries = first["entries"] + second["entries"]
        self.assertEqual(
            [".notes.txt", "a.txt", "b.md", "folder"], [entry["name"] for entry in entries]
        )
        self.assertEqual(2, first["skipped"])
        self.assertNotIn(str(self.root), json.dumps(first))
        self.assert_code("INVALID_PAGE", self.access.list, "docs", offset=True)
        self.assert_code("INVALID_PAGE", self.access.list, "docs", limit=10001)

    def test_missing_parent_not_created_and_delete_checks_exact_hash(self):
        self.assert_code(
            "FILE_NOT_FOUND", self.access.write, "docs", "missing/new.txt", "text", None
        )
        self.assertFalse((self.root / "missing").exists())
        current = self.access.write("docs", "notes.txt", "text", None)
        self.assert_code("VERSION_CONFLICT", self.access.delete, "docs", "notes.txt", "0" * 64)
        self.access.delete("docs", "notes.txt", current.sha256)
        self.assertFalse((self.root / "notes.txt").exists())

    def test_failed_replace_cleans_temporary_file_and_preserves_original(self):
        current = self.access.write("docs", "notes.txt", "original", None)
        with patch("personal_files.paths.os.replace", side_effect=PermissionError("private path")):
            self.assert_code(
                "FILE_UNAVAILABLE",
                self.access.write,
                "docs",
                "notes.txt",
                "replacement",
                current.sha256,
            )
        self.assertEqual(b"original", (self.root / "notes.txt").read_bytes())
        self.assertEqual(["notes.txt"], [path.name for path in self.root.iterdir()])

    def test_sqlite_state_rejects_hardlinks_and_non_regular_sidecars(self):
        original = self.base / "ledger-fixture"
        original.write_bytes(b"not a database")
        for name in [
            "files.sqlite3",
            "files.sqlite3-journal",
            "files.sqlite3-wal",
            "files.sqlite3-shm",
        ]:
            path = self.state / name
            with self.subTest(name=name):
                os.link(original, path)
                try:
                    self.assert_code("STATE_CHANGED", self.access.validate_state)
                finally:
                    path.unlink()
                path.mkdir()
                try:
                    self.assert_code("STATE_CHANGED", self.access.validate_state)
                finally:
                    path.rmdir()
        self.access.validate_state()

    def test_atomic_create_collision_at_publication_never_overwrites(self):
        import personal_files.paths as paths

        name = "rename" if os.name == "nt" else "link"
        original = getattr(paths.os, name)

        def competing_create(source, target, *args, **kwargs):
            Path(target).write_bytes(b"human last instant")
            return original(source, target, *args, **kwargs)

        with patch.object(paths.os, name, side_effect=competing_create):
            self.assert_code(
                "VERSION_CONFLICT", self.access.write, "docs", "collision.txt", "agent", None
            )
        self.assertEqual(b"human last instant", (self.root / "collision.txt").read_bytes())
        self.assertEqual(["collision.txt"], [path.name for path in self.root.iterdir()])

    def test_error_after_replacement_is_explicitly_unknown(self):
        current = self.access.write("docs", "notes.txt", "before", None)
        original = self.access.snapshot
        reads = 0

        def fail_after_replace(*args, **kwargs):
            nonlocal reads
            reads += 1
            if reads == 4:
                raise FilesError("FILE_CHANGED")
            return original(*args, **kwargs)

        with patch.object(self.access, "snapshot", side_effect=fail_after_replace):
            self.assert_code(
                "WRITE_RESULT_UNKNOWN",
                self.access.write,
                "docs",
                "notes.txt",
                "after",
                current.sha256,
            )
        self.assertEqual(b"after", (self.root / "notes.txt").read_bytes())
        self.assertEqual(["notes.txt"], [path.name for path in self.root.iterdir()])

    def test_wrong_hash_is_not_silently_treated_as_missing(self):
        self.assert_code("HASH_REQUIRED", self.access.write, "docs", "notes.txt", "text", "bad")
        self.assert_code(
            "VERSION_CONFLICT", self.access.write, "docs", "notes.txt", "text", "0" * 64
        )
        self.assertFalse((self.root / "notes.txt").exists())

    def test_read_initialization_never_creates_configured_state_directory(self):
        state = self.base / "missing-state"
        self.assert_code(
            "CONFIG_INVALID", FileAccess, {**self.config, "state_directory": str(state)}
        )
        self.assertFalse(state.exists())

    def test_target_key_binds_location_across_separators_config_and_recreation(self):
        folder = self.root / "Folder"
        folder.mkdir()
        first = self.access.target_key("docs", "Folder/Note.txt")
        self.assertEqual(first, self.access.target_key("docs", "Folder\\Note.txt"))
        self.assertEqual(64, len(first))
        alias = {
            **self.config,
            "roots": [{**self.config["roots"][0], "key": "renamed", "mode": "read_only"}],
        }
        self.assertEqual(first, FileAccess(alias).target_key("renamed", "Folder/Note.txt"))
        if os.name == "nt":
            self.assertEqual(first, self.access.target_key("docs", "folder/note.TXT"))
        created = self.access.write("docs", "Folder/Note.txt", "first", None)
        self.assertEqual(first, self.access.target_key("docs", "Folder/Note.txt"))
        self.access.delete("docs", "Folder/Note.txt", created.sha256)
        self.access.write("docs", "Folder/Note.txt", "replacement", None)
        self.assertEqual(first, self.access.target_key("docs", "Folder/Note.txt"))
        self.assertNotEqual(first, self.access.target_key("docs", "Folder/Other.txt"))
        self.assert_code("PATH_INVALID", self.access.target_key, "docs", "../outside.txt")

    @unittest.skipUnless(os.name == "nt", "Native Windows short-name regression")
    def test_missing_leaf_rejects_real_short_parent_before_any_write(self):
        import ctypes

        for name in ["Directory Long Name", ".codex"]:
            parent = self.root / name
            parent.mkdir()
            buffer = ctypes.create_unicode_buffer(32768)
            self.assertGreater(
                ctypes.windll.kernel32.GetShortPathNameW(str(parent), buffer, len(buffer)), 0
            )
            alias = Path(buffer.value).name
            self.assertNotEqual(alias.casefold(), parent.name.casefold())
            for operation, args in [
                (self.access.snapshot, ("docs", f"{alias}/new.txt", True)),
                (self.access.target_key, ("docs", f"{alias}/new.txt")),
                (self.access.write, ("docs", f"{alias}/new.txt", "must not appear", None)),
            ]:
                with self.subTest(name=name, operation=operation.__name__):
                    self.assert_code("PATH_INVALID", operation, *args)
                    self.assertEqual([], list(parent.iterdir()))

    @unittest.skipUnless(os.name == "nt", "Native Windows short-name regression")
    def test_configuration_rejects_real_short_alias_of_denied_root(self):
        import ctypes

        denied = self.root / ".codex"
        denied.mkdir()
        buffer = ctypes.create_unicode_buffer(32768)
        self.assertGreater(
            ctypes.windll.kernel32.GetShortPathNameW(str(denied), buffer, len(buffer)), 0
        )
        self.assertNotEqual(Path(buffer.value).name.casefold(), denied.name.casefold())
        config = {
            **self.config,
            "roots": [{**self.config["roots"][0], "path": buffer.value}],
        }
        self.assert_code("CONFIG_INVALID", FileAccess, config)

    def test_directory_create_is_single_level_and_can_hold_text(self):
        self.assertIsNone(self.access.directory_absent("docs", "folder"))
        self.assertFalse((self.root / "folder").exists())
        self.assertIsNone(self.access.mkdir("docs", "folder"))
        self.assertTrue((self.root / "folder").is_dir())
        self.access.mkdir("docs", "folder/child")
        self.access.write("docs", "folder/child/notes.txt", "text", None)
        self.assertEqual("text", self.access.snapshot("docs", "folder/child/notes.txt").content)
        self.assert_code("FILE_NOT_FOUND", self.access.mkdir, "docs", "missing/child")
        self.assertFalse((self.root / "missing").exists())

    def test_directory_creation_checks_authorization_and_invalid_paths(self):
        self.config["roots"][0]["mode"] = "read_only"
        readonly = FileAccess(self.config)
        for operation in [readonly.directory_absent, readonly.mkdir]:
            self.assert_code("READ_ONLY", operation, "docs", "child")
        for operation in [self.access.directory_absent, self.access.mkdir]:
            self.assert_code("ROOT_NOT_AUTHORIZED", operation, "other", "child")
            for path in ["", "../child", ".codex/child", ".env", "NUL", "child."]:
                self.assert_code("PATH_INVALID", operation, "docs", path)
        self.assertEqual([], list(self.root.iterdir()))

    def test_directory_creation_never_overwrites_existing_file_or_directory(self):
        (self.root / "file").write_bytes(b"kept")
        (self.root / "folder").mkdir()
        for operation in [self.access.directory_absent, self.access.mkdir]:
            for path in ["file", "folder"]:
                self.assert_code("VERSION_CONFLICT", operation, "docs", path)
        self.assertEqual(b"kept", (self.root / "file").read_bytes())
        self.assertEqual([], list((self.root / "folder").iterdir()))

    def test_directory_create_collision_never_overwrites(self):
        original = Path.mkdir

        def competing_create(path, *args, **kwargs):
            path.write_bytes(b"human last instant")
            return original(path, *args, **kwargs)

        with patch.object(Path, "mkdir", side_effect=competing_create, autospec=True):
            self.assert_code("VERSION_CONFLICT", self.access.mkdir, "docs", "child")
        self.assertEqual(b"human last instant", (self.root / "child").read_bytes())

    def test_directory_parent_replaced_before_create_is_rejected(self):
        parent = self.root / "folder"
        parent.mkdir()
        original = self.access._assert_parents
        replaced = False

        def replace_parent(root, parents):
            nonlocal replaced
            if not replaced:
                replaced = True
                parent.rename(self.root / "old-folder")
                parent.mkdir()
            return original(root, parents)

        with patch.object(self.access, "_assert_parents", side_effect=replace_parent):
            self.assert_code("PATH_CHANGED", self.access.mkdir, "docs", "folder/child")
        self.assertEqual([], list(parent.iterdir()))
        self.assertEqual([], list((self.root / "old-folder").iterdir()))

    def test_directory_parent_replaced_after_create_is_unknown(self):
        parent = self.root / "folder"
        parent.mkdir()
        original = Path.mkdir

        def replace_after_create(path, *args, **kwargs):
            original(path, *args, **kwargs)
            parent.rename(self.root / "old-folder")
            original(parent)

        with patch.object(Path, "mkdir", side_effect=replace_after_create, autospec=True):
            self.assert_code("WRITE_RESULT_UNKNOWN", self.access.mkdir, "docs", "folder/child")
        self.assertEqual([], list(parent.iterdir()))
        self.assertTrue((self.root / "old-folder" / "child").is_dir())

    def test_created_directory_replacement_before_final_check_is_unknown(self):
        original = self.access._route

        def replace_before_check(root, parts, **kwargs):
            if kwargs.get("directory"):
                (self.root / "child").rename(self.root / "old-child")
                (self.root / "child").mkdir()
            return original(root, parts, **kwargs)

        with patch.object(self.access, "_route", side_effect=replace_before_check):
            self.assert_code("WRITE_RESULT_UNKNOWN", self.access.mkdir, "docs", "child")
        self.assertTrue((self.root / "old-child").is_dir())
        self.assertTrue((self.root / "child").is_dir())

    def test_directory_creation_error_with_uncertain_effect_is_unknown(self):
        original = Path.mkdir

        def create_then_raise(path, *args, **kwargs):
            original(path, *args, **kwargs)
            raise OSError("synthetic failure after creation")

        with patch.object(Path, "mkdir", side_effect=create_then_raise, autospec=True):
            self.assert_code("WRITE_RESULT_UNKNOWN", self.access.mkdir, "docs", "child")
        self.assertTrue((self.root / "child").is_dir())

    @unittest.skipUnless(os.name == "nt", "Native Windows short-name regression")
    def test_directory_create_rejects_real_short_parent(self):
        import ctypes

        for name in ["Directory Long Name", ".codex"]:
            parent = self.root / name
            parent.mkdir()
            buffer = ctypes.create_unicode_buffer(32768)
            self.assertGreater(
                ctypes.windll.kernel32.GetShortPathNameW(str(parent), buffer, len(buffer)), 0
            )
            alias = Path(buffer.value).name
            self.assertNotEqual(alias.casefold(), parent.name.casefold())
            for operation in [self.access.directory_absent, self.access.mkdir]:
                with self.subTest(name=name, operation=operation.__name__):
                    self.assert_code("PATH_INVALID", operation, "docs", f"{alias}/child")
                    self.assertEqual([], list(parent.iterdir()))
