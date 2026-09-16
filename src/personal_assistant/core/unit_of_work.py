"""Unit-of-work boundary for multi-repository commands.

A synchronous, dependency-free transaction coordinator. The production database
adapter implements it; the in-memory adapters do not need it.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol


class UnitOfWorkPort(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[None]: ...
