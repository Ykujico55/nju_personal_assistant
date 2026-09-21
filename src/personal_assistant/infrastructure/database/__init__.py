"""Production database adapters and migration runner."""

from .browser_store import PostgresBrowserAdapterStore, PostgresBrowserSessionStore
from .config import PostgresAdapterConfig, normalize_dsn
from .connection import PostgresDatabase
from .disclosure_consents import PostgresDisclosureConsentStore
from .extension_data import PostgresExtensionDataAccess
from .extension_versions import PostgresVersionCatalog
from .mail_ledger import PostgresMailDeliveryLedger
from .migrate import (
    MIGRATION_ADVISORY_LOCK_KEY,
    MigrationChecksumError,
    MigrationError,
    run_migrations,
)
from .operations import PostgresExtensionOperationStore
from .postgres import PostgresAdapterNotImplemented, PostgresAdapters, build_postgres_adapters

__all__ = [
    "MIGRATION_ADVISORY_LOCK_KEY",
    "MigrationChecksumError",
    "MigrationError",
    "PostgresAdapterConfig",
    "PostgresBrowserAdapterStore",
    "PostgresBrowserSessionStore",
    "PostgresAdapterNotImplemented",
    "PostgresAdapters",
    "PostgresDatabase",
    "PostgresDisclosureConsentStore",
    "PostgresExtensionDataAccess",
    "PostgresExtensionOperationStore",
    "PostgresMailDeliveryLedger",
    "PostgresVersionCatalog",
    "build_postgres_adapters",
    "normalize_dsn",
    "run_migrations",
]
