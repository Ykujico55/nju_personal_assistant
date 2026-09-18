"""F05: authorized root parsing, traversal and path-safety counterexamples."""

from __future__ import annotations

import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from personal_knowledge.paths import (
    AuthorizedRoot,
    PathSafetyError,
    RootConfigurationError,
    hash_bytes,
    is_within,
    media_type_for,
    parse_roots,
    resolve_roots,
    safe_join,
    safe_read_bytes,
    walk_root,
)


class RootConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa_f05_roots_"))
        self.root = self.tmp / "notes"
        self.root.mkdir()
        (self.root / "a.md").write_text("# A\n\nbody\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_valid_root_is_resolved(self) -> None:
        specs = parse_roots([{"path": str(self.root), "key": "notes", "label": "Notes"}])
        self.assertEqual("notes", specs[0].key)
        roots = resolve_roots(specs)
        self.assertEqual(self.root.resolve(), roots[0].path)

    def test_missing_path_is_rejected(self) -> None:
        with self.assertRaises(RootConfigurationError):
            parse_roots([{"path": str(self.tmp / "missing")}])

    def test_file_instead_of_directory_is_rejected(self) -> None:
        with self.assertRaises(RootConfigurationError):
            parse_roots([{"path": str(self.root / "a.md")}])

    def test_empty_roots_are_rejected(self) -> None:
        with self.assertRaises(RootConfigurationError):
            parse_roots([])

    def test_non_array_roots_are_rejected(self) -> None:
        with self.assertRaises(RootConfigurationError):
            parse_roots("C:/notes")

    def test_overlapping_roots_are_rejected(self) -> None:
        nested = self.root / "sub"
        nested.mkdir()
        with self.assertRaises(RootConfigurationError) as captured:
            parse_roots([{"path": str(self.root)}, {"path": str(nested)}])
        self.assertEqual("roots_overlap", captured.exception.code)

    def test_duplicate_keys_are_rejected(self) -> None:
        other = self.tmp / "other"
        other.mkdir()
        with self.assertRaises(RootConfigurationError):
            parse_roots(
                [
                    {"path": str(self.root), "key": "same"},
                    {"path": str(other), "key": "same"},
                ]
            )


class PathSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa_f05_paths_"))
        self.root = self.tmp / "notes"
        self.root.mkdir()
        (self.root / "keep.md").write_text("keep\n", encoding="utf-8", newline="\n")
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        (self.outside / "secret.txt").write_text("secret\n", encoding="utf-8", newline="\n")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_traversal_is_rejected(self) -> None:
        for relative in ("../outside/secret.txt", "..\\outside\\secret.txt", "a/../../x"):
            with self.assertRaises(PathSafetyError) as captured:
                safe_join(self.root, relative)
            self.assertEqual("path_traversal", captured.exception.code)

    def test_absolute_paths_are_rejected(self) -> None:
        for relative in (str(self.outside / "secret.txt"), "/etc/passwd", "C:/Windows/win.ini"):
            with self.assertRaises(PathSafetyError):
                safe_join(self.root, relative)

    def test_valid_relative_path_resolves(self) -> None:
        resolved = safe_join(self.root, "keep.md")
        self.assertEqual((self.root / "keep.md").resolve(), resolved)
        self.assertEqual("keep\n", safe_read_bytes(self.root, "keep.md").decode("utf-8"))

    def test_case_only_escape_is_rejected_on_windows(self) -> None:
        if os.name != "nt":
            self.skipTest("case-insensitive containment is a Windows concern")
        resolved = self.root.resolve()
        other_case = Path(str(resolved).upper())
        self.assertFalse(is_within(resolved, other_case.parent.parent / "elsewhere"))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_symlink_escape_is_rejected(self) -> None:
        link = self.root / "escape.txt"
        try:
            link.symlink_to(self.outside / "secret.txt")
        except OSError as exc:
            self.skipTest(f"cannot create a symlink here: {exc}")
        with self.assertRaises(PathSafetyError):
            safe_read_bytes(self.root, "escape.txt")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_in_root_symlink_directory_is_not_followed_by_walk(self) -> None:
        link = self.root / "linked"
        try:
            link.symlink_to(self.outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"cannot create a symlink here: {exc}")
        roots = resolve_roots(parse_roots([{"path": str(self.root)}]))
        result = walk_root(roots[0])
        self.assertEqual(
            ["keep.md"], [item.relative_path for item in result.files]
        )

    def test_walk_reports_allowed_media_types_only(self) -> None:
        (self.root / "b.txt").write_text("b\n", encoding="utf-8")
        (self.root / "c.pdf").write_bytes(b"%PDF-1.4\n")
        (self.root / "ignored.bin").write_bytes(b"\x00\x01")
        (self.root / "sub").mkdir()
        (self.root / "sub" / "d.md").write_text("d\n", encoding="utf-8")
        roots = resolve_roots(parse_roots([{"path": str(self.root)}]))
        result = walk_root(roots[0])
        self.assertEqual(
            ["b.txt", "c.pdf", "keep.md", "sub/d.md"],
            [item.relative_path for item in result.files],
        )

    def test_hidden_entries_are_skipped_by_default(self) -> None:
        (self.root / ".hidden.md").write_text("hidden\n", encoding="utf-8")
        roots = resolve_roots(parse_roots([{"path": str(self.root)}]))
        default = walk_root(roots[0])
        self.assertNotIn(".hidden.md", [item.relative_path for item in default.files])
        included = walk_root(roots[0], include_hidden=True)
        self.assertIn(".hidden.md", [item.relative_path for item in included.files])

    def test_read_bytes_does_not_modify_the_source(self) -> None:
        target = self.root / "keep.md"
        before = (target.read_bytes(), target.stat().st_mtime_ns)
        safe_read_bytes(self.root, "keep.md")
        after = (target.read_bytes(), target.stat().st_mtime_ns)
        self.assertEqual(before, after)

    def test_read_limit_is_enforced_on_bytes_actually_read(self) -> None:
        target = self.root / "growing.txt"
        target.write_bytes(b"small")
        with patch(
            "builtins.open", return_value=io.BytesIO(b"x" * 65)
        ), self.assertRaises(PathSafetyError) as captured:
            safe_read_bytes(self.root, "growing.txt", max_bytes=64)
        self.assertEqual("file_too_large", captured.exception.code)

    def test_post_read_check_re_resolves_the_path(self) -> None:
        outside = self.tmp / "outside.md"
        outside.write_text("outside secret", encoding="utf-8")
        inside = self.root / "keep.md"
        with patch(
            "personal_knowledge.paths.safe_join", side_effect=[inside, outside]
        ), self.assertRaises(PathSafetyError) as captured:
            safe_read_bytes(self.root, "keep.md", max_bytes=1024)
        self.assertEqual("path_escape", captured.exception.code)

    def test_media_type_mapping(self) -> None:
        self.assertEqual("text/markdown", media_type_for("x/y/note.MD"))
        self.assertEqual("text/plain", media_type_for("a.txt"))
        self.assertEqual("application/pdf", media_type_for("a.pdf"))
        self.assertIsNone(media_type_for("a.exe"))

    def test_hash_is_sha256_hex(self) -> None:
        digest = hash_bytes(b"abc")
        self.assertEqual(64, len(digest))
        self.assertEqual("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", digest)

    def test_authorized_root_contains_helper(self) -> None:
        root = AuthorizedRoot(key="notes", path=self.root.resolve())
        self.assertTrue(root.contains(self.root / "keep.md"))
        self.assertFalse(root.contains(self.outside))


if __name__ == "__main__":
    unittest.main()
