"""Bounded, local-only import adapters for job and recipient records.

Page text and unrecognised metadata are data, never instructions. This module
has no network, model, browser or execution dependency.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .domain import DomainError, Job

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_RECORDS = 1000
MAX_FIELD_CHARACTERS = 10_000
MAX_FIELDS = 128

JOB_FIELDS = {
    "url": ("url", "job_url", "job_url_direct", "url_direct", "链接", "职位链接"),
    "title": ("title", "job_title", "职位", "职位名称", "岗位"),
    "company": ("company", "company_name", "公司", "公司名称"),
    "recruiter": ("recruiter", "recruiter_name", "hr", "HR", "招聘者"),
    "location": ("location", "city", "城市", "地点", "工作地点"),
    "salary_text": ("salary_text", "salary", "薪资", "薪资待遇"),
    "description": ("description", "job_description", "职位描述", "描述"),
}
RECIPIENT_FIELDS = {
    "email": ("email", "email_address", "recipient", "邮箱", "收件人"),
    "name": ("name", "recipient_name", "姓名", "联系人"),
    "company": ("company", "company_name", "公司", "公司名称"),
    "position": ("position", "title", "职位", "岗位"),
}
_LOCAL_PART = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.\-]+\Z")
_DOMAIN_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_URI = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_DRIVE = re.compile(r"^[A-Za-z]:[/\\]")


class SourceError(ValueError):
    """A local import failed without returning partial, unvalidated data."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _error(code: str, message: str) -> SourceError:
    return SourceError(code, message)


def _local_text(path: Path, formats: frozenset[str]) -> tuple[str, str]:
    try:
        selected = Path(path)
    except (TypeError, ValueError):
        raise _error("SOURCE_PATH_INVALID", "a local file path is required") from None
    text = str(selected)
    if (_URI.match(text) and not _DRIVE.match(text)) or text.startswith(("//", "\\\\")):
        raise _error("SOURCE_PATH_INVALID", "network and URI sources are not supported")
    suffix = selected.suffix.casefold()
    if suffix not in formats:
        raise _error("SOURCE_FORMAT_UNSUPPORTED", "unsupported local file format")
    try:
        with selected.open("rb") as stream:
            payload = stream.read(MAX_FILE_BYTES + 1)
    except OSError:
        raise _error("SOURCE_UNREADABLE", "the local source cannot be read") from None
    if len(payload) > MAX_FILE_BYTES:
        raise _error("SOURCE_TOO_LARGE", "the source exceeds the 2 MiB limit")
    try:
        return payload.decode("utf-8-sig"), suffix
    except UnicodeDecodeError:
        raise _error("SOURCE_ENCODING_INVALID", "the source must use UTF-8") from None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _error("SOURCE_JSON_DUPLICATE_KEY", "JSON keys must be unique")
        result[key] = value
    return result


def _nonfinite(_: str) -> None:
    raise _error("SOURCE_JSON_INVALID", "nonfinite JSON numbers are not allowed")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise _error("SOURCE_JSON_INVALID", "nonfinite JSON numbers are not allowed")
    return parsed


def _json_rows(text: str, key: str) -> list[Any]:
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_nonfinite,
            parse_float=_finite_float,
        )
    except SourceError:
        raise
    except (ValueError, RecursionError):
        raise _error(
            "SOURCE_JSON_INVALID", "the source is not a valid bounded JSON document"
        ) from None
    if isinstance(value, dict):
        value = value.get(key)
    if not isinstance(value, list):
        raise _error("SOURCE_RECORDS_INVALID", f"expected a JSON array or an object with {key}")
    return value


def _csv_rows(text: str) -> list[dict[str, Any]]:
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        headers = reader.fieldnames
        if not headers:
            raise _error("SOURCE_CSV_INVALID", "CSV headers are required")
        clean = [header.strip() for header in headers]
        if any(not header for header in clean) or len(clean) != len(set(clean)):
            raise _error("SOURCE_CSV_INVALID", "CSV headers must be nonempty and unique")
        if len(clean) > MAX_FIELDS:
            raise _error("SOURCE_CSV_INVALID", "too many CSV fields")
        reader.fieldnames = clean
        rows: list[dict[str, Any]] = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise _error("SOURCE_CSV_INVALID", "CSV row length differs from its headers")
            rows.append(row)
            if len(rows) > MAX_RECORDS:
                raise _error("SOURCE_TOO_MANY_RECORDS", "the source exceeds 1000 records")
        return rows
    except csv.Error:
        raise _error("SOURCE_CSV_INVALID", "the source is not valid CSV") from None


