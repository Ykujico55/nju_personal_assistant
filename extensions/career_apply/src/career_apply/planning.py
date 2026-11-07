"""Pure application material plans and bounded local resume inspection."""
from __future__ import annotations

import hashlib
import io
import re
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from string import Template

from .domain import MAX_RESUME_BYTES, Job, ResumeRef, build_plan, canonical_digest
from .sources import normalize_recipient

_ALLOWED_TEMPLATE_FIELDS = frozenset({"name", "email", "company", "position"})


def _text(value: object, *, header: bool, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("application text is empty or exceeds its limit")
    allowed = "" if header else "\r\n\t"
    if any((ord(c) < 32 and c not in allowed) or ord(c) == 127 for c in value):
        raise ValueError("application text contains unsupported control characters")
    return value


def read_resume(path: Path) -> ResumeRef:
    """Hash a user-selected local file without exporting its absolute path."""
    path = Path(path)
    selected = str(path)
    is_drive = bool(re.match(r"^[A-Za-z]:[/\\]", selected))
    if ((re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", selected) and not is_drive)
            or selected.startswith(("//", "\\\\"))):
        raise ValueError("resume must be a local file")
    formats = {".pdf": "application/pdf", ".docx":
               "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
               ".txt": "text/plain"}
    suffix = path.suffix.casefold()
    if suffix not in formats:
        raise ValueError("supported resumes are PDF, DOCX and UTF-8 TXT")
    try:
        with path.open("rb") as stream:
            payload = stream.read(MAX_RESUME_BYTES + 1)
    except OSError:
        raise ValueError("the local resume cannot be read") from None
    if not payload or len(payload) > MAX_RESUME_BYTES:
        raise ValueError("resume must be nonempty and at most 8 MiB")
    if suffix == ".pdf" and not payload.startswith(b"%PDF-"):
        raise ValueError("resume content does not match PDF format")
    if suffix == ".txt":
        try:
            content = payload.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError("text resumes must use UTF-8") from None
        if "\x00" in content or not content.strip():
            raise ValueError("text resume is empty or contains binary data")
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                entries = archive.infolist()
                if len(entries) > 1000 or not {"[Content_Types].xml", "word/document.xml"} <= {
                    item.filename for item in entries
                }:
                    raise ValueError("resume content does not match DOCX format")
                if any(item.file_size > 16 * 1024 * 1024 for item in entries):
                    raise ValueError("DOCX contents exceed the inspection limit")
        except zipfile.BadZipFile:
            raise ValueError("resume content does not match DOCX format") from None
    return ResumeRef(filename=path.name, sha256=hashlib.sha256(payload).hexdigest(),
                     size_bytes=len(payload), media_type=formats[suffix])


def boss_plan(
    job: Job, action_kind: str, *, message: str = "", resume: ResumeRef | None = None,
) -> dict[str, object]:
    plan = build_plan(job, action_kind, message=message, resume=resume).as_dict()
    return {**plan, "platform": "boss", "execution_available": False}


def _render(template: str, recipient: Mapping[str, str]) -> str:
    material = Template(template)
    if not material.is_valid() or any(
        name not in _ALLOWED_TEMPLATE_FIELDS for name in material.get_identifiers()
    ):
        raise ValueError("mail template contains an unknown or invalid field")
    return material.substitute(recipient)


def mail_plans(
    recipients: Iterable[Mapping[str, object]], subject: str, body: str,
    resume: ResumeRef | None = None,
) -> list[dict[str, object]]:
    """Prepare one exact plan per recipient; never send or resolve a mail host."""
    _text(subject, header=True, maximum=200)
    _text(body, header=False, maximum=20_000)
    if resume is not None and not isinstance(resume, ResumeRef):
        raise ValueError("resume metadata is invalid")
    results: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, item in enumerate(recipients):
        if index >= 1000:
            raise ValueError("recipient count exceeds 1000")
        recipient = normalize_recipient(item)
        identity = recipient["email"].casefold()
        if identity in seen:
            continue
        seen.add(identity)
        exact_subject = _text(_render(subject, recipient), header=True, maximum=200)
        exact_body = _text(_render(body, recipient), header=False, maximum=20_000)
        command = {"platform": "email", "recipient": recipient,
                   "subject": exact_subject, "body": exact_body,
                   "resume": resume.as_dict() if resume else None}
        results.append({**command, "state": "PREPARED" if resume else "NEEDS_USER",
                        "reasons": [] if resume else ["RESUME_REQUIRED"],
                        "execution_available": False,
                        "dedupe_key": "email:" + canonical_digest({
                            "email": identity, "company": recipient["company"],
                            "position": recipient["position"],
                        }), "command_digest": canonical_digest(command)})
    return results
