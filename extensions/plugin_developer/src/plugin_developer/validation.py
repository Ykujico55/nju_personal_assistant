"""Static inspection and explicitly authorized local unittest validation.

Generated code executes as the current OS user.  Snapshotting, environment
scrubbing and a process group/job aid reproducibility and cleanup; they are not
an untrusted-code sandbox and do not deny network or credential-store access.
"""

from __future__ import annotations

import ast
import ctypes
import hashlib
import importlib.util
import json
import math
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import uuid
from contextlib import suppress
from pathlib import Path
from typing import IO, Any

MAX_FILE_BYTES = 512 * 1024
MAX_CANDIDATE_BYTES = 2 * 1024 * 1024
MAX_FILES = 100
MAX_LOG_BYTES = 64 * 1024
_SUFFIXES = {".py", ".toml", ".json", ".md", ".txt", ".lock"}
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_PIN = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9_,.-]+\])?)==([A-Za-z0-9.+_-]+)")
_RESERVED = re.compile(r"(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)


class ValidationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _links(path: Path) -> None:
    for part in (*reversed(path.parents), path):
        current = part.lstat()
        if stat.S_ISLNK(current.st_mode) or (
            getattr(current, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValidationError("CANDIDATE_PATH_INVALID")


def _parts(value: str) -> None:
    for part in value.split("/"):
        if (
            not part
            or part.startswith(".")
            or part != part.rstrip(" .")
            or _RESERVED.match(part)
            or any(ord(char) < 32 or char in '<>:"|?*\\' for char in part)
        ):
            raise ValidationError("CANDIDATE_PATH_INVALID")


def _version(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _capture(root: Path) -> dict[str, bytes]:
    root = Path(root)
    try:
        if not root.is_absolute():
            root = root.absolute()
        _links(root)
        start = root.stat()
        if not stat.S_ISDIR(start.st_mode):
            raise ValidationError("CANDIDATE_UNAVAILABLE")
        pending = [root]
        data: dict[str, bytes] = {}
        seen: set[str] = set()
        total = 0
        directories = 0
        while pending:
            directory = pending.pop()
            directories += 1
            if directories > 100:
                raise ValidationError("CANDIDATE_TOO_LARGE")
            _links(directory)
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    relative = path.relative_to(root).as_posix()
                    _parts(relative)
                    if relative.casefold() in seen:
                        raise ValidationError("CANDIDATE_PATH_INVALID")
                    seen.add(relative.casefold())
                    before = path.lstat()
                    if stat.S_ISLNK(before.st_mode) or getattr(before, "st_file_attributes", 0) & (
                        stat.FILE_ATTRIBUTE_REPARSE_POINT | stat.FILE_ATTRIBUTE_HIDDEN
                    ):
                        raise ValidationError("CANDIDATE_PATH_INVALID")
                    if stat.S_ISDIR(before.st_mode):
                        pending.append(path)
                        continue
                    if (
                        not stat.S_ISREG(before.st_mode)
                        or path.suffix.lower() not in _SUFFIXES
                        or len(data) >= MAX_FILES
                        or before.st_size > MAX_FILE_BYTES
                    ):
                        raise ValidationError("CANDIDATE_TOO_LARGE")
                    _links(path)
                    with path.open("rb") as stream:
                        opened = os.fstat(stream.fileno())
                        if _version(opened) != _version(before):
                            raise ValidationError("CANDIDATE_CHANGED")
                        content = stream.read(MAX_FILE_BYTES + 1)
                        after = os.fstat(stream.fileno())
                    _links(path)
                    current = path.stat()
                    if (
                        len(content) > MAX_FILE_BYTES
                        or len(content) != before.st_size
                        or _version(after) != _version(before)
                        or _version(current) != _version(before)
                    ):
                        raise ValidationError("CANDIDATE_CHANGED")
                    content.decode("utf-8")
                    if b"\x00" in content:
                        raise ValidationError("CANDIDATE_BINARY_REJECTED")
                    total += len(content)
                    if total > MAX_CANDIDATE_BYTES:
                        raise ValidationError("CANDIDATE_TOO_LARGE")
                    data[relative] = content
        _links(root)
        current = root.stat()
        if (start.st_dev, start.st_ino) != (current.st_dev, current.st_ino):
            raise ValidationError("CANDIDATE_CHANGED")
        return dict(sorted(data.items()))
    except ValidationError:
        raise
    except (OSError, UnicodeError, RuntimeError, ValueError):
        raise ValidationError("CANDIDATE_UNAVAILABLE") from None


def _files(data: dict[str, bytes]) -> list[dict[str, Any]]:
    return [
        {"path": path, "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)}
        for path, content in sorted(data.items())
    ]


def _digest(data: dict[str, bytes]) -> str:
    serialized = json.dumps(_files(data), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def candidate_files(root: Path) -> list[dict[str, Any]]:
    return _files(_capture(root))


def candidate_digest(root: Path) -> str:
    return _digest(_capture(root))


def snapshot_candidate(source: Path, destination: Path, expected_digest: str) -> Path:
    data = _capture(source)
    if not _HASH.fullmatch(expected_digest) or _digest(data) != expected_digest:
        raise ValidationError("CANDIDATE_CHANGED")
    destination = Path(destination)
    if destination.exists():
        raise ValidationError("SNAPSHOT_EXISTS")
    parent = destination.parent
    _links(parent)
    destination.mkdir()
    try:
        for relative, content in data.items():
            path = destination.joinpath(*relative.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            _links(path.parent)
            with path.open("xb") as stream:
                stream.write(content)
        if candidate_digest(destination) != expected_digest:
            raise ValidationError("SNAPSHOT_CHANGED")
    except (OSError, RuntimeError, ValueError):
        raise ValidationError("SNAPSHOT_UNAVAILABLE") from None
    return destination


def _name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value.split("[", 1)[0]).lower()


def static_review(candidate_root: Path) -> dict[str, Any]:
    data = _capture(candidate_root)
    errors: list[dict[str, Any]] = []
    parsed: dict[str, Any] = {}
    asts: dict[str, ast.Module] = {}
    for path, content in data.items():
        try:
            if path.endswith(".py"):
                asts[path] = ast.parse(content.decode("utf-8"), filename=path)
            elif path.endswith(".json"):
                parsed[path] = json.loads(content)
            elif path.endswith(".toml"):
                parsed[path] = tomllib.loads(content.decode("utf-8"))
        except (SyntaxError, RecursionError) as exc:
            errors.append(
                {
                    "code": "PYTHON_SYNTAX_INVALID",
                    "path": path,
                    "line": getattr(exc, "lineno", 0) or 0,
                }
            )
        except (json.JSONDecodeError, tomllib.TOMLDecodeError, ValueError):
            errors.append({"code": "STRUCTURED_FILE_INVALID", "path": path})
    manifest = parsed.get("extension.toml", {})
    project = parsed.get("pyproject.toml", {}).get("project", {})
    if not isinstance(manifest, dict) or not all(
        isinstance(manifest.get(key), str) and manifest[key]
        for key in ("id", "version", "entrypoint")
    ):
        errors.append({"code": "MANIFEST_INVALID", "path": "extension.toml"})
        manifest = {}
    if (
        not isinstance(project, dict)
        or not project
        or project.get("version") != manifest.get("version")
    ):
        errors.append({"code": "PROJECT_INVALID", "path": "pyproject.toml"})
        project = {}
    lock_name = manifest.get("dependency_lock", "requirements.lock")
    if not isinstance(lock_name, str) or lock_name not in data:
        errors.append({"code": "DEPENDENCY_LOCK_MISSING", "path": "extension.toml"})
    lock = data.get(lock_name, b"").decode("utf-8") if isinstance(lock_name, str) else ""
    pins: dict[str, str] = {}
    for line in lock.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(
            r"([A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9_,.-]+\])?)"
            r"==([A-Za-z0-9.+_-]+)(?:\s+--hash=sha256:[0-9a-f]{64})+",
            line,
        )
        if match is None:
            errors.append({"code": "DEPENDENCY_LOCK_INVALID", "path": str(lock_name)})
            continue
        name, version = _name(match[1]), match[2]
        if name in pins and pins[name] != version:
            errors.append({"code": "DEPENDENCY_LOCK_INVALID", "path": str(lock_name)})
        pins[name] = version
    dependencies = project.get("dependencies", [])
    if not isinstance(dependencies, list):
        dependencies = []
        errors.append({"code": "DEPENDENCIES_INVALID", "path": "pyproject.toml"})
    for dependency in dependencies:
        match = _PIN.fullmatch(dependency) if isinstance(dependency, str) else None
        if match is None or (
            _name(match[1]) != "personal-assistant-extension-sdk"
            and pins.get(_name(match[1])) != match[2]
        ):
            errors.append({"code": "DEPENDENCY_LOCK_INVALID", "path": "pyproject.toml"})
    own_packages = {
        path.split("/")[1].split(".")[0]
        for path in data
        if path.startswith("src/") and len(path.split("/")) >= 2
    }
    imports = {_name(str(item)).replace("-", "_") for item in pins}
    for path, tree in asts.items():
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                first = name.split(".")[0]
                if first == "personal_assistant":
                    errors.append(
                        {
                            "code": "PRIVATE_HOST_IMPORT",
                            "path": path,
                            "line": getattr(node, "lineno", 0),
                        }
                    )
                elif first not in (
                    sys.stdlib_module_names | own_packages | imports | {"personal_assistant_sdk"}
                ) and not path.startswith("tests/"):
                    errors.append(
                        {
                            "code": "UNDECLARED_IMPORT",
                            "path": path,
                            "line": getattr(node, "lineno", 0),
                        }
                    )
    tools = manifest.get("tools", [])
    return {
        "candidate_hash": _digest(data),
        "ok": not errors,
        "errors": errors[:100],
        "manifest": {
            "id": manifest.get("id"),
            "version": manifest.get("version"),
            "tools": [
                {"id": item.get("id"), "risk": item.get("risk")}
                for item in tools
                if isinstance(item, dict)
            ][:100]
            if isinstance(tools, list)
            else [],
            "capabilities": manifest.get("capabilities", {}),
        },
        "dependencies": dependencies[:100],
        "warnings": ["TRUSTED_CODE_EXECUTION_NOT_A_SANDBOX"],
    }


_RUNNER = r"""
import sys
if sys.stdin.buffer.read(1) != b"G":
    raise SystemExit(3)
sys.stdin.close()
import json
import re
import traceback
import unittest
from pathlib import Path
config = json.loads(Path(__file__).with_name("control.json").read_text(encoding="utf-8"))
root = Path(config["snapshot"])
sys.path.insert(0, str(root / "src"))
if config.get("sdk_path"):
    sys.path.insert(1, config["sdk_path"])
def clean(value, pattern, fallback):
    return value if isinstance(value, str) and re.fullmatch(pattern, value) else fallback
class Result(unittest.TextTestResult):
    diagnostics = None
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.diagnostics = []
    def locate(self, test, error):
        if len(self.diagnostics) >= 20:
            return
        item = {
            "test_id": clean(test.id(), r"[A-Za-z0-9_.]{1,180}", "unknown"),
            "exception_type": clean(error[0].__name__, r"[A-Za-z_][A-Za-z0-9_]{0,127}", "Error"),
            "relative_path": "", "line": 0,
        }
        for frame in traceback.extract_tb(error[2]):
            path = Path(frame.filename)
            try:
                item["relative_path"] = path.resolve().relative_to(root.resolve()).as_posix()
                item["line"] = frame.lineno
            except (ValueError, OSError):
                pass
        self.diagnostics.append(item)
    def addError(self, test, error):
        self.locate(test, error)
        super().addError(test, error)
    def addFailure(self, test, error):
        self.locate(test, error)
        super().addFailure(test, error)
    def addSubTest(self, test, subtest, error):
        if error is not None:
            self.locate(test, error)
        super().addSubTest(test, subtest, error)
try:
    suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern="test*.py")
    result = unittest.TextTestRunner(verbosity=1, resultclass=Result).run(suite)
    report = {
        "tests_run": result.testsRun, "failures": len(result.failures),
        "errors": len(result.errors), "skipped": len(result.skipped),
        "failed_tests": result.diagnostics,
    }
    Path(config["report"]).write_text(json.dumps(report), encoding="utf-8")
    raise SystemExit(0 if result.wasSuccessful() and result.testsRun > len(result.skipped) else 1)
except SystemExit:
    raise
except BaseException:
    traceback.print_exc()
    raise SystemExit(2)
"""


class _WindowsJob:
    def __init__(self) -> None:
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class Io(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_ulonglong)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", Basic),
                ("IoInfo", Io),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self.kernel: Any = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.TerminateJobObject.restype = wintypes.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ValidationError("PROCESS_CONTAINMENT_UNAVAILABLE")
        limits = Extended()
        limits.BasicLimitInformation.LimitFlags = 0x00002000
        if not self.kernel.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            self.close()
            raise ValidationError("PROCESS_CONTAINMENT_UNAVAILABLE")

    def assign(self, pid: int) -> None:
        process = self.kernel.OpenProcess(0x0100 | 0x0001, False, pid)
        try:
            if not process or not self.kernel.AssignProcessToJobObject(self.handle, process):
                raise ValidationError("PROCESS_CONTAINMENT_UNAVAILABLE")
        finally:
            if process:
                self.kernel.CloseHandle(process)

    def close(self) -> None:
        if self.handle:
            terminated = self.kernel.TerminateJobObject(self.handle, 1)
            closed = self.kernel.CloseHandle(self.handle)
            self.handle = None
            if not terminated or not closed:
                raise ValidationError("PROCESS_CLEANUP_UNCONFIRMED")


class _Output:
    def __init__(self, stream: IO[bytes]) -> None:
        self.stream = stream
        self.data = bytearray()
        self.count = 0
        self.thread = threading.Thread(target=self.drain, daemon=True)

    def drain(self) -> None:
        try:
            while chunk := self.stream.read(8192):
                self.count += len(chunk)
                remaining = MAX_LOG_BYTES - len(self.data)
                if remaining > 0:
                    self.data.extend(chunk[:remaining])
        except OSError:
            pass


def _environment(home: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        in {
            "SYSTEMROOT",
            "WINDIR",
            "SYSTEMDRIVE",
            "COMSPEC",
            "PATHEXT",
            "NUMBER_OF_PROCESSORS",
            "PROCESSOR_ARCHITECTURE",
        }
    }
    env.update(
        {
            key: str(home)
            for key in ("HOME", "USERPROFILE", "TEMP", "TMP", "APPDATA", "LOCALAPPDATA")
        }
    )
    env.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", PIP_CONFIG_FILE=os.devnull)
    env["PATH"] = os.pathsep.join(
        (
            str(Path(sys.executable).parent),
            os.path.join(env.get("SystemRoot", env.get("SYSTEMROOT", "")), "System32"),
        )
    )
    return env


def _logs(directory: Path | None, outputs: list[_Output]) -> str | None:
    if directory is None:
        return None
    directory = Path(directory).absolute()
    for part in (*reversed(directory.parents), directory):
        if part.exists():
            _links(part)
    directory.mkdir(parents=True, exist_ok=True)
    _links(directory)
    name = "validation-" + uuid.uuid4().hex + ".log"
    with (directory / name).open("xb") as stream:
        for heading, output in zip((b"STDOUT\n", b"\nSTDERR\n"), outputs, strict=True):
            stream.write(heading)
            stream.write(output.data)
    return name


def run_validation(
    candidate_root: Path,
    *,
    expected_digest: str,
    approved_digest: str,
    timeout_seconds: float = 30,
    log_directory: Path | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "BLOCKED",
        "candidate_hash": expected_digest,
        "tests_run": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "failed_tests": [],
        "exit_code": None,
        "duration_seconds": 0.0,
        "log_name": None,
        "stdout_truncated": False,
        "stderr_truncated": False,
    }
    if (
        not isinstance(expected_digest, str)
        or not _HASH.fullmatch(expected_digest)
        or approved_digest != expected_digest
    ):
        return {**result, "code": "VALIDATION_APPROVAL_REQUIRED"}
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or not 0.1 <= timeout_seconds <= 120
    ):
        return {**result, "code": "VALIDATION_TIMEOUT_INVALID"}
    started = time.monotonic()
    process: subprocess.Popen[bytes] | None = None
    job: _WindowsJob | None = None
    outputs: list[_Output] = []
    executing = False
    try:
        if cancel_event is not None and cancel_event.is_set():
            return {**result, "status": "CANCELLED", "code": "VALIDATION_CANCELLED"}
        review = static_review(candidate_root)
        if review["candidate_hash"] != expected_digest:
            raise ValidationError("CANDIDATE_CHANGED")
        if not review["ok"]:
            return {**result, "code": "STATIC_REVIEW_FAILED", "static_errors": review["errors"]}
        with tempfile.TemporaryDirectory(prefix="pa-plugin-validation-") as temporary:
            run_root = Path(temporary)
            snapshot = snapshot_candidate(candidate_root, run_root / "snapshot", expected_digest)
            home = run_root / "home"
            home.mkdir()
            report = run_root / "report.json"
            spec = importlib.util.find_spec("personal_assistant_sdk")
            sdk_path = str(Path(spec.origin).parent.parent) if spec and spec.origin else None
            (run_root / "control.json").write_text(
                json.dumps(
                    {
                        "snapshot": str(snapshot),
                        "report": str(report),
                        "sdk_path": sdk_path,
                    }
                ),
                encoding="utf-8",
            )
            runner = run_root / "runner.py"
            runner.write_text(_RUNNER, encoding="utf-8")
            if cancel_event is not None and cancel_event.is_set():
                result.update(status="CANCELLED", code="VALIDATION_CANCELLED")
                return result
            if os.name == "nt":
                job = _WindowsJob()
            process = subprocess.Popen(
                [sys.executable, "-I", "-Xutf8", "-B", "-u", str(runner)],
                cwd=snapshot,
                env=_environment(home),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=os.name != "nt",
            )
            try:
                if job is not None:
                    job.assign(process.pid)
                assert process.stdout is not None and process.stderr is not None
                outputs = [_Output(process.stdout), _Output(process.stderr)]
                for output in outputs:
                    output.thread.start()
                assert process.stdin is not None
                if cancel_event is not None and cancel_event.is_set():
                    result.update(status="CANCELLED", code="VALIDATION_CANCELLED")
                    return result
                process.stdin.write(b"G")
                process.stdin.flush()
                process.stdin.close()
                executing = True
                try:
                    deadline = time.monotonic() + timeout_seconds
                    while process.poll() is None:
                        if cancel_event is not None and cancel_event.is_set():
                            result["status"] = "CANCELLED"
                            result["code"] = "VALIDATION_CANCELLED"
                            break
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(process.args, timeout_seconds)
                        try:
                            process.wait(timeout=min(0.1, remaining))
                        except subprocess.TimeoutExpired:
                            continue
                    result["exit_code"] = process.returncode
                    if result["status"] != "CANCELLED":
                        result["status"] = "FAILED"
                    if (
                        result["status"] != "CANCELLED"
                        and report.is_file()
                        and report.stat().st_size <= MAX_LOG_BYTES
                    ):
                        payload = json.loads(report.read_bytes())
                        if isinstance(payload, dict) and all(
                            type(payload.get(key)) is int and 0 <= payload[key] <= 100000
                            for key in ("tests_run", "failures", "errors", "skipped")
                        ):
                            result.update(
                                {
                                    key: payload[key]
                                    for key in ("tests_run", "failures", "errors", "skipped")
                                }
                            )
                            diagnostics = payload.get("failed_tests", [])
                            if isinstance(diagnostics, list):
                                for item in diagnostics[:20]:
                                    if not isinstance(item, dict):
                                        continue
                                    test_id = item.get("test_id", "")
                                    exception_type = item.get("exception_type", "")
                                    relative = item.get("relative_path", "")
                                    line = item.get("line", 0)
                                    if (
                                        isinstance(test_id, str)
                                        and re.fullmatch(r"[A-Za-z0-9_.]{1,180}", test_id)
                                        and isinstance(exception_type, str)
                                        and re.fullmatch(
                                            r"[A-Za-z_][A-Za-z0-9_]{0,127}", exception_type
                                        )
                                        and isinstance(relative, str)
                                        and len(relative) <= 1024
                                        and type(line) is int
                                        and 0 <= line <= 1000000
                                    ):
                                        if relative:
                                            try:
                                                _parts(relative)
                                            except ValidationError:
                                                continue
                                        result["failed_tests"].append(
                                            {
                                                "test_id": test_id,
                                                "exception_type": exception_type,
                                                "relative_path": relative,
                                                "line": line,
                                            }
                                        )
                            if (
                                process.returncode == 0
                                and payload["tests_run"] > payload["skipped"]
                                and payload["failures"] == payload["errors"] == 0
                            ):
                                result["status"] = "PASSED"
                except subprocess.TimeoutExpired:
                    result["status"] = "TIMED_OUT"
                    result["code"] = "VALIDATION_DEADLINE_EXCEEDED"
            finally:
                cleanup_confirmed = True
                if job is not None:
                    try:
                        job.close()
                    except (ValidationError, OSError):
                        cleanup_confirmed = False
                    job = None
                elif os.name != "nt":
                    with suppress(ProcessLookupError):
                        kill_group = getattr(os, "killpg", None)
                        if kill_group is None:
                            raise ValidationError("PROCESS_CONTAINMENT_UNAVAILABLE")
                        kill_group(process.pid, getattr(signal, "SIGKILL", 9))
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                pending_streams: set[int] = set()
                for output in outputs:
                    output.thread.join(timeout=2)
                    if output.thread.is_alive():
                        cleanup_confirmed = False
                        pending_streams.add(id(output.stream))
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None and id(stream) not in pending_streams:
                        with suppress(OSError, ValueError):
                            stream.close()
                if not cleanup_confirmed:
                    raise ValidationError("PROCESS_CLEANUP_UNCONFIRMED")
            result["stdout_truncated"] = outputs[0].count > MAX_LOG_BYTES if outputs else False
            result["stderr_truncated"] = outputs[1].count > MAX_LOG_BYTES if outputs else False
            result["log_name"] = _logs(log_directory, outputs) if outputs else None
    except ValidationError as exc:
        result["code"] = exc.code
        result["status"] = "UNKNOWN" if executing else "BLOCKED"
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        result["code"] = "VALIDATION_UNAVAILABLE"
        result["status"] = "UNKNOWN" if executing else "BLOCKED"
    finally:
        if job is not None:
            try:
                job.close()
            except (ValidationError, OSError):
                result.update(status="UNKNOWN", code="PROCESS_CLEANUP_UNCONFIRMED")
        result["duration_seconds"] = round(time.monotonic() - started, 3)
    return result