def _bounded_rows(rows: list[Any]) -> list[Any]:
    if len(rows) > MAX_RECORDS:
        raise _error("SOURCE_TOO_MANY_RECORDS", "the source exceeds 1000 records")
    for row in rows:
        if isinstance(row, str) and len(row) > MAX_FIELD_CHARACTERS:
            raise _error("SOURCE_FIELD_TOO_LARGE", "a field exceeds 10000 characters")
        if isinstance(row, Mapping):
            if len(row) > MAX_FIELDS:
                raise _error("SOURCE_RECORD_INVALID", "a record has too many fields")
            if any(
                isinstance(value, str) and len(value) > MAX_FIELD_CHARACTERS
                for value in row.values()
            ):
                raise _error("SOURCE_FIELD_TOO_LARGE", "a field exceeds 10000 characters")
    return rows


def _fields(row: Mapping[str, Any], aliases: dict[str, tuple[str, ...]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for field, names in aliases.items():
        value: Any = ""
        for name in names:
            candidate = row.get(name)
            if candidate is not None and candidate != "":
                value = candidate
                break
        if not isinstance(value, str):
            raise _error("SOURCE_RECORD_INVALID", "recognised fields must contain text")
        result[field] = value
    return result


def load_jobs(path: Path) -> tuple[dict[str, object], ...]:
    """Read BOSS records from JSON/CSV; retain only normalised domain fields."""
    text, suffix = _local_text(path, frozenset({".json", ".csv"}))
    rows = _bounded_rows(_json_rows(text, "jobs") if suffix == ".json" else _csv_rows(text))
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise _error("SOURCE_RECORD_INVALID", "job records must be objects")
        try:
            job = Job.from_mapping(_fields(row, JOB_FIELDS))
        except DomainError:
            raise _error("SOURCE_JOB_INVALID", "a BOSS job identity or field is invalid") from None
        if job.url not in seen:
            result.append(job.as_dict())
            seen.add(job.url)
    return tuple(result)


def _email(value: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise _error("SOURCE_EMAIL_INVALID", "email addresses cannot contain control characters")
    address = value.strip()
    if len(address) > 254 or address.count("@") != 1:
        raise _error("SOURCE_EMAIL_INVALID", "a single email address is required")
    local, domain = address.split("@")
    if (
        len(local) > 64
        or not _LOCAL_PART.fullmatch(local)
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
    ):
        raise _error("SOURCE_EMAIL_INVALID", "email local part is invalid")
    try:
        domain = domain.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise _error("SOURCE_EMAIL_INVALID", "email domain is invalid") from None
    labels = domain.split(".")
    if len(domain) > 253 or len(labels) < 2 or any(not _DOMAIN_LABEL.fullmatch(x) for x in labels):
        raise _error("SOURCE_EMAIL_INVALID", "email domain is invalid")
    return local + "@" + domain


def normalize_recipient(value: Mapping[str, object]) -> dict[str, str]:
    """Validate one JSON-shaped recipient without reading a file or using a host."""
    if not isinstance(value, Mapping):
        raise _error("SOURCE_RECORD_INVALID", "recipient records must be objects")
    _bounded_rows([value])
    recipient = _fields(value, RECIPIENT_FIELDS)
    recipient["email"] = _email(recipient["email"])
    for field in ("name", "company", "position"):
        text = recipient[field]
        if any(ord(character) < 32 or ord(character) == 127 for character in text):
            raise _error("SOURCE_RECORD_INVALID", "recipient metadata cannot contain controls")
        recipient[field] = text.strip()
    return recipient


def load_recipients(path: Path) -> tuple[dict[str, str], ...]:
    """Read a local recipient list without sending messages or resolving hosts."""
    text, suffix = _local_text(path, frozenset({".json", ".csv", ".txt"}))
    if suffix == ".json":
        rows = _json_rows(text, "recipients")
    elif suffix == ".csv":
        rows = _csv_rows(text)
    else:
        rows = [
            line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
        ]
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in _bounded_rows(rows):
        if isinstance(row, str):
            row = {"email": row}
        if not isinstance(row, Mapping):
            raise _error("SOURCE_RECORD_INVALID", "recipient records must be objects or emails")
        recipient = normalize_recipient(row)
        identity = recipient["email"].casefold()
        if identity not in seen:
            seen.add(identity)
            result.append(recipient)
    return tuple(result)


__all__ = ["SourceError", "load_jobs", "load_recipients", "normalize_recipient"]
