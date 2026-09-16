"""Small, dependency-free environment configuration.

Environment loading is intentionally left to the process manager or uvicorn's
``--env-file`` option so importing the package never performs file I/O.
"""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


class ConfigurationError(RuntimeError):
    pass


_CLOUDFLARE_ACCESS_SUFFIX = ".cloudflareaccess.com"
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_AUDIENCE = re.compile(r"^[\x21-\x7e]{1,256}$")


def normalize_team_domain(raw: str) -> str:
    """Canonicalize a Cloudflare Access team domain to an SSRF-safe https origin.

    Accepts ``team``, ``team.cloudflareaccess.com`` or
    ``https://team.cloudflareaccess.com``.  Anything carrying a scheme other
    than https, credentials, a port, a path, a query, a fragment or a host that
    is not a single label under ``cloudflareaccess.com`` is rejected.
    """

    value = raw.strip().lower()
    if not value:
        raise ConfigurationError("PA_CF_ACCESS_TEAM_DOMAIN must not be empty")
    if "://" in value:
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise ConfigurationError("PA_CF_ACCESS_TEAM_DOMAIN is not a valid URL") from exc
        if parsed.scheme != "https" or parsed.username or parsed.password or port is not None:
            raise ConfigurationError(
                "PA_CF_ACCESS_TEAM_DOMAIN must be an https URL without credentials or port"
            )
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ConfigurationError(
                "PA_CF_ACCESS_TEAM_DOMAIN must not contain a path, query or fragment"
            )
        host = parsed.hostname or ""
        if not host.endswith(_CLOUDFLARE_ACCESS_SUFFIX):
            raise ConfigurationError(
                "PA_CF_ACCESS_TEAM_DOMAIN must be a Cloudflare Access team domain"
            )
        label = host[: -len(_CLOUDFLARE_ACCESS_SUFFIX)]
    else:
        if any(character in value for character in "/@?#:"):
            raise ConfigurationError("PA_CF_ACCESS_TEAM_DOMAIN contains illegal characters")
        host = value
        if host.endswith(_CLOUDFLARE_ACCESS_SUFFIX):
            label = host[: -len(_CLOUDFLARE_ACCESS_SUFFIX)]
        elif "." not in host:
            label = host
        else:
            raise ConfigurationError(
                "PA_CF_ACCESS_TEAM_DOMAIN must be a Cloudflare Access team domain"
            )
    if not _DNS_LABEL.fullmatch(label):
        raise ConfigurationError("PA_CF_ACCESS_TEAM_DOMAIN has an invalid team name")
    return f"https://{label}{_CLOUDFLARE_ACCESS_SUFFIX}"


def normalize_public_origin(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise ConfigurationError("PA_PUBLIC_ORIGIN must not be empty")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ConfigurationError("PA_PUBLIC_ORIGIN is not a valid URL") from exc
    if parsed.scheme != "https":
        raise ConfigurationError("PA_PUBLIC_ORIGIN must use https")
    if not parsed.hostname:
        raise ConfigurationError("PA_PUBLIC_ORIGIN must include a host")
    if parsed.username or parsed.password:
        raise ConfigurationError("PA_PUBLIC_ORIGIN must not include credentials")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ConfigurationError("PA_PUBLIC_ORIGIN must not include a path, query or fragment")
    host = parsed.hostname.lower()
    netloc = host if port is None else f"{host}:{port}"
    return f"https://{netloc}"


def normalize_audience(raw: str) -> str:
    value = raw.strip()
    if not _AUDIENCE.fullmatch(value):
        raise ConfigurationError(
            "PA_CF_ACCESS_AUD must be 1-256 printable characters without spaces"
        )
    return value


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

    def __post_init__(self) -> None:
        # Every construction path (from_env, direct construction, replace)
        # writes back canonical values and validates, so an instance always
        # satisfies the documented normalization invariant and a hand-built
        # Settings cannot bypass the Access boundary.
        if self.cf_access_team_domain is not None:
            object.__setattr__(
                self,
                "cf_access_team_domain",
                normalize_team_domain(self.cf_access_team_domain),
            )
        if self.cf_access_aud is not None:
            object.__setattr__(
                self, "cf_access_aud", normalize_audience(self.cf_access_aud)
            )
        if self.public_origin is not None:
            object.__setattr__(
                self, "public_origin", normalize_public_origin(self.public_origin)
            )
        self.validate()

    @classmethod
    def from_env(cls) -> Settings:
        team_domain = os.getenv("PA_CF_ACCESS_TEAM_DOMAIN") or None
        audience = os.getenv("PA_CF_ACCESS_AUD") or None
        public_origin = os.getenv("PA_PUBLIC_ORIGIN") or None
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
            public_origin=normalize_public_origin(public_origin) if public_origin else None,
            cf_access_team_domain=normalize_team_domain(team_domain) if team_domain else None,
            cf_access_aud=normalize_audience(audience) if audience else None,
        )
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
        # Re-validate even hand-built Settings so a misconfigured instance cannot
        # silently keep trusting unauthenticated remote traffic.
        if self.cf_access_team_domain is not None:
            normalize_team_domain(self.cf_access_team_domain)
        if self.cf_access_aud is not None:
            normalize_audience(self.cf_access_aud)
        if self.public_origin is not None and (
            normalize_public_origin(self.public_origin) != self.public_origin
        ):
            raise ConfigurationError(
                "PA_PUBLIC_ORIGIN must be stored in normalized https://host form"
            )
        if len({self.public_port, self.admin_port, self.health_port}) != 3:
            raise ConfigurationError("public, admin and health ports must be distinct")
