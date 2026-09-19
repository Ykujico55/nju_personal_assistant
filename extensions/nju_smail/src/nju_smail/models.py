"""Typed configuration and reporting models for the smail extension."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEFAULT_FOLDERS = ("INBOX",)
DEFAULT_MAX_MESSAGES = 50
DEFAULT_MAX_MESSAGE_BYTES = 2 * 1024 * 1024
MAX_ACCOUNTS = 4


class MailConfigError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DraftConflictError(MailConfigError):
    """A concurrent or replayed draft edit did not apply."""


@dataclass(frozen=True, slots=True)
class AccountConfig:
    """Non-secret account reference; the host owns endpoints and credentials."""

    account_id: str
    display_name: str = ""


@dataclass(frozen=True, slots=True)
class ExtensionSettings:
    accounts: tuple[AccountConfig, ...]
    folders: tuple[str, ...] = DEFAULT_FOLDERS
    poll_interval_seconds: int = 300
    max_messages_per_sync: int = DEFAULT_MAX_MESSAGES
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES

    def account(self, account_id: str) -> AccountConfig:
        for item in self.accounts:
            if item.account_id == account_id:
                return item
        raise MailConfigError("SMAL_ACCOUNT_UNKNOWN", "the account is not configured")

    @property
    def configured(self) -> bool:
        return bool(self.accounts)


@dataclass(frozen=True, slots=True)
class FolderSyncReport:
    account_id: str
    folder: str
    status: str
    new_messages: int = 0
    events: int = 0
    scanned: int = 0
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class SyncReport:
    folders: tuple[FolderSyncReport, ...] = field(default_factory=tuple)

    @property
    def new_messages(self) -> int:
        return sum(item.new_messages for item in self.folders)

    @property
    def events(self) -> int:
        return sum(item.events for item in self.folders)

    def as_json(self) -> dict[str, Any]:
        return {
            "accounts": [
                {
                    "account_id": item.account_id,
                    "folder": item.folder,
                    "status": item.status,
                    "new_messages": item.new_messages,
                    "events": item.events,
                    "scanned": item.scanned,
                    "error_code": item.error_code,
                }
                for item in self.folders
            ],
            "new_messages": self.new_messages,
            "events": self.events,
        }


def parse_settings(config: dict[str, Any]) -> ExtensionSettings:
    raw_accounts = config.get("accounts", [])
    if raw_accounts is None:
        raw_accounts = []
    if not isinstance(raw_accounts, list):
        raise MailConfigError("SMAL_CONFIG_INVALID", "accounts must be a list")
    if len(raw_accounts) > MAX_ACCOUNTS:
        raise MailConfigError("SMAL_CONFIG_INVALID", "too many accounts configured")
    accounts = tuple(_account(item) for item in raw_accounts)
    identifiers = [item.account_id for item in accounts]
    if len(set(identifiers)) != len(identifiers):
        raise MailConfigError("SMAL_CONFIG_INVALID", "account ids must be unique")
    folders_raw = config.get("folders", list(DEFAULT_FOLDERS))
    if not isinstance(folders_raw, list) or not folders_raw:
        raise MailConfigError("SMAL_CONFIG_INVALID", "folders must be a non-empty list")
    folders = tuple(_folder(item) for item in folders_raw)
    poll = _bounded_int(config.get("poll_interval_seconds", 300), 60, 3600)
    max_messages = _bounded_int(
        config.get("max_messages_per_sync", DEFAULT_MAX_MESSAGES), 1, 200
    )
    max_bytes = _bounded_int(
        config.get("max_message_bytes", DEFAULT_MAX_MESSAGE_BYTES),
        65536,
        8 * 1024 * 1024,
    )
    return ExtensionSettings(
        accounts=accounts,
        folders=folders,
        poll_interval_seconds=poll,
        max_messages_per_sync=max_messages,
        max_message_bytes=max_bytes,
    )


def _account(value: Any) -> AccountConfig:
    if not isinstance(value, dict):
        raise MailConfigError("SMAL_CONFIG_INVALID", "each account must be an object")
    allowed = {"account_id", "display_name"}
    unknown = set(value) - allowed
    if unknown:
        raise MailConfigError(
            "SMAL_CONFIG_INVALID",
            "account fields must reference the host registry: " + ", ".join(sorted(unknown)),
        )
    return AccountConfig(
        account_id=_text(value.get("account_id"), "account_id", 64),
        display_name=str(value.get("display_name", ""))[:120],
    )


def _folder(value: Any) -> str:
    return _text(value, "folder", 255)


def _text(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MailConfigError("SMAL_CONFIG_INVALID", f"{name} must be a non-empty string")
    if len(value) > limit or "\r" in value or "\n" in value:
        raise MailConfigError("SMAL_CONFIG_INVALID", f"{name} is invalid")
    return value.strip()


def _bounded_int(value: Any, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MailConfigError("SMAL_CONFIG_INVALID", "expected an integer setting")
    if not minimum <= value <= maximum:
        raise MailConfigError("SMAL_CONFIG_INVALID", "setting is out of range")
    return int(value)


__all__ = [
    "AccountConfig",
    "DEFAULT_FOLDERS",
    "ExtensionSettings",
    "FolderSyncReport",
    "MailConfigError",
    "SyncReport",
    "parse_settings",
]
