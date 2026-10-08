"""Black-box check of the local health-only listener used by F08.6."""

from __future__ import annotations

import socket
import subprocess
import sys
import time

import httpx


def test_health_listener_exposes_only_exact_health_path() -> None:
    with socket.socket() as port_probe:
        port_probe.bind(("127.0.0.1", 0))
        port = port_probe.getsockname()[1]

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "personal_assistant.health_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            follow_redirects=False,
            trust_env=False,
            timeout=1,
        ) as client:
            deadline = time.monotonic() + 10
            while True:
                try:
                    response = client.get("/healthz")
                    if response.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert process.poll() is None, "health listener exited during startup"
                assert time.monotonic() < deadline, "health listener did not start"
                time.sleep(0.1)

            assert response.json() == {"status": "ok"}
            for path in (
                "/healthz/",
                "/",
                "/api/v1/tasks",
                "/admin/v1/extensions",
                "/docs",
                "/openapi.json",
            ):
                assert client.get(path).status_code == 404, path
            assert client.post("/api/v1/tasks", json={"objective": "probe"}).status_code == 404
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
