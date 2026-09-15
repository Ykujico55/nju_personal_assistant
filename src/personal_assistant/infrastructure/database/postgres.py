"""Deliberately explicit boundary for the unfinished production adapter.

The SQL schema exists and the ports are stable, but returning an in-memory adapter from
this function would create a dangerous false sense of durability. F01 implements it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn


class PostgresAdapterNotImplemented(RuntimeError):
    code = "POSTGRES_ADAPTER_NOT_IMPLEMENTED"


@dataclass(frozen=True, slots=True)
class PostgresAdapterConfig:
    database_url: str


def build_postgres_adapters(config: PostgresAdapterConfig) -> NoReturn:
    del config
    raise PostgresAdapterNotImplemented(
        "Production PostgreSQL adapters are task F01 in docs/NEXT_STEPS.md"
    )

