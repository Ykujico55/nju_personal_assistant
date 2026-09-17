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
from urllib.parse import unquote, urlsplit


class ConfigurationError(RuntimeError):
    pass


_CLOUDFLARE_ACCESS_SUFFIX = ".cloudflareaccess.com"
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_AUDIENCE = re.compile(r"^[\x21-\x7e]{1,256}$")
_MODEL_ID = re.compile(r"^[\x21-\x7e]{1,128}$")
_PROVIDER_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_SECRET_HANDLE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_MAX_MODEL_TIMEOUT_SECONDS = 600.0


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


def normalize_model_endpoint(
    raw: str,
    *,
    field_name: str,
    allow_http: bool = False,
    require_loopback: bool = False,
) -> str:
    """Canonicalize a model endpoint and reject credential-smuggling URLs.

    Remote endpoints must be https.  Local endpoints may use http, but the host
    must be a loopback address so a "local" provider can never be pointed at a
    remote service to bypass the disclosure-consent boundary.
    """

    value = raw.strip()
    if not value:
        raise ConfigurationError(f"{field_name} must not be empty")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ConfigurationError(f"{field_name} is not a valid URL") from exc
    allowed_schemes = {"https", "http"} if allow_http else {"https"}
    if parsed.scheme not in allowed_schemes:
        raise ConfigurationError(
            f"{field_name} must use {'https or http' if allow_http else 'https'}"
        )
    if not parsed.hostname:
        raise ConfigurationError(f"{field_name} must include a host")
    if parsed.username or parsed.password:
        raise ConfigurationError(f"{field_name} must not include credentials")
    if parsed.query or parsed.fragment:
        raise ConfigurationError(f"{field_name} must not include a query or fragment")
    if require_loopback and not _is_loopback(parsed.hostname):
        raise ConfigurationError(f"{field_name} must be a loopback address")
    host = parsed.hostname.lower()
    path = parsed.path.rstrip("/")
    decoded_path = path
    for _ in range(3):
        candidate = unquote(decoded_path)
        if candidate == decoded_path:
            break
        decoded_path = candidate
    if any(character.isspace() for character in decoded_path):
        raise ConfigurationError(f"{field_name} contains illegal characters")
    if any(segment in (".", "..") for segment in decoded_path.split("/")):
        raise ConfigurationError(f"{field_name} must not contain '.' or '..' path segments")
    netloc = host if port is None else f"{host}:{port}"
    return f"{parsed.scheme}://{netloc}{path}"


def normalize_model_id(raw: str, *, field_name: str) -> str:
    value = raw.strip()
    if not _MODEL_ID.fullmatch(value):
        raise ConfigurationError(
            f"{field_name} must be 1-128 printable characters without spaces"
        )
    return value


def normalize_provider_id(raw: str, *, field_name: str) -> str:
    value = raw.strip()
    if not _PROVIDER_ID.fullmatch(value):
        raise ConfigurationError(
            f"{field_name} must be 1-64 characters of [A-Za-z0-9._:-]"
        )
    return value


