"""F04 real-PostgreSQL tests: migration 0005, durability and concurrency.

Only run when ``PA_TEST_DATABASE_URL`` points at a dedicated ``*_test``
database; each test creates and drops its own throwaway database.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import shutil
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg

from personal_assistant.bootstrap import build_container
from personal_assistant.core.models import (
    ContextField,
    DataClassification,
    ModelRequest,
    RecipientIdentity,
    recipient_fingerprint,
)
from personal_assistant.core.models.disclosure import (
    DisclosureConsentService,
    DisclosureConsentState,
    DisclosureIdempotencyConflictError,
    DisclosureStateError,
)
from personal_assistant.domain import ConcurrentModificationError
from personal_assistant.infrastructure.database import (
    MigrationChecksumError,
    PostgresAdapterConfig,
    PostgresDisclosureConsentStore,
    build_postgres_adapters,
)
from personal_assistant.infrastructure.memory import InMemorySecretStore
from personal_assistant.settings import Settings

BASE_URL = os.getenv("PA_TEST_DATABASE_URL")
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
RAW = "private mail body 4242"

REMOTE_IDENTITY = RecipientIdentity(
    provider_id="remote.openai",
    adapter="openai_compatible",
    endpoint="https://api.example.test/v1",
    model_id="gpt-test-1",
)
REMOTE_FP = recipient_fingerprint(REMOTE_IDENTITY)
RECIPIENTS = {REMOTE_IDENTITY.provider_id: REMOTE_IDENTITY}


def build_service(store: object) -> DisclosureConsentService:
    return DisclosureConsentService(store, recipients=RECIPIENTS)  # type: ignore[arg-type]

F04_MIGRATION_NAMES = (
    "0001_core.sql",
    "0002_f01_persistence.sql",
    "0003_f02_operations.sql",
    "0004_f02_operation_request_scope.sql",
    "0005_f04_model_disclosure.sql",
)
MIGRATION_NAMES = (*F04_MIGRATION_NAMES, "0006_f06_mail_transport.sql")


def _dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgres://", "postgresql://"
    )


def _with_db(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + database, "", ""))


def _file_checksum(name: str) -> str:
    return hashlib.sha256((MIGRATIONS_DIR / name).read_bytes()).hexdigest()


def sensitive_request() -> ModelRequest:
    return ModelRequest(
        purpose="draft reply",
        instruction="draft a reply",
        fields=(ContextField("mail_body", RAW, DataClassification.SENSITIVE, "mail:1"),),
    )


@unittest.skipUnless(BASE_URL, "set PA_TEST_DATABASE_URL to run real PostgreSQL tests")
class PostgresF04Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        base = _dsn(BASE_URL or "")
        base_name = urlsplit(base).path.lstrip("/")
        if not base_name.endswith("_test"):
            raise RuntimeError(
                "PA_TEST_DATABASE_URL must name a dedicated *_test database"
            )
        self._base = base
        self._admin_dsn = _with_db(base, "postgres")
        self._db_name = f"pa_f04_test_{uuid4().hex}"
        admin = await asyncpg.connect(self._admin_dsn)
        try:
            await admin.execute(f'CREATE DATABASE "{self._db_name}"')
        finally:
            await admin.close()
        self.tmp = Path(tempfile.mkdtemp(prefix="pa_f04_pg_"))
        self._adapters = []
        self._temp_dirs: list[Path] = []

    async def asyncTearDown(self) -> None:
        for adapters in self._adapters:
            with contextlib.suppress(Exception):
                await adapters.close()
        admin = await asyncpg.connect(self._admin_dsn)
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{self._db_name}" WITH (FORCE)')
        finally:
            await admin.close()
        resolved = self.tmp.resolve()
        if not resolved.is_relative_to(Path(tempfile.gettempdir()).resolve()):
            raise AssertionError(f"refusing to delete {resolved}")
        shutil.rmtree(resolved, ignore_errors=True)
        for directory in self._temp_dirs:
            shutil.rmtree(directory, ignore_errors=True)

    def _adapters_for(self, database: str, migrations_dir: Path | None = None):
        return build_postgres_adapters(
            PostgresAdapterConfig(
                database_url=_with_db(self._base, database),
                migrations_dir=migrations_dir,
            )
        )

    async def _new_adapters(self, migrations_dir: Path | None = None):
        adapters = self._adapters_for(self._db_name, migrations_dir)
        await adapters.startup()
        self._adapters.append(adapters)
        return adapters

    def _settings(self, *, with_models: bool = False) -> Settings:
        values: dict[str, object] = {
            "environment": "test",
            "log_level": "INFO",
            "public_host": "127.0.0.1",
            "public_port": 8000,
            "admin_host": "127.0.0.1",
            "admin_port": 8001,
            "health_host": "127.0.0.1",
            "health_port": 8010,
            "storage_backend": "postgres",
            "database_url": _with_db(self._base, self._db_name),
            "extension_root": self.tmp / "extensions",
            "artifact_root": self.tmp / "artifacts",
            "trust_cloudflare_access": False,
            "public_origin": None,
            "cf_access_team_domain": None,
            "cf_access_aud": None,
        }
        if with_models:
            values.update(
                {
                    "model_remote_base_url": "https://api.example.test/v1",
                    "model_remote_model": "gpt-test-1",
                    "model_remote_secret_handle": "handle-1",
                    "model_local_base_url": "http://127.0.0.1:11434",
                    "model_local_model": "llama3.1:8b",
                }
            )
        return Settings(**values)

    async def _fetch(self, query: str, *args: object) -> list[asyncpg.Record]:
        adapters = await self._new_adapters()
        async with adapters.database.connection() as connection:
            return list(await connection.fetch(query, *args))

    # -- migration contract -------------------------------------------------

    async def test_empty_database_applies_0005_and_rerun_is_noop(self) -> None:
        adapters = await self._new_adapters()
        rows = await self._fetch(
            "SELECT version, checksum FROM schema_migrations ORDER BY version"
        )
        versions = [row["version"] for row in rows]
        self.assertEqual([name[:-4] for name in MIGRATION_NAMES], versions)
        for row in rows:
            self.assertEqual(_file_checksum(f"{row['version']}.sql"), row["checksum"])
        self.assertEqual((), await adapters.startup())

    async def test_upgrade_from_0004_adds_only_later_migrations_and_keeps_checksums(self) -> None:
        legacy = Path(tempfile.mkdtemp(prefix="pa_f04_legacy_"))
        self._temp_dirs.append(legacy)
        for name in F04_MIGRATION_NAMES[:-1]:
            shutil.copy(MIGRATIONS_DIR / name, legacy)
        first = self._adapters_for(self._db_name, legacy)
        applied = await first.startup()
        self.assertNotIn("0005_f04_model_disclosure", applied)
        await first.close()

        second = self._adapters_for(self._db_name)
        applied = await second.startup()
        self._adapters.append(second)
        self.assertEqual(
            ("0005_f04_model_disclosure", "0006_f06_mail_transport"), applied
        )
        rows = await self._fetch(
            "SELECT version, checksum FROM schema_migrations ORDER BY version"
        )
        by_version = {row["version"]: row["checksum"] for row in rows}
        for name in F04_MIGRATION_NAMES:
            self.assertEqual(_file_checksum(name), by_version[name[:-4]], name)
        self.assertEqual(
            _file_checksum("0006_f06_mail_transport.sql"),
            by_version["0006_f06_mail_transport"],
        )

    async def test_0005_checksum_drift_is_rejected(self) -> None:
        await self._new_adapters()
        drifted = Path(tempfile.mkdtemp(prefix="pa_f04_drift_"))
        self._temp_dirs.append(drifted)
        for name in MIGRATION_NAMES:
            shutil.copy(MIGRATIONS_DIR / name, drifted)
        with (drifted / "0005_f04_model_disclosure.sql").open("ab") as handle:
            handle.write(b"\n-- drift fixture\n")
        late = self._adapters_for(self._db_name, drifted)
        with self.assertRaises(MigrationChecksumError):
            await late.startup()
        await late.close()
        self._adapters.append(late)

    # -- persistence and lifecycle -----------------------------------------

    async def test_container_rebuild_keeps_persisted_consent_usable(self) -> None:
        settings = self._settings(with_models=True)
        container = build_container(settings, secret_store=InMemorySecretStore())
        await container.storage.startup()
        request = sensitive_request()
        preview = container.disclosures.preview(request, provider_id="remote.openai", now=NOW)
        record = await container.disclosures.confirm(
            request,
            provider_id="remote.openai",
            user_id="owner",
            preview_hash=preview.preview_hash,
            idempotency_key="confirm-restart",
            now=NOW,
        )
        await container.storage.close()

        rebuilt = build_container(settings, secret_store=InMemorySecretStore())
        await rebuilt.storage.startup()
        authorized = await rebuilt.disclosures.authorize(
            request,
            consent_id=record.id,
            user_id="owner",
            provider_id="remote.openai",
            recipient_fingerprint=REMOTE_FP,
            now=NOW + timedelta(minutes=5),
        )
        self.assertIsNotNone(authorized)
        assert authorized is not None
        self.assertEqual(record.id, authorized.id)
        self.assertEqual(0, authorized.version)
        self.assertEqual(DisclosureConsentState.ACTIVE, authorized.state)

        revoked = await rebuilt.disclosures.revoke(
            record.id,
            user_id="owner",
            expected_version=record.version,
            idempotency_key="revoke-restart",
            now=NOW + timedelta(minutes=6),
        )
        self.assertEqual(DisclosureConsentState.REVOKED, revoked.state)
        await rebuilt.storage.close()

        third = build_container(settings, secret_store=InMemorySecretStore())
        await third.storage.startup()
        self.assertIsNone(
            await third.disclosures.authorize(
                request,
                consent_id=record.id,
                user_id="owner",
                provider_id="remote.openai",
                recipient_fingerprint=REMOTE_FP,
                now=NOW + timedelta(minutes=7),
            )
        )
        replay = await third.disclosures.revoke(
            record.id,
            user_id="owner",
            expected_version=record.version,
            idempotency_key="revoke-restart",
            now=NOW + timedelta(minutes=8),
        )
        self.assertEqual(revoked.revoked_at, replay.revoked_at)
        await third.storage.close()

    async def test_concurrent_connections_cannot_create_conflicting_consents(self) -> None:
        adapters = await self._new_adapters()
        first = build_service(adapters.disclosure_consents)
        second = build_service(PostgresDisclosureConsentStore(adapters.database))
        request = sensitive_request()
        preview = first.preview(request, provider_id="remote.openai", now=NOW)

        results = await asyncio.gather(
            first.confirm(
                request,
                provider_id="remote.openai",
                user_id="owner",
                preview_hash=preview.preview_hash,
                idempotency_key="shared-key",
                now=NOW,
            ),
            second.confirm(
                request,
                provider_id="remote.openai",
                user_id="owner",
                preview_hash=preview.preview_hash,
                idempotency_key="shared-key",
                now=NOW,
            ),
            return_exceptions=True,
        )
        self.assertTrue(all(not isinstance(item, BaseException) for item in results), results)
        ids = {item.id for item in results}  # type: ignore[union-attr]
        self.assertEqual(1, len(ids))
        async with adapters.database.connection() as connection:
            count = await connection.fetchval(
                "SELECT count(*) FROM model_disclosure_consents"
            )
        self.assertEqual(1, count)

        changed = ModelRequest(
            purpose="summarize",
            instruction="summarize",
            fields=(ContextField("mail_body", RAW, DataClassification.SENSITIVE, "mail:1"),),
        )
        changed_preview = first.preview(changed, provider_id="remote.openai", now=NOW)
        with self.assertRaises(DisclosureIdempotencyConflictError):
            await first.confirm(
                changed,
                provider_id="remote.openai",
                user_id="owner",
                preview_hash=changed_preview.preview_hash,
                idempotency_key="shared-key",
                now=NOW,
            )

    async def test_revoke_cas_and_idempotent_replay_across_restart(self) -> None:
        adapters = await self._new_adapters()
        service = build_service(adapters.disclosure_consents)
        request = sensitive_request()
        preview = service.preview(request, provider_id="remote.openai", now=NOW)
        record = await service.confirm(
            request,
            provider_id="remote.openai",
            user_id="owner",
            preview_hash=preview.preview_hash,
            idempotency_key="confirm-1",
            now=NOW,
        )
        with self.assertRaises(ConcurrentModificationError):
            await service.revoke(
                record.id,
                user_id="owner",
                expected_version=7,
                idempotency_key="revoke-stale",
                now=NOW,
            )
        revoked = await service.revoke(
            record.id,
            user_id="owner",
            expected_version=0,
            idempotency_key="revoke-1",
            now=NOW + timedelta(minutes=1),
        )
        self.assertEqual(1, revoked.version)
        await adapters.close()

        rebuilt_adapters = await self._new_adapters()
        rebuilt = build_service(rebuilt_adapters.disclosure_consents)
        replay = await rebuilt.revoke(
            record.id,
            user_id="owner",
            expected_version=0,
            idempotency_key="revoke-1",
            now=NOW + timedelta(minutes=2),
        )
        self.assertEqual(revoked.revoked_at, replay.revoked_at)
        self.assertEqual(1, replay.version)
        self.assertIsNone(
            await rebuilt.authorize(
                request,
                consent_id=record.id,
                user_id="owner",
                provider_id="remote.openai",
                recipient_fingerprint=REMOTE_FP,
                now=NOW + timedelta(minutes=2),
            )
        )
        await rebuilt_adapters.close()

        final_adapters = await self._new_adapters()
        final = build_service(final_adapters.disclosure_consents)
        self.assertIsNone(await final.authorize(
            request,
            consent_id=record.id,
            user_id="owner",
            provider_id="remote.openai",
            recipient_fingerprint=REMOTE_FP,
            now=NOW + timedelta(minutes=3),
        ))

    async def test_expired_consent_cannot_be_revoked(self) -> None:
        adapters = await self._new_adapters()
        service = build_service(adapters.disclosure_consents)
        request = sensitive_request()
        preview = service.preview(
            request, provider_id="remote.openai", ttl=timedelta(minutes=1), now=NOW
        )
        record = await service.confirm(
            request,
            provider_id="remote.openai",
            user_id="owner",
            preview_hash=preview.preview_hash,
            idempotency_key="confirm-short",
            ttl=timedelta(minutes=1),
            now=NOW,
        )
        with self.assertRaises(DisclosureStateError):
            await service.revoke(
                record.id,
                user_id="owner",
                expected_version=0,
                idempotency_key="revoke-expired",
                now=NOW + timedelta(minutes=2),
            )
        async with adapters.database.connection() as connection:
            row = await connection.fetchrow(
                "SELECT state, version FROM model_disclosure_consents WHERE id = $1",
                record.id,
            )
        self.assertEqual("EXPIRED", row["state"])
        self.assertEqual(1, row["version"])
        with self.assertRaises(DisclosureStateError):
            await service.revoke(
                record.id,
                user_id="owner",
                expected_version=1,
                idempotency_key="revoke-expired-2",
                now=NOW + timedelta(minutes=3),
            )

    async def test_concurrent_revokes_allow_exactly_one_winner(self) -> None:
        adapters = await self._new_adapters()
        first = build_service(adapters.disclosure_consents)
        second = build_service(PostgresDisclosureConsentStore(adapters.database))
        request = sensitive_request()
        preview = first.preview(request, provider_id="remote.openai", now=NOW)
        record = await first.confirm(
            request,
            provider_id="remote.openai",
            user_id="owner",
            preview_hash=preview.preview_hash,
            idempotency_key="confirm-1",
            now=NOW,
        )
        results = await asyncio.gather(
            first.revoke(
                record.id,
                user_id="owner",
                expected_version=0,
                idempotency_key="revoke-a",
                now=NOW,
            ),
            second.revoke(
                record.id,
                user_id="owner",
                expected_version=0,
                idempotency_key="revoke-b",
                now=NOW,
            ),
            return_exceptions=True,
        )
        successes = [item for item in results if not isinstance(item, BaseException)]
        failures = [item for item in results if isinstance(item, BaseException)]
        self.assertEqual(1, len(successes), results)
        self.assertEqual(1, len(failures), results)
        self.assertIsInstance(failures[0], ConcurrentModificationError)
        async with adapters.database.connection() as connection:
            state = await connection.fetchval(
                "SELECT state FROM model_disclosure_consents WHERE id = $1", record.id
            )
        self.assertEqual("REVOKED", state)

    async def test_raw_values_never_reach_consent_tables(self) -> None:
        adapters = await self._new_adapters()
        service = build_service(adapters.disclosure_consents)
        request = sensitive_request()
        preview = service.preview(request, provider_id="remote.openai", now=NOW)
        await service.confirm(
            request,
            provider_id="remote.openai",
            user_id="owner",
            preview_hash=preview.preview_hash,
            idempotency_key="confirm-raw",
            now=NOW,
        )
        rows = await self._fetch("SELECT * FROM model_disclosure_consents")
        commands = await self._fetch("SELECT * FROM model_disclosure_commands")
        rendered = repr(rows) + repr(commands)
        self.assertNotIn(RAW, rendered)
        self.assertEqual(1, len(rows))
        self.assertEqual(64, len(rows[0]["field_digest"]))
        listed = await service.list_for_user("owner")
        self.assertEqual(1, len(listed))
        self.assertEqual(rows[0]["id"], listed[0].id)


if __name__ == "__main__":
    unittest.main()
