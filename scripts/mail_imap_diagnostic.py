"""Step-by-step IMAP diagnostic used by the controlled real-mail harness.

Reads one JSON line ``{address, password, host, port}`` from stdin (the password
never appears in argv, environment, files or logs) and prints one JSON line per
step to stdout: the result type and a bounded server response.  It performs the
same read-only command sequence as the host mail client, so a failure pinpoints
the exact step.
"""

from __future__ import annotations

import contextlib
import imaplib
import json
import re
import ssl
import sys
from typing import Any

MAX_TEXT = 200


def _step(name: str, value: str) -> None:
    print(json.dumps([name, value[:MAX_TEXT]], ensure_ascii=False), flush=True)


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _probe_containers(connection: imaplib.IMAP4_SSL) -> None:
    """Read-only LIST probes for children hidden under NoSelect containers."""

    try:
        typ, listing = connection.list()
    except Exception as exc:  # noqa: BLE001 - diagnostic output
        _step("containers", f"{type(exc).__name__}: {exc}")
        return
    if typ != "OK" or not isinstance(listing, list):
        _step("containers", f"{typ} no listing")
        return
    containers: list[str] = []
    for item in listing:
        text = _text(item)
        if "HasChildren" not in text:
            continue
        match = re.search(r'"([^"]*)"\s*$', text)
        if match:
            containers.append(match.group(1))
    _step("containers", f"{len(containers)}: {' | '.join(containers)[:150]}")
    for raw in containers[:5]:
        quoted = '"' + raw.replace("\\", "\\\\").replace('"', '\\"') + '"'
        for label, directory, pattern in (
            ("ref-star", quoted, '"*"'),
            ("ref-pct", quoted, '"%"'),
            ("prefix-star", '""', f'"{raw}/*"'),
        ):
            try:
                typ, data = connection.list(directory, pattern)
                names = []
                if isinstance(data, list):
                    for entry in data[:20]:
                        match = re.search(r'"([^"]*)"\s*$', _text(entry))
                        if match:
                            names.append(match.group(1))
                _step(f"list:{label}", f"{typ} {len(names)}: {' | '.join(names)[:150]}")
            except Exception as exc:  # noqa: BLE001 - diagnostic output
                _step(f"list:{label}", f"{type(exc).__name__}: {exc}")


def main() -> int:
    line = sys.stdin.readline()
    if not line:
        _step("input", "missing")
        return 2
    payload = json.loads(line)
    address = str(payload["address"])
    password = str(payload["password"])
    host = str(payload["host"])
    port = int(payload["port"])
    try:
        connection = imaplib.IMAP4_SSL(
            host=host,
            port=port,
            ssl_context=ssl.create_default_context(),
            timeout=15,
        )
        _step("connect", "ok")
        typ, data = connection.capability()
        capabilities = _text(data[0]) if data and data[0] else ""
        _step("capability", f"{typ} {capabilities}")
        mechanisms = sorted(
            item[5:] for item in capabilities.upper().split() if item.startswith("AUTH=")
        )
        if "PLAIN" in mechanisms:
            credentials = f"\0{address}\0{password}".encode()
            connection.authenticate("PLAIN", lambda _challenge: credentials)
        else:
            connection.login(address, password)
        _step("authenticate", "ok")
        typ, data = connection.capability()
        _step("capability_after_auth", f"{typ} {_text(data[0]) if data and data[0] else ''}")
        if "ID" in capabilities.upper().split():
            try:
                typ, data = connection.xatom(
                    "ID", '("name" "personal-assistant" "version" "0.1")'
                )
                _step("id", f"{typ} {_text(data)}")
            except Exception as exc:  # noqa: BLE001 - diagnostic output
                _step("id_exception", f"{type(exc).__name__}: {exc}")
                raise
        try:
            typ, data = connection.select('"INBOX"', readonly=True)
            _step("select", f"{typ} {_text(data[0]) if data and data[0] else ''}")
        except Exception as exc:  # noqa: BLE001 - diagnostic output
            _step("select_exception", f"{type(exc).__name__}: {exc}")
            raise
        typ, data = connection.response("UIDVALIDITY")
        _step("uidvalidity", f"{typ} {_text(data[0]) if data and data[0] else ''}")
        _probe_containers(connection)
        with contextlib.suppress(Exception):
            connection.logout()
        _step("done", "ok")
    except Exception as exc:  # noqa: BLE001 - diagnostic output
        _step("exception", f"{type(exc).__name__}: {exc}")
        return 1
    finally:
        del password
    return 0


if __name__ == "__main__":
    sys.exit(main())