def normalize_secret_handle_id(raw: str, *, field_name: str) -> str:
    value = raw.strip()
    if not _SECRET_HANDLE_ID.fullmatch(value):
        raise ConfigurationError(
            f"{field_name} must be 1-128 opaque characters of [A-Za-z0-9._:-]"
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


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
    if not 0 < value <= _MAX_MODEL_TIMEOUT_SECONDS:
        raise ConfigurationError(
            f"{name} must be between 0 and {_MAX_MODEL_TIMEOUT_SECONDS:g} seconds"
        )
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
    model_remote_provider_id: str = "remote.openai"
    model_remote_base_url: str | None = None
    model_remote_model: str | None = None
    model_remote_secret_handle: str | None = None
    model_remote_timeout_seconds: float = 60.0
    model_local_provider_id: str = "local.ollama"
    model_local_base_url: str | None = None
    model_local_model: str | None = None
    model_local_timeout_seconds: float = 120.0
    model_local_fallback_provider_id: str | None = None

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
        object.__setattr__(
            self,
            "model_remote_provider_id",
            normalize_provider_id(
                self.model_remote_provider_id, field_name="PA_MODEL_REMOTE_PROVIDER_ID"
            ),
        )
        object.__setattr__(
            self,
            "model_local_provider_id",
            normalize_provider_id(
                self.model_local_provider_id, field_name="PA_MODEL_LOCAL_PROVIDER_ID"
            ),
        )
        if self.model_remote_base_url is not None:
            object.__setattr__(
                self,
                "model_remote_base_url",
                normalize_model_endpoint(
                    self.model_remote_base_url, field_name="PA_MODEL_REMOTE_BASE_URL"
                ),
            )
        if self.model_local_base_url is not None:
            object.__setattr__(
                self,
                "model_local_base_url",
                normalize_model_endpoint(
                    self.model_local_base_url,
                    field_name="PA_MODEL_LOCAL_BASE_URL",
                    allow_http=True,
                    require_loopback=True,
                ),
            )
        if self.model_remote_model is not None:
            object.__setattr__(
                self,
                "model_remote_model",
                normalize_model_id(self.model_remote_model, field_name="PA_MODEL_REMOTE_MODEL"),
            )
        if self.model_local_model is not None:
            object.__setattr__(
                self,
                "model_local_model",
                normalize_model_id(self.model_local_model, field_name="PA_MODEL_LOCAL_MODEL"),
            )
        if self.model_remote_secret_handle is not None:
            object.__setattr__(
                self,
                "model_remote_secret_handle",
                normalize_secret_handle_id(
                    self.model_remote_secret_handle,
                    field_name="PA_MODEL_REMOTE_SECRET_HANDLE",
                ),
            )
        if self.model_local_fallback_provider_id is not None:
            object.__setattr__(
                self,
                "model_local_fallback_provider_id",
                normalize_provider_id(
                    self.model_local_fallback_provider_id,
                    field_name="PA_MODEL_LOCAL_FALLBACK_PROVIDER_ID",
                ),
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
            model_remote_provider_id=os.getenv(
                "PA_MODEL_REMOTE_PROVIDER_ID", "remote.openai"
            ).strip(),
            model_remote_base_url=os.getenv("PA_MODEL_REMOTE_BASE_URL") or None,
            model_remote_model=os.getenv("PA_MODEL_REMOTE_MODEL") or None,
            model_remote_secret_handle=os.getenv("PA_MODEL_REMOTE_SECRET_HANDLE") or None,
            model_remote_timeout_seconds=_env_float("PA_MODEL_REMOTE_TIMEOUT_SECONDS", 60.0),
            model_local_provider_id=os.getenv(
                "PA_MODEL_LOCAL_PROVIDER_ID", "local.ollama"
            ).strip(),
            model_local_base_url=os.getenv("PA_MODEL_LOCAL_BASE_URL") or None,
            model_local_model=os.getenv("PA_MODEL_LOCAL_MODEL") or None,
            model_local_timeout_seconds=_env_float("PA_MODEL_LOCAL_TIMEOUT_SECONDS", 120.0),
            model_local_fallback_provider_id=(
                os.getenv("PA_MODEL_LOCAL_FALLBACK_PROVIDER_ID") or None
            ),
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
        for timeout, field_name in (
            (self.model_remote_timeout_seconds, "PA_MODEL_REMOTE_TIMEOUT_SECONDS"),
            (self.model_local_timeout_seconds, "PA_MODEL_LOCAL_TIMEOUT_SECONDS"),
        ):
            if not 0 < timeout <= _MAX_MODEL_TIMEOUT_SECONDS:
                raise ConfigurationError(
                    f"{field_name} must be between 0 and "
                    f"{_MAX_MODEL_TIMEOUT_SECONDS:g} seconds"
                )
        if self.model_remote_base_url is not None:
            if (
                normalize_model_endpoint(
                    self.model_remote_base_url, field_name="PA_MODEL_REMOTE_BASE_URL"
                )
                != self.model_remote_base_url
            ):
                raise ConfigurationError(
                    "PA_MODEL_REMOTE_BASE_URL must be stored in normalized https form"
                )
            if not self.model_remote_model:
                raise ConfigurationError(
                    "remote model endpoint requires PA_MODEL_REMOTE_MODEL"
                )
            if not self.model_remote_secret_handle:
                raise ConfigurationError(
                    "remote model endpoint requires PA_MODEL_REMOTE_SECRET_HANDLE"
                )
        else:
            if self.model_remote_model is not None:
                raise ConfigurationError(
                    "PA_MODEL_REMOTE_MODEL requires PA_MODEL_REMOTE_BASE_URL"
                )
            if self.model_remote_secret_handle is not None:
                raise ConfigurationError(
                    "PA_MODEL_REMOTE_SECRET_HANDLE requires PA_MODEL_REMOTE_BASE_URL"
                )
        if self.model_local_base_url is not None:
            if (
                normalize_model_endpoint(
                    self.model_local_base_url,
                    field_name="PA_MODEL_LOCAL_BASE_URL",
                    allow_http=True,
                    require_loopback=True,
                )
                != self.model_local_base_url
            ):
                raise ConfigurationError(
                    "PA_MODEL_LOCAL_BASE_URL must be stored in normalized loopback form"
                )
            if not self.model_local_model:
                raise ConfigurationError(
                    "local model endpoint requires PA_MODEL_LOCAL_MODEL"
                )
        else:
            if self.model_local_model is not None:
                raise ConfigurationError(
                    "PA_MODEL_LOCAL_MODEL requires PA_MODEL_LOCAL_BASE_URL"
                )
            if self.model_local_fallback_provider_id is not None:
                raise ConfigurationError(
                    "PA_MODEL_LOCAL_FALLBACK_PROVIDER_ID requires a configured local model"
                )
        if self.model_local_fallback_provider_id is not None and (
            self.model_local_fallback_provider_id != self.model_local_provider_id
        ):
            raise ConfigurationError(
                "PA_MODEL_LOCAL_FALLBACK_PROVIDER_ID must name the configured local provider"
            )
        if (
            self.model_remote_base_url is not None
            and self.model_local_base_url is not None
            and self.model_remote_provider_id == self.model_local_provider_id
        ):
            raise ConfigurationError(
                "remote and local model providers must have distinct provider ids"
            )
        if len({self.public_port, self.admin_port, self.health_port}) != 3:
            raise ConfigurationError("public, admin and health ports must be distinct")
