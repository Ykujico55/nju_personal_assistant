"""Same-user Windows Generic Credentials behind the host SecretStorePort.

Only opaque references leave this boundary. CRED_PERSIST_LOCAL_MACHINE persists
across logons on this machine, without enterprise roaming. It does not grant
access to other users or protect secrets from trusted code running as this user.
"""
from __future__ import annotations

import asyncio
import ctypes
import re
import sys
from dataclasses import dataclass, field
from typing import Protocol
from uuid import uuid4

from personal_assistant.core.extensions.async_utils import run_blocking, shield_cleanup
from personal_assistant.core.secrets import SecretHandle, SecretUnavailableError

_MAX_BLOB = 5 * 512
_TOKEN = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
_HANDLE = re.compile(r"wcm1_[0-9a-f]{32}")
_ERROR_NOT_FOUND = 1168


@dataclass(frozen=True, slots=True)
class CredentialRecord:
    target: str
    username: str
    comment: str
    blob: bytearray = field(repr=False)
    credential_type: int = 1
    persist: int = 2


class CredentialApi(Protocol):
    def write(self, record: CredentialRecord) -> None: ...
    def read(self, target: str) -> CredentialRecord | None: ...
    def delete(self, target: str) -> None: ...


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _CredentialW(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint32),
        ("credential_type", ctypes.c_uint32),
        ("target", ctypes.c_wchar_p),
        ("comment", ctypes.c_wchar_p),
        ("last_written", _FileTime),
        ("blob_size", ctypes.c_uint32),
        ("blob", ctypes.POINTER(ctypes.c_ubyte)),
        ("persist", ctypes.c_uint32),
        ("attribute_count", ctypes.c_uint32),
        ("attributes", ctypes.c_void_p),
        ("target_alias", ctypes.c_wchar_p),
        ("username", ctypes.c_wchar_p),
    ]


class _NativeCredentialApi:
    def __init__(self) -> None:
        # Delay native loading until the Windows production backend is selected.
        self._dll = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        pointer = ctypes.POINTER(_CredentialW)
        self._dll.CredWriteW.argtypes = [pointer, ctypes.c_uint32]
        self._dll.CredWriteW.restype = ctypes.c_int32
        self._dll.CredReadW.argtypes = [
            ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(pointer)
        ]
        self._dll.CredReadW.restype = ctypes.c_int32
        self._dll.CredDeleteW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
        self._dll.CredDeleteW.restype = ctypes.c_int32
        self._dll.CredFree.argtypes = [ctypes.c_void_p]
        self._dll.CredFree.restype = None

    def write(self, record: CredentialRecord) -> None:
        size = len(record.blob)
        buffer = (ctypes.c_ubyte * size).from_buffer_copy(record.blob)
        credential = _CredentialW(
            credential_type=1, target=record.target, username=record.username,
            comment=record.comment, blob_size=size, blob=buffer, persist=2,
        )
        try:
            if not self._dll.CredWriteW(ctypes.byref(credential), 0):
                raise OSError(ctypes.get_last_error(), "credential write failed")
        finally:
            ctypes.memset(ctypes.addressof(buffer), 0, size)

    def read(self, target: str) -> CredentialRecord | None:
        pointer = ctypes.POINTER(_CredentialW)()
        if not self._dll.CredReadW(target, 1, 0, ctypes.byref(pointer)):
            code = ctypes.get_last_error()
            if code == _ERROR_NOT_FOUND:
                return None
            raise OSError(code, "credential read failed")
        credential = pointer.contents
        size = credential.blob_size
        try:
            if not 0 < size <= _MAX_BLOB or not credential.blob:
                raise ValueError("invalid credential blob")
            return CredentialRecord(
                target=credential.target or "", username=credential.username or "",
                comment=credential.comment or "",
                blob=bytearray(ctypes.string_at(credential.blob, size)),
                credential_type=credential.credential_type, persist=credential.persist,
            )
        finally:
            # Bound the length before touching native memory; always free the
            # complete OS allocation even when metadata or decoding is invalid.
            if 0 < size <= _MAX_BLOB and credential.blob:
                ctypes.memset(credential.blob, 0, size)
            self._dll.CredFree(pointer)

    def delete(self, target: str) -> None:
        if not self._dll.CredDeleteW(target, 1, 0):
            code = ctypes.get_last_error()
            if code != _ERROR_NOT_FOUND:
                raise OSError(code, "credential delete failed")


