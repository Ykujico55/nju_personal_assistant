"""Small, dependency-free environment configuration.

Environment loading is intentionally left to the process manager or uvicorn's
``--env-file`` option so importing the package never performs file I/O.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(RuntimeError):
    pass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be a boolean")


def _env_port(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if not 1 <= value <= 65535:
        raise ConfigurationError(f"{name} must be between 1 and 65535")
    return value


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    log_level: str
    public_host: str
    public_port: int
    admin_host: str
    admin_port: int
    health_host: str
    health_port: int
    storage_backend: str
    database_url: str
    extension_root: Path
    artifact_root: Path
    trust_cloudflare_access: bool
    public_origin: str | None
    cf_access_team_domain: str | None
    cf_access_aud: str | None

    @classmethod
    def from_env(cls) -> Settings:
        settings = cls(
            environment=os.getenv("PA_ENVIRONMENT", "development").strip().lower(),
            log_level=os.getenv("PA_LOG_LEVEL", "INFO").strip().upper(),
            public_host=os.getenv("PA_PUBLIC_HOST", "127.0.0.1").strip(),
            public_port=_env_port("PA_PUBLIC_PORT", 8000),
            admin_host=os.getenv("PA_ADMIN_HOST", "127.0.0.1").strip(),
            admin_port=_env_port("PA_ADMIN_PORT", 8001),
            health_host=os.getenv("PA_HEALTH_HOST", "127.0.0.1").strip(),
            health_port=_env_port("PA_HEALTH_PORT", 8010),
            storage_backend=os.getenv("PA_STORAGE_BACKEND", "memory").strip().lower(),
            database_url=os.getenv(
                "PA_DATABASE_URL",
                "postgresql+asyncpg://assistant:change-me@127.0.0.1:5432/assistant",
            ).strip(),
            extension_root=Path(os.getenv("PA_EXTENSION_ROOT", "./var/extensions")).resolve(),
            artifact_root=Path(os.getenv("PA_ARTIFACT_ROOT", "./var/artifacts")).resolve(),
            trust_cloudflare_access=_env_bool("PA_TRUST_CLOUDFLARE_ACCESS", False),
            public_origin=os.getenv("PA_PUBLIC_ORIGIN") or None,
            cf_access_team_domain=os.getenv("PA_CF_ACCESS_TEAM_DOMAIN") or None,
            cf_access_aud=os.getenv("PA_CF_ACCESS_AUD") or None,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.environment not in {"development", "test", "production"}:
            raise ConfigurationError("PA_ENVIRONMENT must be development, test or production")
        if self.storage_backend not in {"memory", "postgres"}:
            raise ConfigurationError("PA_STORAGE_BACKEND must be memory or postgres")
        if self.environment == "production" and self.storage_backend != "postgres":
            raise ConfigurationError("production refuses non-durable memory storage")
        if self.environment == "production" and not self.trust_cloudflare_access:
            raise ConfigurationError("production public API requires verified Cloudflare Access")
        if not self.trust_cloudflare_access and not _is_loopback(self.public_host):
            raise ConfigurationError(
                "public API may leave loopback only when Cloudflare Access verification is enabled"
            )
        if not _is_loopback(self.admin_host):
            raise ConfigurationError("Admin API must listen on a loopback address")
        if not _is_loopback(self.health_host):
            raise ConfigurationError("Health-only API must listen on a loopback address")
        if self.trust_cloudflare_access and not (
            self.cf_access_team_domain and self.cf_access_aud and self.public_origin
        ):
            raise ConfigurationError(
                "Cloudflare Access requires team domain, audience and PA_PUBLIC_ORIGIN"
            )
        if len({self.public_port, self.admin_port, self.health_port}) != 3:
            raise ConfigurationError("public, admin and health ports must be distinct")
