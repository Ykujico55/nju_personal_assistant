"""Local public model settings; no secret resolution and no implicit service restart."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from personal_assistant.infrastructure.models.setup import KEYS
from personal_assistant.settings import normalize_model_id


class ModelSettingsError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ModelSettings:
    def __init__(
        self,
        path: Path,
        *,
        profile: bool = False,
        defaults_path: Path | None = None,
    ) -> None:
        self.path = path.absolute()
        self.profile = profile
        self.defaults_path = defaults_path
        self._receipts: dict[str, str] = {}

    @staticmethod
    def _read(path: Path) -> tuple[bytes, dict[str, str]]:
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
                raise ModelSettingsError("MODEL_CONFIG_UNAVAILABLE")
            raw = path.read_bytes()
            value = json.loads(raw)
            if not isinstance(value, dict) or any(
                not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()
            ):
                raise ModelSettingsError("MODEL_CONFIG_INVALID")
            return raw, value
        except (OSError, ValueError) as exc:
            if isinstance(exc, ModelSettingsError):
                raise
            raise ModelSettingsError("MODEL_CONFIG_UNAVAILABLE") from None

    def _snapshot(self) -> tuple[str, dict[str, str], dict[str, str]]:
        raw, original = self._read(self.path)
        if not self.profile and set(original) != KEYS:
            raise ModelSettingsError("MODEL_CONFIG_INVALID")
        defaults_raw = b""
        defaults: dict[str, str] = {}
        if self.profile and not KEYS.issubset(original):
            if self.defaults_path is None:
                raise ModelSettingsError("MODEL_CONFIG_UNAVAILABLE")
            defaults_raw, defaults = self._read(self.defaults_path)
            if set(defaults) != KEYS:
                raise ModelSettingsError("MODEL_CONFIG_INVALID")
        effective = {key: original.get(key, defaults.get(key, "")) for key in KEYS}
        if not all(effective.values()):
            raise ModelSettingsError("MODEL_CONFIG_UNAVAILABLE")
        if effective["PA_MODEL_REMOTE_JSON_MODE"] not in {"true", "false"} or effective[
            "PA_MODEL_REMOTE_THINKING"
        ] not in {"enabled", "disabled"}:
            raise ModelSettingsError("MODEL_CONFIG_INVALID")
        revision = hashlib.sha256(raw + b"\0" + defaults_raw).hexdigest()
        return revision, original, effective

    def load(self) -> dict[str, Any]:
        revision, _, config = self._snapshot()
        return {
            "provider": config["PA_MODEL_REMOTE_PROVIDER_ID"],
            "base_url": config["PA_MODEL_REMOTE_BASE_URL"],
            "model": config["PA_MODEL_REMOTE_MODEL"],
            "json_mode": config["PA_MODEL_REMOTE_JSON_MODE"] == "true",
            "thinking": config["PA_MODEL_REMOTE_THINKING"],
            "credential_configured": bool(config["PA_MODEL_REMOTE_SECRET_HANDLE"]),
            "revision": revision,
            "path": str(self.path),
            "restart_required": False,
        }

    def save(self, fields: dict[str, Any], key: str) -> dict[str, Any]:
        allowed = {"model", "json_mode", "thinking", "revision"}
        if set(fields) != allowed or not isinstance(key, str) or not 1 <= len(key) <= 256:
            raise ModelSettingsError("MODEL_SETTINGS_INVALID")
        if (
            not isinstance(fields["model"], str)
            or type(fields["json_mode"]) is not bool
            or fields["thinking"] not in {"enabled", "disabled"}
            or not isinstance(fields["revision"], str)
        ):
            raise ModelSettingsError("MODEL_SETTINGS_INVALID")
        try:
            normalized = normalize_model_id(fields["model"], field_name="model")
        except ValueError:
            raise ModelSettingsError("MODEL_SETTINGS_INVALID") from None
        request_hash = hashlib.sha256(
            json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        if key in self._receipts:
            if self._receipts[key] != request_hash:
                raise ModelSettingsError("MODEL_SETTINGS_KEY_CONFLICT")
            return {**self.load(), "restart_required": True}

        lock = self.path.with_name(self.path.name + ".workbench-lock")
        temporary = self.path.with_name("." + self.path.name + "." + uuid4().hex + ".tmp")
        try:
            with lock.open("x", encoding="utf-8") as stream:
                stream.write("workbench settings write\n")
        except OSError:
            raise ModelSettingsError("MODEL_SETTINGS_BUSY") from None
        try:
            revision, original, config = self._snapshot()
            if revision != fields["revision"]:
                raise ModelSettingsError("MODEL_SETTINGS_CHANGED")
            config.update(
                {
                    "PA_MODEL_REMOTE_MODEL": normalized,
                    "PA_MODEL_REMOTE_JSON_MODE": str(fields["json_mode"]).lower(),
                    "PA_MODEL_REMOTE_THINKING": fields["thinking"],
                }
            )
            value = {**original, **config}
            with temporary.open("x", encoding="utf-8") as output:
                json.dump(value, output, ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            if self._snapshot()[0] != revision:
                raise ModelSettingsError("MODEL_SETTINGS_CHANGED")
            os.replace(temporary, self.path)
            if len(self._receipts) >= 32:
                self._receipts.pop(next(iter(self._receipts)))
            self._receipts[key] = request_hash
            return {**self.load(), "restart_required": True}
        except OSError:
            raise ModelSettingsError("MODEL_SETTINGS_WRITE_FAILED") from None
        finally:
            temporary.unlink(missing_ok=True)
            lock.unlink(missing_ok=True)
