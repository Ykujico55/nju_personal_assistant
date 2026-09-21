"""NJU ehall supervised transaction extension (business logic only).

The extension never touches a browser itself: it uses the host's read-only
``host.browser.*`` capability for snapshots and delegates every page write to
the host executors behind the Tool Gateway.
"""

from __future__ import annotations

EXTENSION_ID = "nju.ehall"
EXTENSION_VERSION = "0.1.0"

__all__ = ["EXTENSION_ID", "EXTENSION_VERSION"]
