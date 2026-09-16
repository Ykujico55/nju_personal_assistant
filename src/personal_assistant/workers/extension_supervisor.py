"""Extension Supervisor entrypoint.

In F02 the supervisor is hosted by the loopback Local Admin API process, which is
the only component allowed to expose code-level extension management.  Running
this module standalone would create a second supervisor with a second view of the
same extensions, so it intentionally refuses to start.
"""

from __future__ import annotations


def main() -> int:
    raise RuntimeError(
        "The Extension Supervisor is hosted by the Local Admin API "
        "(personal_assistant.admin_app, scripts/run-admin.ps1 on 127.0.0.1:8001). "
        "It has no standalone mode by design."
    )


if __name__ == "__main__":
    raise SystemExit(main())
