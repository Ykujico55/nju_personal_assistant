"""Pure, offline job selection and application planning.

PREPARED describes complete planning material, never permission to send.  This
module has no browser, network, filesystem, SDK, or host integration.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import unquote, urlsplit

MAX_DAILY_LIMIT = 100
MAX_RESUME_BYTES = 8 * 1024 * 1024
_JOB_PATH = re.compile(r"/job_detail/([A-Za-z0-9_-]{1,128})\.html\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_MEDIA_TYPE = re.compile(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+\Z")
_ACTION_KINDS = frozenset({"greet", "message", "send_resume"})


class DomainError(ValueError):
    """Validation failure identified without including private field contents."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _invalid(field_name: str) -> DomainError:
    return DomainError("INVALID_INPUT", f"{field_name} is invalid")


def _text(
    value: object, field_name: str, *, required: bool = False,
    maximum: int = 200, multiline: bool = False, strip: bool = True,
) -> str:
    if not isinstance(value, str):
        raise _invalid(field_name)
    allowed = "\n\r\t" if multiline else ""
    if any((ord(char) < 32 and char not in allowed) or ord(char) == 127 for char in value):
        raise _invalid(field_name)
    result = value.strip() if strip else value
    if len(result) > maximum or (required and not result.strip()):
        raise _invalid(field_name)
    return result


def normalize_boss_url(url: str) -> str:
    """Accept one exact BOSS HTTPS job page; discard tracking query/fragment."""
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise _invalid("job URL")
    if "\\" in url or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url):
        raise _invalid("job URL")
    decoded = unquote(url)
    if any(ord(char) < 32 or ord(char) == 127 for char in decoded):
        raise _invalid("job URL")
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme.lower() != "https" or parsed.hostname != "www.zhipin.com"
            or parsed.username is not None or parsed.password is not None
            or parsed.port not in (None, 443)
        ):
            raise _invalid("job URL")
    except ValueError as exc:
        if isinstance(exc, DomainError):
            raise
        raise _invalid("job URL") from None
    match = _JOB_PATH.fullmatch(parsed.path)
    if match is None:
        raise DomainError("JOB_ID_REQUIRED", "an explicit BOSS job-detail ID is required")
    return "https://www.zhipin.com" + parsed.path


def boss_job_id(url: str) -> str:
    canonical = normalize_boss_url(url)
    match = _JOB_PATH.fullmatch(urlsplit(canonical).path)
    assert match is not None
    return match.group(1)


def canonical_digest(value: object) -> str:
    """Canonical JSON digest; rejects unsupported values and non-finite numbers."""
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise _invalid("canonical JSON material") from None
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class Job:
    url: str
    title: str
    company: str
    job_id: str = ""
    recruiter: str = ""
    location: str = ""
    salary_text: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        canonical = normalize_boss_url(self.url)
        derived_id = boss_job_id(canonical)
        if not isinstance(self.job_id, str) or (self.job_id and self.job_id != derived_id):
            raise DomainError("JOB_ID_MISMATCH", "job ID does not match the job URL")
        object.__setattr__(self, "url", canonical)
        object.__setattr__(self, "job_id", derived_id)
        for name, maximum, required in (
            ("title", 300, True), ("company", 300, True), ("recruiter", 200, False),
            ("location", 200, False), ("salary_text", 200, False),
        ):
            object.__setattr__(self, name, _text(
                getattr(self, name), name, required=required, maximum=maximum,
            ))
        object.__setattr__(self, "description", _text(
            self.description, "description", maximum=65_536, multiline=True,
        ))

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> Job:
        fields = ("url", "title", "company", "job_id", "recruiter", "location",
                  "salary_text", "description")
        supplied: dict[str, str] = {}
        for name in fields:
            raw = value.get(name, "")
            if raw is None and name not in {"url", "title", "company"}:
                raw = ""
            if not isinstance(raw, str):
                raise _invalid(name)
            supplied[name] = raw
        return cls(**supplied)

    def as_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in (
            "job_id", "url", "title", "company", "recruiter", "location",
            "salary_text", "description",
        )}


def application_key(job: Job) -> str:
    """One candidate/application identity survives URL tracking or resume edits."""
    return canonical_digest({"platform": "boss", "job_id": job.job_id})


