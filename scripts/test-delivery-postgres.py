"""Run local development acceptance in one isolated, disposable PostgreSQL container."""

from __future__ import annotations

import argparse
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--tests", nargs="+")
    parser.add_argument("--log-dir", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    tests = ["tests/integration/test_postgres_delivery.py"]
    if args.all:
        script = (root / "scripts/test-postgres.ps1").read_text(encoding="utf-8")
        tests = re.findall(r"tests/integration/[\w_]+\.py", script)
        if not tests:
            raise ValueError("PostgreSQL test enumeration is missing")
    if args.tests:
        if args.all:
            raise ValueError("choose either all tests or explicit targets")
        for selected in args.tests:
            path = (root / selected.split("::")[0]).resolve()
            if not path.is_relative_to(root / "tests") or not path.is_file():
                raise ValueError("test target must be an existing project test file")
        tests = args.tests
    name = "pa-delivery-test-" + uuid4().hex[:12]
    with socket.socket() as socket_:
        socket_.bind(("127.0.0.1", 0))
        port = socket_.getsockname()[1]
    password = uuid4().hex  # An ephemeral synthetic test credential; never printed or reused.
    try:
        created = subprocess.run(
            [
                "docker",
                "run",
                "--pull=never",
                "--detach",
                "--name",
                name,
                "--label",
                "pa.delivery-test=" + name,
                "--publish",
                f"127.0.0.1:{port}:5432",
                "--env",
                "POSTGRES_USER=assistant",
                "--env",
                "POSTGRES_PASSWORD=" + password,
                "pgvector/pgvector:pg17",
            ],
            capture_output=True,
            text=True,
            timeout=45,
        )
    except subprocess.TimeoutExpired:
        print("LOCAL_TEST_CONTAINER_START_TIMEOUT")
        # A timed-out create can complete on the daemon. Only remove this exact owned label.
        try:
            inspected = subprocess.run(
                [
                    "docker",
                    "container",
                    "inspect",
                    name,
                    "--format",
                    '{{ index .Config.Labels "pa.delivery-test" }}',
                ],
                capture_output=True,
                text=True,
                timeout=8,
            )
            if inspected.returncode == 0 and inspected.stdout.strip() == name:
                subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=15)
        except subprocess.TimeoutExpired:
            print("LOCAL_TEST_CONTAINER_CLEANUP_UNCONFIRMED")
        return 1
    identifier = created.stdout.strip()
    if created.returncode or not re.fullmatch(r"[0-9a-f]{64}", identifier):
        print("LOCAL_TEST_CONTAINER_START_FAILED")
        return 1
    try:
        for _ in range(30):
            probe = subprocess.run(
                ["docker", "exec", identifier, "pg_isready", "-U", "assistant"],
                capture_output=True,
                timeout=5,
            )
            if not probe.returncode:
                break
            time.sleep(1)
        else:
            print("LOCAL_TEST_DATABASE_NOT_READY")
            return 1
        env = os.environ.copy()
        env["PA_TEST_DATABASE_URL"] = (
            f"postgresql://assistant:{password}@127.0.0.1:{port}/assistant_test"
        )
        env["PYTHONUTF8"] = "1"
        log = root / (
            "var/verification/development-delivery/postgres-full.log"
            if args.all
            else "var/verification/development-delivery/postgres-directed.log"
        )
        if args.log_dir is not None:
            log = args.log_dir / log.name
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w", encoding="utf-8") as output:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-p",
                    "no:cacheprovider",
                    "-p",
                    "no:faulthandler",
                    "-o",
                    "addopts=--strict-markers",
                    "-q",
                    "--tb=short",
                    *tests,
                ],
                cwd=root,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        print(log.read_text(encoding="utf-8")[-10000:])
        return result.returncode
    finally:
        # Delete only the exact container returned by this invocation; no named existing services.
        subprocess.run(
            ["docker", "rm", "--force", identifier],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )


if __name__ == "__main__":
    raise SystemExit(main())
