"""Core host-control port; filesystem and OS details remain in infrastructure."""
from __future__ import annotations

from typing import Protocol


class HostControlPort(Protocol):
    def frozen(self) -> bool: ...
    def freeze(self) -> None: ...
    def resume(self) -> None: ...