def _strings(value: Sequence[str], field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or len(value) > 100:
        raise _invalid(field_name)
    return tuple(dict.fromkeys(_text(item, field_name, required=True, maximum=256)
                               for item in value))


@dataclass(frozen=True, slots=True)
class FilterSpec:
    include_keywords: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()
    company_denylist: tuple[str, ...] = ()
    daily_limit: int = 10

    def __post_init__(self) -> None:
        for name in ("include_keywords", "exclude_keywords", "locations", "company_denylist"):
            object.__setattr__(self, name, _strings(getattr(self, name), name))
        if type(self.daily_limit) is not int or not 1 <= self.daily_limit <= MAX_DAILY_LIMIT:
            raise _invalid("daily_limit")


def filter_jobs(
    jobs: Iterable[Job], spec: FilterSpec, *, already_prepared_today: int = 0,
) -> tuple[Job, ...]:
    """Stable first-seen selection; keyword/city inclusion is OR, denylist exact."""
    if type(already_prepared_today) is not int or already_prepared_today < 0:
        raise _invalid("already_prepared_today")
    remaining = max(0, spec.daily_limit - already_prepared_today)
    if not remaining:
        return ()
    denied = {value.casefold() for value in spec.company_denylist}
    include = tuple(value.casefold() for value in spec.include_keywords)
    exclude = tuple(value.casefold() for value in spec.exclude_keywords)
    locations = tuple(value.casefold() for value in spec.locations)
    result: list[Job] = []
    seen: set[str] = set()
    for job in jobs:
        identity = application_key(job)
        if identity in seen:
            continue
        seen.add(identity)
        haystack = "\n".join((job.title, job.company, job.location,
                              job.salary_text, job.description)).casefold()
        if job.company.casefold() in denied:
            continue
        if include and not any(value in haystack for value in include):
            continue
        if any(value in haystack for value in exclude):
            continue
        if locations and not any(value in job.location.casefold() for value in locations):
            continue
        result.append(job)
        if len(result) == remaining:
            break
    return tuple(result)


@dataclass(frozen=True, slots=True)
class ResumeRef:
    filename: str
    sha256: str
    size_bytes: int
    media_type: str = "application/pdf"
    artifact_id: str = ""

    def __post_init__(self) -> None:
        name = _text(self.filename, "resume filename", required=True, maximum=180)
        if name in {".", ".."} or any(char in name for char in '/\\<>:"|?*'):
            raise _invalid("resume filename")
        digest = _text(self.sha256, "resume SHA-256", required=True, maximum=64).lower()
        if _SHA256.fullmatch(digest) is None:
            raise _invalid("resume SHA-256")
        if type(self.size_bytes) is not int or not 1 <= self.size_bytes <= MAX_RESUME_BYTES:
            raise _invalid("resume size")
        media = _text(self.media_type, "resume media type", required=True, maximum=128).lower()
        if _MEDIA_TYPE.fullmatch(media) is None:
            raise _invalid("resume media type")
        artifact = _text(self.artifact_id, "resume artifact ID", maximum=128)
        object.__setattr__(self, "filename", name)
        object.__setattr__(self, "sha256", digest)
        object.__setattr__(self, "media_type", media)
        object.__setattr__(self, "artifact_id", artifact)

    def as_dict(self) -> dict[str, object]:
        return {"filename": self.filename, "sha256": self.sha256,
                "size_bytes": self.size_bytes, "media_type": self.media_type,
                "artifact_id": self.artifact_id}


class ApplicationState(StrEnum):
    IMPORTED = "IMPORTED"
    NEEDS_USER = "NEEDS_USER"
    PREPARED = "PREPARED"
    EXECUTING = "EXECUTING"
    GREETING_SENT = "GREETING_SENT"
    MESSAGE_SENT = "MESSAGE_SENT"
    WAITING_REPLY = "WAITING_REPLY"
    RESUME_SENT = "RESUME_SENT"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class ApplicationPlan:
    job: Job
    action_kind: str
    message: str = ""
    resume: ResumeRef | None = None
    state: ApplicationState = field(init=False)
    reasons: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.action_kind, str) or self.action_kind not in _ACTION_KINDS:
            raise _invalid("action_kind")
        message = _text(self.message, "message", maximum=4000, multiline=True, strip=False)
        if self.resume is not None and not isinstance(self.resume, ResumeRef):
            raise _invalid("resume reference")
        reasons = []
        if not self.job.recruiter:
            reasons.append("RECRUITER_REQUIRED")
        if not message.strip():
            reasons.append("MESSAGE_REQUIRED")
        if self.action_kind == "send_resume" and self.resume is None:
            reasons.append("RESUME_REQUIRED")
        object.__setattr__(self, "reasons", tuple(reasons))
        object.__setattr__(self, "state", ApplicationState.NEEDS_USER if reasons
                           else ApplicationState.PREPARED)

    def as_command(self) -> dict[str, object]:
        return {
            "platform": "boss", "action_kind": self.action_kind,
            "target": {"job_id": self.job.job_id, "url": self.job.url,
                       "title": self.job.title, "company": self.job.company,
                       "recruiter": self.job.recruiter},
            "message": self.message,
            "resume": self.resume.as_dict() if self.resume else None,
        }

    @property
    def command_digest(self) -> str:
        return canonical_digest(self.as_command())

    @property
    def dedupe_key(self) -> str:
        return canonical_digest({"application": application_key(self.job),
                                 "action_kind": self.action_kind})

    def as_dict(self) -> dict[str, object]:
        return {"job": self.job.as_dict(), "action_kind": self.action_kind,
                "message": self.message, "resume": self.resume.as_dict() if self.resume else None,
                "state": self.state.value, "reasons": list(self.reasons),
                "command_digest": self.command_digest, "dedupe_key": self.dedupe_key}


