"""Regression: extension installation under a non-ASCII host path.

Windows child processes print text stdout in the ANSI code page when it is a
pipe; the installer must not decode that output as UTF-8, and the generated
``.pth`` must still resolve the payload so the real Worker starts.  A host whose
repository path contains CJK characters (a common Windows setup) previously hit
``INSTALL_FAILED`` here.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from personal_assistant.core.extensions.manifest import ManifestParser
from personal_assistant.infrastructure.extensions.installer import VenvArtifactInstaller
from personal_assistant.infrastructure.extensions.processes import ProcessContractVerifier
from personal_assistant.infrastructure.extensions.staging import LocalArtifactStager

ROOT = Path(__file__).resolve().parents[2]
EXTENSION = ROOT / "extensions" / "example_echo"


class NonAsciiInstallPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_install_and_verify_under_a_non_ascii_root(self) -> None:
        base = Path(tempfile.mkdtemp(prefix="pa_f07_路径_"))
        root = base / "扩展安装目录"
        root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, base, True)
        manifest = ManifestParser().parse(EXTENSION)
        stager = LocalArtifactStager(root / "暂存")
        staged = await stager.stage(str(EXTENSION))
        installer = VenvArtifactInstaller(install_root=root / "已安装", stager=stager)
        installed = await installer.install(staged, manifest)
        self.assertIn("路径", str(installed.install_path))
        self.assertTrue((installed.install_path / "venv").is_dir())
        # Contract verification starts the real Worker from the venv, so this
        # also proves the .pth resolves the payload under the CJK path.
        await ProcessContractVerifier().verify(installed, manifest)


if __name__ == "__main__":
    unittest.main()
