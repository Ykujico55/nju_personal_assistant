"""Prepare inactive F08.6 templates or check local config without starting apps."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import shutil
from pathlib import Path
from urllib.parse import unquote, urlsplit

from dotenv import dotenv_values

from personal_assistant.settings import (
    ConfigurationError,
    Settings,
    normalize_audience,
    normalize_public_origin,
    normalize_team_domain,
)

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "PA_ENVIRONMENT", "PA_STORAGE_BACKEND", "PA_TRUST_CLOUDFLARE_ACCESS",
    "PA_DATABASE_URL", "PA_PUBLIC_ORIGIN", "PA_CF_ACCESS_TEAM_DOMAIN", "PA_CF_ACCESS_AUD",
)
LISTENERS = {"public": 8000, "admin": 8001, "health": 8010}


def fixed_origin(value: str) -> str:
    normalized = normalize_public_origin(value)
    parsed = urlsplit(normalized)
    host = parsed.hostname or ""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ConfigurationError("a fixed DNS hostname is required")
    if "." not in host or host.endswith(".localhost") or parsed.port is not None:
        raise ConfigurationError("a standard HTTPS DNS hostname is required")
    return normalized


def prepare(arguments: argparse.Namespace) -> int:
    try:
        origin = fixed_origin(arguments.public_origin) if arguments.public_origin else ""
        team = normalize_team_domain(arguments.team_domain) if arguments.team_domain else ""
        audience = normalize_audience(arguments.audience) if arguments.audience else ""
    except (ConfigurationError, ValueError):
        print(json.dumps({"created": False, "error": "INVALID_PUBLIC_CONFIGURATION"}))
        return 1

    destination = Path(arguments.output_dir)
    names = (".env.template", "cloudflared.template.yml", "START_HERE.md")
    if any((destination / name).exists() for name in names):
        print(json.dumps({"created": False, "error": "EXISTING_MATERIAL_PRESERVED"}))
        return 1
    values = {
        "PA_ENVIRONMENT": "production", "PA_LOG_LEVEL": "INFO",
        "PA_PUBLIC_HOST": "127.0.0.1", "PA_PUBLIC_PORT": "8000",
        "PA_ADMIN_HOST": "127.0.0.1", "PA_ADMIN_PORT": "8001",
        "PA_HEALTH_HOST": "127.0.0.1", "PA_HEALTH_PORT": "8010",
        "PA_STORAGE_BACKEND": "postgres", "PA_DATABASE_URL": "",
        "PA_EXTENSION_ROOT": "./var/extensions", "PA_ARTIFACT_ROOT": "./var/artifacts",
        "PA_TRUST_CLOUDFLARE_ACCESS": "true", "PA_PUBLIC_ORIGIN": origin,
        "PA_CF_ACCESS_TEAM_DOMAIN": team, "PA_CF_ACCESS_AUD": audience,
        "PA_MAIL_SEND_ENABLED": "false", "PA_BROWSER_COMPANION_URL": "",
        "PA_BROWSER_SUBMIT_ENABLED": "false",
    }
    environment = (
        "# Inactive candidate. Fill the selected database DSN privately.\n"
        "# Do not copy this to .env or start an app until the local check passes.\n"
        "# Push credentials require the trusted secret backend; never put keys here.\n"
    )
    for key, value in values.items():
        encoded = json.dumps(value) if key in {
            "PA_PUBLIC_ORIGIN", "PA_CF_ACCESS_TEAM_DOMAIN", "PA_CF_ACCESS_AUD"
        } and value else value
        environment += f"{key}={encoded}\n"
    hostname = urlsplit(origin).hostname if origin else "<fixed-hostname>"
    tunnel = (
        "# Inactive locally-managed Tunnel template. Protect the entire hostname\n"
        "# with Access before configuring or starting this tunnel.\n"
        "tunnel: <tunnel-uuid>\n"
        "credentials-file: <absolute-local-credentials-file>\n"
        "ingress:\n"
        f"  - hostname: {json.dumps(hostname)}\n"
        "    service: http://127.0.0.1:8000\n"
        "  - service: http_status:404\n"
    )
    instructions = (
        "# F08.6 inactive preparation\n\n"
        "1. Register Cloudflare and select a fixed domain/hostname.\n"
        "2. Create a self-hosted Access application for the entire hostname;\n"
        "   allow only your identity. Record the team domain and application AUD.\n"
        "3. Fill .env.template locally with those values and a deliberately selected\n"
        "   durable database DSN. Preserve existing data; this helper never migrates it.\n"
        "   Use literal values, not ${...} environment substitutions.\n"
        "   Check and launch from the same clean shell; inherited PA_* settings\n"
        "   must not override or add runtime configuration.\n"
        "4. From the repository run .venv-win\\Scripts\\python.exe\n"
        "   scripts/prepare_f08.py check --env-file <candidate-file>.\n"
        "5. Review Tunnel routing to public 8000 only; Tailscale Serve may proxy\n"
        "   only health 8010. Actual exposure needs a separate deployment step.\n"
        "6. Android installation/notification permission and network-switch tests\n"
        "   need the real device. Production Push remains unavailable until F09\n"
        "   provides the trusted secret backend.\n\n"
        "Do not share passwords, cert.pem, Tunnel credentials, JWTs or Push keys.\n"
        "See docs/NEXT_STEPS.md for acceptance evidence and remaining conditions.\n"
    )
    try:
        destination.mkdir(parents=True, exist_ok=True)
        for name, content in zip(names, (environment, tunnel, instructions), strict=True):
            with (destination / name).open("x", encoding="utf-8", newline="\n") as output:
                output.write(content)
    except OSError:
        print(json.dumps({"created": False, "error": "OUTPUT_WRITE_FAILED"}))
        return 1
    print(json.dumps({"created": True, "files": list(names), "activated": False}))
    return 0


def check(arguments: argparse.Namespace) -> int:
    report: dict[str, object] = {
        "local_configuration_valid": False, "errors": [],
        "database": "not_probed", "deployed_access_policy": "not_verified",
        "push_secret_backend": "unavailable_default",
        "tools_on_path": {name: shutil.which(name) is not None
                          for name in ("cloudflared", "tailscale", "docker")},
    }
    errors: list[dict[str, object]] = []
    try:
        with Path(arguments.env_file).open(encoding="utf-8") as source:
            values = {key: value for key, value in
                      dotenv_values(stream=source, interpolate=False).items() if value is not None}
    except (OSError, UnicodeError):
        errors.append({"code": "CONFIGURATION_UNREADABLE"})
    else:
        missing = [key for key in REQUIRED if not values.get(key)]
        if missing:
            errors.append({"code": "MISSING_FIELDS", "fields": missing})
        if any("${" in value for value in values.values()):
            errors.append({"code": "ENVIRONMENT_SUBSTITUTION"})
        # Uvicorn loads dotenv with override=False. Refuse a candidate that
        # would be changed at launch by inherited runtime configuration.
        if any(key.startswith("PA_") and not key.startswith("PA_TEST_")
               and value != values.get(key) for key, value in os.environ.items()):
            errors.append({"code": "AMBIENT_CONFIGURATION_CONFLICT"})
        saved_environment = os.environ.copy()
        try:
            os.environ.clear()
            os.environ.update(values)
            settings = Settings.from_env()
            fixed_origin(settings.public_origin or "")
            if settings.environment != "production" or settings.storage_backend != "postgres":
                errors.append({"code": "PRODUCTION_POSTGRES_REQUIRED"})
            if not settings.trust_cloudflare_access:
                errors.append({"code": "VERIFIED_ACCESS_REQUIRED"})
            for role, port in LISTENERS.items():
                if getattr(settings, f"{role}_host") != "127.0.0.1" or (
                    getattr(settings, f"{role}_port") != port
                ):
                    errors.append({"code": "LISTENER_MISMATCH", "role": role})
            database = urlsplit(settings.database_url)
            if database.scheme not in {"postgresql", "postgresql+asyncpg"} or (
                not database.hostname or not database.path.strip("/")
                or (database.port is not None and database.port < 1)
            ):
                errors.append({"code": "INVALID_DATABASE_URL"})
            if unquote(database.password or "") in {"change-me", "replace-me"}:
                errors.append({"code": "DATABASE_PLACEHOLDER"})
        except (ConfigurationError, ValueError, TypeError):
            # Exception text can contain submitted values. Never echo it.
            errors.append({"code": "INVALID_CONFIGURATION"})
        finally:
            os.environ.clear()
            os.environ.update(saved_environment)
    report["errors"] = errors
    report["local_configuration_valid"] = not errors
    print(json.dumps(report))
    return int(bool(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--output-dir", default=str(ROOT / "var/deployment/f08"))
    prepare_parser.add_argument("--public-origin")
    prepare_parser.add_argument("--team-domain")
    prepare_parser.add_argument("--audience")
    check_parser = commands.add_parser("check")
    check_parser.add_argument("--env-file", required=True)
    arguments = parser.parse_args()
    return prepare(arguments) if arguments.command == "prepare" else check(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