def build_plan(
    job: Job, action_kind: str, *, message: str = "", resume: ResumeRef | None = None,
) -> ApplicationPlan:
    return ApplicationPlan(job=job, action_kind=action_kind, message=message, resume=resume)


_TRANSITIONS: dict[ApplicationState, frozenset[ApplicationState]] = {
    ApplicationState.IMPORTED: frozenset({ApplicationState.PREPARED, ApplicationState.NEEDS_USER,
                                          ApplicationState.CANCELLED}),
    ApplicationState.NEEDS_USER: frozenset({ApplicationState.PREPARED, ApplicationState.CANCELLED}),
    ApplicationState.PREPARED: frozenset({ApplicationState.EXECUTING, ApplicationState.NEEDS_USER,
                                        ApplicationState.CANCELLED}),
    ApplicationState.EXECUTING: frozenset({ApplicationState.GREETING_SENT,
                                          ApplicationState.MESSAGE_SENT,
                                          ApplicationState.RESUME_SENT, ApplicationState.FAILED,
                                          ApplicationState.UNKNOWN}),
    ApplicationState.GREETING_SENT: frozenset({ApplicationState.WAITING_REPLY,
                                              ApplicationState.EXECUTING}),
    ApplicationState.MESSAGE_SENT: frozenset({ApplicationState.WAITING_REPLY,
                                             ApplicationState.EXECUTING}),
    ApplicationState.WAITING_REPLY: frozenset({ApplicationState.PREPARED,
                                              ApplicationState.EXECUTING,
                                              ApplicationState.CANCELLED}),
    ApplicationState.FAILED: frozenset({ApplicationState.PREPARED, ApplicationState.CANCELLED}),
    ApplicationState.RESUME_SENT: frozenset(),
    ApplicationState.UNKNOWN: frozenset(),
    ApplicationState.CANCELLED: frozenset(),
}


def _state(value: ApplicationState | str) -> ApplicationState:
    try:
        return ApplicationState(value)
    except (ValueError, TypeError):
        raise _invalid("application state") from None


def state_after_import(existing: ApplicationState | str | None) -> ApplicationState:
    """Re-importing job data cannot erase an application's recorded outcome."""
    return ApplicationState.IMPORTED if existing is None else _state(existing)


def transition_state(
    current: ApplicationState | str, target: ApplicationState | str,
) -> ApplicationState:
    before, after = _state(current), _state(target)
    if before == after:
        return before
    if before is ApplicationState.UNKNOWN:
        raise DomainError(
            "RECONCILIATION_REQUIRED", "an UNKNOWN outcome cannot be retried or reset",
        )
    if after not in _TRANSITIONS[before]:
        raise DomainError("INVALID_TRANSITION", "the application state transition is not allowed")
    return after


def reconcile_state(
    current: ApplicationState | str, proven_outcome: ApplicationState | str, *,
    evidence_reference: str,
) -> ApplicationState:
    """Record an explicit reconciliation reference, without claiming to verify it.

    This pure helper has no remote evidence source.  A caller must retain and
    validate the referenced evidence; this is never an automatic retry path.
    """
    before, after = _state(current), _state(proven_outcome)
    _text(evidence_reference, "evidence_reference", required=True, maximum=512)
    if before is not ApplicationState.UNKNOWN or after not in {
        ApplicationState.GREETING_SENT, ApplicationState.MESSAGE_SENT,
        ApplicationState.RESUME_SENT, ApplicationState.FAILED,
    }:
        raise DomainError("INVALID_RECONCILIATION", "reconciliation requires an UNKNOWN outcome")
    return after
