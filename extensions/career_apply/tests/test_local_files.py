"""Authorized local resume reads, stable snapshots, and private path boundaries."""
from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from career_apply.local_files import LocalResumeError, ResumeDirectory


class ResumeDirectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="career-local-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "resumes"
        self.root.mkdir()
        self.original = b"Public synthetic resume text.\n"
        (self.root / "resume.txt").write_bytes(self.original)
        self.config = [{"key": "cv", "path": str(self.root)}]

    def digest(self, data: bytes | None = None) -> str:
        return hashlib.sha256(self.original if data is None else data).hexdigest()

    def directory(self) -> ResumeDirectory:
        return ResumeDirectory(self.config)

    def read(self, directory: ResumeDirectory, path: str = "resume.txt") -> tuple[str, str, bytes]:
        return directory.read_file(
            root_key="cv", relative_path=path, expected_sha256=self.digest()
        )

    def test_listing_has_relative_metadata_without_contents_or_absolute_paths(self) -> None:
        directory = self.directory()
        listing = directory.list_files()
        self.assertEqual(False, listing["truncated"])
        self.assertEqual([{
            "root_key": "cv", "relative_path": "resume.txt", "filename": "resume.txt",
            "media_type": "text/plain", "size_bytes": len(self.original),
            "sha256": self.digest(),
        }], listing["files"])
        rendered = str(listing)
        self.assertNotIn(str(self.root), rendered)
        self.assertNotIn(self.original.decode(), rendered)

    def test_read_returns_identical_bytes_and_does_not_modify_source(self) -> None:
        path = self.root / "resume.txt"
        before = path.stat()
        self.assertEqual(("resume.txt", "text/plain", self.original), self.read(self.directory()))
        after = path.stat()
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)
        self.assertEqual(self.original, path.read_bytes())

    def test_supported_nested_paths_and_unicode_names(self) -> None:
        nested = self.root / "中文"
        nested.mkdir()
        (nested / "简历.txt").write_bytes(self.original)
        entries = self.directory().list_files()["files"]
        self.assertTrue(any(item["relative_path"] == "中文/简历.txt" for item in entries))
        self.assertEqual(self.original, self.read(self.directory(), r"中文\简历.txt")[2])

    def test_missing_authorization_never_defaults_to_working_directory(self) -> None:
        for value in (None, [], ""):
            with self.subTest(value=value), self.assertRaises(LocalResumeError) as caught:
                ResumeDirectory(value)
            self.assertEqual("RESUME_ROOTS_REQUIRED", caught.exception.code)

    def test_invalid_root_shapes_duplicates_overlaps_and_non_directories_fail(self) -> None:
        invalid = [
            {}, [{"key": "cv", "path": "relative"}],
            [{"key": "cv", "path": str(self.root / "resume.txt")}],
            self.config * 2,
            self.config + [{"key": "nested", "path": str(self.root / "sub")}],
            [{"key": "../bad", "path": str(self.root)}],
            [{"key": "cv", "path": str(self.root), "surprise": "ignored?"}],
            [{"key": str(i), "path": str(self.root)} for i in range(17)],
        ]
        (self.root / "sub").mkdir()
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(LocalResumeError):
                ResumeDirectory(value)

    def test_exact_size_boundary_remains_readable(self) -> None:
        data = b"x" * (512 * 1024)
        (self.root / "boundary.txt").write_bytes(data)
        result = self.directory().read_file(
            root_key="cv", relative_path="boundary.txt", expected_sha256=self.digest(data)
        )
        self.assertEqual(len(data), len(result[2]))

    @unittest.skipUnless(os.name == "nt", "Windows hidden attribute boundary")
    def test_windows_hidden_attribute_is_not_listed_or_read(self) -> None:
        import ctypes
        hidden = self.root / "hidden.txt"
        hidden.write_bytes(self.original)
        setter = ctypes.windll.kernel32.SetFileAttributesW
        setter.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        setter.restype = ctypes.c_int
        self.assertTrue(setter(str(hidden), 2))
        self.addCleanup(setter, str(hidden), 128)
        self.assertEqual(1, len(self.directory().list_files()["files"]))
        with self.assertRaises(LocalResumeError):
            self.read(self.directory(), "hidden.txt")

    def test_unconfigured_root_is_rejected(self) -> None:
        with self.assertRaises(LocalResumeError) as caught:
            self.directory().read_file(
                root_key="outside", relative_path="resume.txt", expected_sha256=self.digest()
            )
        self.assertEqual("RESUME_ROOT_NOT_AUTHORIZED", caught.exception.code)

    def test_traversal_absolute_windows_aliases_and_hidden_paths_are_rejected(self) -> None:
        invalid = (
            "../resume.txt", r"..\resume.txt", "/resume.txt", r"C:\resume.txt",
            "C:resume.txt", r"\\server\share\resume.txt", r"\\?\C:\resume.txt",
            "resume.txt:secret", "sub/../resume.txt", "./resume.txt", "sub//resume.txt",
            ".private/resume.txt", "resume.txt.", "resume.txt ", "NUL.txt", "CON",
            "re*sume.txt", "re?sume.txt", "bad\x00.txt", "",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(LocalResumeError):
                self.read(self.directory(), value)

    def test_digest_mismatch_and_invalid_digest_are_not_silently_imported(self) -> None:
        for digest in ("a" * 64, "", "bad", "A" * 64):
            with self.subTest(digest=digest), self.assertRaises(LocalResumeError):
                self.directory().read_file(
                    root_key="cv", relative_path="resume.txt", expected_sha256=digest
                )

    def test_hidden_unsupported_and_oversized_files_are_not_listed(self) -> None:
        (self.root / ".hidden.txt").write_bytes(self.original)
        (self.root / "secret.exe").write_bytes(self.original)
        (self.root / "huge.pdf").write_bytes(b"%PDF-" + b"x" * (512 * 1024))
        listing = self.directory().list_files()
        paths = [item["relative_path"] for item in listing["files"]]
        self.assertEqual(["resume.txt"], paths)
        self.assertEqual(3, listing["skipped"])
        for path in (".hidden.txt", "secret.exe", "huge.pdf"):
            with self.subTest(path=path), self.assertRaises(LocalResumeError):
                self.read(self.directory(), path)

    def test_limit_has_honest_truncation_and_scan_is_bounded(self) -> None:
        (self.root / "second.txt").write_bytes(self.original)
        listing = self.directory().list_files(limit=1)
        self.assertEqual(1, len(listing["files"]))
        self.assertTrue(listing["truncated"])
        with patch("career_apply.local_files.MAX_SCANNED_ENTRIES", 1):
            listing = self.directory().list_files(limit=100)
        self.assertTrue(listing["truncated"])
        self.assertLessEqual(len(listing["files"]), 1)
        for limit in (0, 101, True):
            with self.subTest(limit=limit), self.assertRaises(LocalResumeError):
                self.directory().list_files(limit=limit)

    def test_root_replacement_invalidates_old_authorization_even_if_bytes_match(self) -> None:
        directory = self.directory()
        self.root.rename(self.base / "old-resumes")
        self.root.mkdir()
        (self.root / "resume.txt").write_bytes(self.original)
        with self.assertRaises(LocalResumeError) as caught:
            self.read(directory)
        self.assertEqual("RESUME_ROOT_CHANGED", caught.exception.code)
        with self.assertRaises(LocalResumeError):
            directory.list_files()

    def test_file_replacement_at_open_is_rejected_before_read(self) -> None:
        directory = self.directory()
        file = self.root / "resume.txt"
        replacement = self.root / "new.txt"
        replacement.write_bytes(b"different private content")
        original_open = Path.open
        streams = []
        def replace_then_open(path: Path):  # type: ignore[no-untyped-def]
            os.replace(replacement, file)
            stream = original_open(path, "rb")
            streams.append(stream)
            return stream
        with (
            patch("career_apply.local_files._open_read", side_effect=replace_then_open),
            patch("career_apply.local_files._bounded_read") as read_bytes,
            self.assertRaises(LocalResumeError) as caught,
        ):
            self.read(directory)
        read_bytes.assert_not_called()
        self.assertEqual("RESUME_FILE_CHANGED", caught.exception.code)
        self.assertTrue(all(stream.closed for stream in streams))

    def test_file_growth_is_bounded_and_rejected(self) -> None:
        directory = self.directory()
        from career_apply import local_files
        original_read = local_files._bounded_read
        def grow_then_read(stream):  # type: ignore[no-untyped-def]
            with (self.root / "resume.txt").open("ab") as writer:
                writer.write(b"x" * (512 * 1024))
            return original_read(stream)
        with (
            patch("career_apply.local_files._bounded_read", side_effect=grow_then_read),
            self.assertRaises(LocalResumeError),
        ):
            self.read(directory)

    def test_os_error_does_not_leak_absolute_paths(self) -> None:
        private = str(self.root / "secret-private.txt")
        with (
            patch("career_apply.local_files._open_read", side_effect=OSError(private)),
            self.assertRaises(LocalResumeError) as caught,
        ):
            self.read(self.directory())
        self.assertNotIn(str(self.root), str(caught.exception))

    @unittest.skipUnless(os.name == "nt", "Windows junction boundary")
    def test_junction_root_or_nested_junction_cannot_escape(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "outside.txt").write_bytes(self.original)
        link = self.root / "linked"
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True, check=False,
        )
        if completed.returncode != 0:
            self.skipTest("junction creation is unavailable")
        with self.assertRaises(LocalResumeError):
            self.read(self.directory(), "linked/outside.txt")
        self.assertEqual(1, len(self.directory().list_files()["files"]))
        with self.assertRaises(LocalResumeError):
            ResumeDirectory([{"key": "link", "path": str(link)}])


if __name__ == "__main__":
    unittest.main()
