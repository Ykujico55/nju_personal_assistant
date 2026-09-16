"""Per-extension-version virtual environments and payload installation.

Installation runs only after the exact user confirmation.  The venv isolates the
extension's interpreter and dependencies; it is not a malicious-code sandbox.
Third-party locked dependencies are installed with the venv's own pip, while the
already-trusted SDK and the confirmed payload are installed from local files so
an offline host can always start the worker.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import sys
from pathlib import Path

from personal_assistant.core.extensions.async_utils import (
    run_blocking,
    shield_cleanup,
    terminate_process,
)
from personal_assistant.core.extensions.errors import ExtensionOperationError
from personal_assistant.core.extensions.lifecycle import (
    ArtifactStager,
    InstalledArtifact,
    StagedArtifact,
)
from personal_assistant.core.extensions.manifest import (
    IGNORED_ARTIFACT_PARTS,
    ExtensionManifest,
)
from personal_assistant.core.extensions.models import ExtensionRecord

_PTH_PREFIX = "personal_assistant_extension_"
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def _safe_component(value: str) -> str:
    return _SAFE_CHARS.sub("_", value)


def venv_python(venv_root: Path) -> Path:
    if sys.platform == "win32":
        return venv_root / "Scripts" / "python.exe"
    return venv_root / "bin" / "python"


def effective_requirements(lock_path: Path) -> tuple[str, ...]:
    """Return pinned requirement lines from a statically validated lockfile."""

    if not lock_path.is_file():
        return ()
    lines = []
    for raw in lock_path.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return tuple(lines)


class VenvArtifactInstaller:
    def __init__(
        self,
        *,
        install_root: Path,
        stager: ArtifactStager,
        python_executable: str | None = None,
        sdk_package: Path | None = None,
        venv_timeout_seconds: float = 180.0,
        dependency_timeout_seconds: float = 600.0,
    ) -> None:
        self._install_root = Path(install_root)
        self._stager = stager
        self._python = python_executable or sys.executable
        self._sdk_package = Path(sdk_package) if sdk_package else None
        self._venv_timeout = venv_timeout_seconds
        self._dependency_timeout = dependency_timeout_seconds

    @property
    def install_root(self) -> Path:
        return self._install_root

    async def install(
        self, staged: StagedArtifact, manifest: ExtensionManifest
    ) -> InstalledArtifact:
        destination = self._install_root / _safe_component(manifest.id) / manifest.version
        payload = destination / "payload"
        venv = destination / "venv"
        import_root = payload
        if destination.exists():
            raise ExtensionOperationError(
                "INSTALL_CONFLICT", f"version is already installed: {manifest.version}"
            )
        try:
            destination.mkdir(parents=True, exist_ok=False)
            # run_blocking waits for the copy thread on cancellation, so the
            # rollback below can never race a still-running thread.
            await run_blocking(self.copy_payload, staged.root, payload)
            source_root = payload / "src"
            import_root = source_root if source_root.is_dir() else payload
            lock_path = payload / manifest.dependency_lock
            requirements = await run_blocking(effective_requirements, lock_path)
            await self._create_venv(venv, with_pip=bool(requirements))
            purelib = await self._venv_purelib(venv)
            await run_blocking(self._install_sdk, purelib)
            pth = purelib / f"{_PTH_PREFIX}{_safe_component(manifest.id)}.pth"
            pth.write_text(f"{import_root}\n", encoding="utf-8")
            if requirements:
                # Hash-checking mode: every artifact must match a declared
                # sha256, and dependencies must themselves be pinned+hashed.
                await self._run(
                    [
                        str(venv_python(venv)),
                        "-m",
                        "pip",
                        "install",
                        "--no-input",
                        "--disable-pip-version-check",
                        "--require-hashes",
                        "-r",
                        str(lock_path),
                    ],
                    timeout=self._dependency_timeout,
                )
        except BaseException:
            # Cancellation must not leave a half-installed version directory.
            await shield_cleanup(asyncio.to_thread(shutil.rmtree, destination, True))
            raise
        # runtime_root is where extension.toml and schema files live (the payload
        # root); the import path (payload/src) is registered through the .pth file.
        return InstalledArtifact(
            install_path=destination,
            runtime_key=str(venv),
            runtime_root=payload,
        )

    async def uninstall_code(self, record: ExtensionRecord) -> None:
        """Delete every installed version of the extension."""

        if not record.install_path:
            return
        version_dir = Path(record.install_path).resolve()
        extension_dir = version_dir.parent
        root = self._install_root.resolve()
        if extension_dir == root or not extension_dir.is_relative_to(root):
            raise ExtensionOperationError(
                "UNINSTALL_PATH_UNSAFE", "refusing to delete outside the install root"
            )
        await run_blocking(shutil.rmtree, extension_dir, True)

    async def remove_version(self, record: ExtensionRecord) -> None:
        """Delete exactly one retained version (failed upgrade candidate cleanup)."""

        if not record.install_path:
            return
        version_dir = Path(record.install_path).resolve()
        root = self._install_root.resolve()
        if version_dir == root or not version_dir.is_relative_to(root):
            raise ExtensionOperationError(
                "UNINSTALL_PATH_UNSAFE", "refusing to delete outside the install root"
            )
        await run_blocking(shutil.rmtree, version_dir, True)

    async def clean_failed_install(self, staged: StagedArtifact) -> None:
        """Rollback of the version directory is owned by :meth:`install`.

        Discarding the staged copy is idempotent with the coordinator's cleanup.
        """

        await self._stager.discard(staged)

    # ----------------------------------------------------------------- private

    @staticmethod
    def copy_payload(source: Path, payload: Path) -> None:
        """Copy exactly the hashed/staged file set into the payload.

        Paths ignored by the artifact hash are never copied, so a tampered
        ``.venv``/``__pycache__`` entry cannot reach the installed payload.
        """

        payload.mkdir(parents=True, exist_ok=True)
        for entry in source.rglob("*"):
            relative = entry.relative_to(source)
            if any(part in IGNORED_ARTIFACT_PARTS for part in relative.parts):
                continue
            if entry.is_symlink():
                raise ExtensionOperationError(
                    "INSTALL_FAILED", "payload turned out to contain a symlink"
                )
            if entry.is_dir():
                (payload / relative).mkdir(parents=True, exist_ok=True)
            elif entry.is_file():
                destination = payload / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(entry, destination)

    async def _create_venv(self, venv: Path, *, with_pip: bool) -> None:
        argv = [self._python, "-m", "venv"]
        if not with_pip:
            argv.append("--without-pip")
        argv.append(str(venv))
        await self._run(argv, timeout=self._venv_timeout)

    async def _venv_purelib(self, venv: Path) -> Path:
        stdout = await self._run_capture(
            [
                str(venv_python(venv)),
                "-c",
                "import sysconfig; print(sysconfig.get_paths()['purelib'])",
            ],
            timeout=self._venv_timeout,
        )
        purelib = Path(stdout.strip())
        if not purelib.is_dir():
            raise ExtensionOperationError(
                "INSTALL_FAILED", "extension venv has no site-packages directory"
            )
        return purelib

    def _install_sdk(self, purelib: Path) -> None:
        package = self._sdk_package or _locate_sdk_package()
        target = purelib / "personal_assistant_sdk"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(
            package, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )

    async def _run(self, argv: list[str], *, timeout: float) -> None:
        await self._run_capture(argv, timeout=timeout, capture=False)

    async def _run_capture(
        self, argv: list[str], *, timeout: float, capture: bool = True
    ) -> str:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE if capture else asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout)
        except asyncio.CancelledError:
            # Reap the child before propagating the cancellation.
            await shield_cleanup(terminate_process(process))
            raise
        except TimeoutError:
            await shield_cleanup(terminate_process(process))
            raise ExtensionOperationError(
                "INSTALL_FAILED", "extension installation command timed out"
            ) from None
        if process.returncode != 0:
            # Raw installer output may contain host paths; only the code is persisted.
            raise ExtensionOperationError(
                "INSTALL_FAILED",
                f"extension installation command failed with exit code {process.returncode}",
            )
        return (stdout or b"").decode("utf-8", errors="replace") if capture else ""


def _locate_sdk_package() -> Path:
    import personal_assistant_sdk

    return Path(personal_assistant_sdk.__file__).resolve().parent


__all__ = ["VenvArtifactInstaller", "effective_requirements", "venv_python"]
