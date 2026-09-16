"""F02: controlled artifact staging is data-only and path-safe."""

from __future__ import annotations

import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from personal_assistant.core.extensions.errors import (
    ExtensionError,
    ManifestValidationError,
)
from personal_assistant.core.extensions.lifecycle import StagedArtifact
from personal_assistant.core.extensions.manifest import (
    ManifestParser,
    compute_artifact_hash,
)
from personal_assistant.infrastructure.extensions.staging import LocalArtifactStager

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "extensions" / "example_echo"


def _safe_rmtree(path: Path) -> None:
    resolved = path.resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if not resolved.is_relative_to(temp_root):
        raise AssertionError(f"refusing to delete a path outside the temp root: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


class LocalArtifactStagerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa_f02_stage_"))
        self.staging = self.tmp / "staging"
        self.staging.mkdir()
        self.stager = LocalArtifactStager(self.staging)

    def tearDown(self) -> None:
        _safe_rmtree(self.tmp)

    async def test_stages_an_isolated_copy_and_discard_only_removes_it(self) -> None:
        staged = await self.stager.stage(str(EXAMPLE))
        self.assertNotEqual(EXAMPLE.resolve(), staged.root.resolve())
        self.assertTrue(staged.root.is_dir())
        self.assertEqual(compute_artifact_hash(EXAMPLE), staged.artifact_hash)
        self.assertEqual(compute_artifact_hash(staged.root), staged.artifact_hash)
        self.assertFalse((staged.root / "src" / "example_echo" / "executed.marker").exists())

        await self.stager.discard(staged)
        self.assertFalse(staged.root.exists())
        self.assertTrue(EXAMPLE.is_dir())

    async def test_rejects_remote_and_unpinned_sources(self) -> None:
        for source in (
            "https://example.test/extension.zip",
            "http://example.test/extension.zip",
            "git+https://example.test/extension.git#main",
            "ssh://example.test/extension.git",
        ):
            with self.subTest(source=source), self.assertRaises(ManifestValidationError):
                await self.stager.stage(source)

    async def test_zip_archive_is_unpacked_inside_the_staging_root(self) -> None:
        archive = self.tmp / "example.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            for path in EXAMPLE.rglob("*"):
                if path.is_file() and "__pycache__" not in path.parts:
                    bundle.write(path, path.relative_to(EXAMPLE).as_posix())
        staged = await self.stager.stage(str(archive))
        self.assertTrue((staged.root / "extension.toml").is_file())
        self.assertEqual(compute_artifact_hash(EXAMPLE), staged.artifact_hash)

    async def test_zip_path_traversal_is_rejected_and_does_not_escape(self) -> None:
        archive = self.tmp / "evil.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("../escape.txt", "escaped")
            bundle.writestr("extension.toml", 'id = "evil.one"\n')
        with self.assertRaises(ManifestValidationError):
            await self.stager.stage(str(archive))
        self.assertFalse((self.tmp / "escape.txt").exists())
        self.assertFalse((self.staging.parent / "escape.txt").exists())

    async def test_symlinked_artifact_tree_is_rejected(self) -> None:
        copy = self.tmp / "linked"
        shutil.copytree(EXAMPLE, copy)
        outside = self.tmp / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        try:
            (copy / "outside.link").symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("this environment does not permit symlink creation")
        with self.assertRaises(ManifestValidationError):
            await self.stager.stage(str(copy))

    async def test_discard_refuses_paths_outside_the_staging_root(self) -> None:
        foreign = self.tmp / "foreign"
        foreign.mkdir()
        with self.assertRaises(ExtensionError):
            await self.stager.discard(
                StagedArtifact("foreign", foreign, compute_artifact_hash(foreign))
            )
        self.assertTrue(foreign.is_dir())

    async def test_ignored_paths_are_never_staged_hashed_or_installed(self) -> None:
        from personal_assistant.infrastructure.extensions.installer import (
            VenvArtifactInstaller,
        )

        source = self.tmp / "with_ignored"
        shutil.copytree(EXAMPLE, source)
        for relative in (".venv/evil.py", "__pycache__/evil.pyc", ".git/config"):
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("tampered", encoding="utf-8")

        staged = await self.stager.stage(str(source))
        self.assertFalse((staged.root / ".venv").exists())
        self.assertFalse((staged.root / "__pycache__").exists())
        self.assertFalse((staged.root / ".git").exists())

        # A tampered ignored path in the staged tree cannot change the confirmed
        # hash and must not reach the installed payload.
        (staged.root / ".venv").mkdir()
        (staged.root / ".venv" / "evil.py").write_text("late tamper", encoding="utf-8")
        self.assertEqual(staged.artifact_hash, compute_artifact_hash(staged.root))

        payload = self.tmp / "payload"
        VenvArtifactInstaller.copy_payload(staged.root, payload)
        self.assertFalse((payload / ".venv").exists())
        self.assertFalse((payload / "__pycache__").exists())
        self.assertTrue((payload / "extension.toml").is_file())

    async def test_zip_ignored_paths_are_not_extracted(self) -> None:
        archive = self.tmp / "ignored.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr(".venv/evil.py", "tamper")
            bundle.writestr("__pycache__/evil.pyc", "tamper")
            for path in EXAMPLE.rglob("*"):
                if path.is_file():
                    relative = path.relative_to(EXAMPLE).as_posix()
                    if any(part in {".git", ".venv", "__pycache__"} for part in path.parts):
                        continue
                    bundle.write(path, relative)
        staged = await self.stager.stage(str(archive))
        self.assertFalse((staged.root / ".venv").exists())
        self.assertFalse((staged.root / "__pycache__").exists())
        self.assertEqual(compute_artifact_hash(EXAMPLE), staged.artifact_hash)


class LockfileValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pa_f02_lock_"))

    def tearDown(self) -> None:
        _safe_rmtree(self.tmp)

    def _copy(self) -> Path:
        destination = self.tmp / f"copy-{len(list(self.tmp.iterdir()))}"
        shutil.copytree(EXAMPLE, destination)
        return destination

    def test_missing_lockfile_fails_static_validation(self) -> None:
        copy = self._copy()
        (copy / "requirements.lock").unlink()
        with self.assertRaises(ManifestValidationError):
            ManifestParser().parse(copy)

    def test_unpinned_and_remote_lock_entries_are_rejected(self) -> None:
        for line in (
            "requests>=2.0",
            "requests",
            "demo-package==1.*",
            "demo-package==1.2,!=1.2.5",
            "demo-package~=1.2",
            "-e git+https://example.test/pkg.git#main",
            "https://example.test/pkg-1.0.whl",
            "pkg @ https://example.test/pkg-1.0.whl",
            "pkg==1.0; python_version >= '3.12'",
            "pkg==1.0",
            "pkg==1.0 --hash=sha256:zzzz",
            "pkg==1.0 --hash=md5:".ljust(24, "a"),
        ):
            with self.subTest(line=line), self.assertRaises(ManifestValidationError):
                copy = self._copy()
                (copy / "requirements.lock").write_text(line + "\n", encoding="utf-8")
                ManifestParser().parse(copy)

    def test_hash_pinned_lockfile_is_accepted_including_continuations(self) -> None:
        digest = "a" * 64
        copy = self._copy()
        (copy / "requirements.lock").write_text(
            "# comment\n"
            f"requests==2.32.3 --hash=sha256:{digest}\n"
            f"demo-package==1.2.3 \\\n    --hash=sha256:{digest} \\\n"
            f"    --hash=sha256:{'b' * 64}\n",
            encoding="utf-8",
        )
        manifest = ManifestParser().parse(copy)
        self.assertEqual("requirements.lock", manifest.dependency_lock)

    def test_manifest_reference_cannot_escape_the_artifact_root(self) -> None:
        copy = self._copy()
        (self.tmp / "outside.lock").write_text("", encoding="utf-8")
        manifest_path = copy / "extension.toml"
        manifest_path.write_text(
            manifest_path.read_text("utf-8").replace(
                'dependency_lock = "requirements.lock"',
                'dependency_lock = "../outside.lock"',
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ManifestValidationError):
            ManifestParser().parse(copy)


if __name__ == "__main__":
    unittest.main()
