"""Configuration and error types for the supervised ehall extension."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class EhallError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True, slots=True)
class EhallSettings:
    browser_origin: str
    allowed_paths: tuple[str, ...] = ()

    @property
    def configured(self) -> bool:
        return bool(self.browser_origin)


def parse_settings(config: Mapping[str, Any]) -> EhallSettings:
    origin = config.get("browser_origin")
    if origin is None:
        return EhallSettings(browser_origin="")
    if not isinstance(origin, str) or not origin.startswith("https://"):
        raise EhallError("EHALL_CONFIG_INVALID", "browser_origin must be an https origin")
    raw_paths = config.get("allowed_paths")
    paths: tuple[str, ...] = ()
    if isinstance(raw_paths, Sequence) and not isinstance(raw_paths, (str, bytes)):
        cleaned = []
        for item in raw_paths:
            if not isinstance(item, str) or not item.startswith("/"):
                raise EhallError("EHALL_CONFIG_INVALID", "allowed_paths must start with '/'")
            if ".." in item or "\\" in item:
                raise EhallError("EHALL_CONFIG_INVALID", "allowed_paths must be static")
            cleaned.append(item)
        paths = tuple(cleaned)
    return EhallSettings(browser_origin=origin.rstrip("/"), allowed_paths=paths)


__all__ = ["EhallError", "EhallSettings", "parse_settings"]