def _label(value: str, maximum: int) -> bool:
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= maximum
            and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def _wipe(blob: bytearray) -> None:
    blob[:] = b"\x00" * len(blob)


class WindowsCredentialManager:
    def __init__(
        self, *, namespace: str = "personal-assistant", api: CredentialApi | None = None
    ) -> None:
        if not _TOKEN.fullmatch(namespace):
            raise SecretUnavailableError("credential namespace is invalid")
        native: CredentialApi | None = api
        if native is None and sys.platform == "win32":
            try:
                native = _NativeCredentialApi()
            except Exception:
                native = None
        if native is None:
            raise SecretUnavailableError("Windows credential storage is unavailable")
        self._namespace = namespace
        self._api = native

    def _target(self, handle: SecretHandle) -> str:
        if not _TOKEN.fullmatch(handle.kind) or not _HANDLE.fullmatch(handle.id):
            raise ValueError("invalid credential reference")
        return f"PersonalAssistant/{self._namespace}/v1/{handle.kind}/{handle.id}"

    def _put(self, handle: SecretHandle, name: str, value: str) -> bool:
        blob = bytearray()
        try:
            target = self._target(handle)
            if not _label(name, 128) or not isinstance(value, str) or not value:
                return False
            blob = bytearray(value, "utf-8")
            if not 0 < len(blob) <= _MAX_BLOB:
                return False
            self._api.write(CredentialRecord(
                target=target, username=handle.kind, comment=name, blob=blob,
            ))
            return True
        except Exception:
            # Do not carry OS exceptions, codec errors or their payload-bearing
            # tracebacks into the public error. Failure never becomes success.
            return False
        finally:
            _wipe(blob)
            del value

    def _resolve(self, handle: SecretHandle, purpose: str) -> str | None:
        record: CredentialRecord | None = None
        try:
            target = self._target(handle)
            if not _label(purpose, 128):
                return None
            record = self._api.read(target)
            if record is None:
                return None
            if (record.target != target or record.username != handle.kind
                    or record.credential_type != 1 or record.persist != 2
                    or not 0 < len(record.blob) <= _MAX_BLOB):
                return None
            return record.blob.decode("utf-8")
        except Exception:
            return None
        finally:
            if record is not None:
                _wipe(record.blob)

    def _delete(self, handle: SecretHandle) -> bool:
        try:
            self._api.delete(self._target(handle))
            return True
        except Exception:
            return False

    async def put(self, *, name: str, kind: str, value: str) -> SecretHandle:
        handle = SecretHandle(id="wcm1_" + uuid4().hex, kind=kind)
        try:
            succeeded = await run_blocking(self._put, handle, name, value)
        except asyncio.CancelledError:
            # Wait for any started write before deleting this exact newly
            # allocated reference. Never enumerate or remove other credentials.
            await shield_cleanup(run_blocking(self._delete, handle))
            raise
        finally:
            del value
        if not succeeded:
            raise SecretUnavailableError("credential could not be stored")
        return handle

    async def resolve_for_broker(self, handle: SecretHandle, *, purpose: str) -> str:
        result = await run_blocking(self._resolve, handle, purpose)
        if result is None:
            raise SecretUnavailableError("credential is missing or unavailable")
        return result

    async def delete(self, handle: SecretHandle) -> None:
        if not await run_blocking(self._delete, handle):
            raise SecretUnavailableError("credential could not be deleted")
