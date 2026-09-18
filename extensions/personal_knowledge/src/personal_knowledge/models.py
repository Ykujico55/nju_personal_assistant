"""Internal models for the personal knowledge extension.

These types never cross the extension boundary except as JSON shaped values in
evidence metadata and tool output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

MEDIA_TYPE_BY_SUFFIX = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
}

ALLOWED_SUFFIXES = tuple(sorted(MEDIA_TYPE_BY_SUFFIX))


class EvidenceStatus(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    DELETED = "DELETED"


class ExtractionDiagnosticCode(StrEnum):
    EMPTY_FILE = "EMPTY_FILE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    DECODE_ERROR = "DECODE_ERROR"
    PDF_MALFORMED = "PDF_MALFORMED"
    PDF_ENCRYPTED = "PDF_ENCRYPTED"
    PDF_UNSUPPORTED_FILTER = "PDF_UNSUPPORTED_FILTER"
    READ_ERROR = "READ_ERROR"
    BUILD_FAILED = "BUILD_FAILED"


class ExtractionError(Exception):
    """Typed, non-fatal extraction failure; workers must not crash on it."""

    def __init__(self, code: ExtractionDiagnosticCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ExtractionDiagnostic:
    code: ExtractionDiagnosticCode
    message: str
    path: str = ""

    def as_json(self) -> dict[str, str]:
        return {"code": self.code.value, "message": self.message, "path": self.path}


@dataclass(frozen=True, slots=True)
class Locator:
    """Stable, re-verifiable position inside one source version.

    ``kind`` is ``line_range`` for Markdown/plain text (1-based inclusive lines)
    or ``page_fragment`` for PDF (1-based page, 0-based character offsets inside
    the extracted page text).
    """

    kind: str
    start: int
    end: int
    page: int | None = None
    label: str = ""

    def as_json(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "kind": self.kind,
            "start": self.start,
            "end": self.end,
            "label": self.label,
        }
        if self.page is not None:
            payload["page"] = self.page
        return payload


@dataclass(frozen=True, slots=True)
class ExtractedSection:
    text: str
    locator: Locator
    heading_path: tuple[str, ...] = ()
    level: int = 0


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    media_type: str
    text: str
    sections: tuple[ExtractedSection, ...]
    extractor_name: str
    extractor_version: str
    diagnostics: tuple[ExtractionDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class Chunk:
    ordinal: int
    text: str
    locator: Locator
    heading_path: tuple[str, ...] = ()
    content_hash: str = ""


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_id: str
    root_key: str
    relative_path: str
    media_type: str
    size_bytes: int
    mtime_ns: int
    content_hash: str
    active_version: str | None
    generation: int = 0


@dataclass(frozen=True, slots=True)
class ChunkRecord:
    source_id: str
    version_id: str
    ordinal: int
    text: str
    locator: Locator
    heading_path: tuple[str, ...]
    content_hash: str
    root_key: str
    relative_path: str
    media_type: str
    source_hash: str


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    results: tuple[ChunkRecord, ...]
    scores: tuple[float, ...]
    unknown: bool
    vector_mode: str


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One fused hit with its verified status.

    Only ``CURRENT`` results carry chunk text.  ``STALE``/``DELETED`` results
    keep metadata (source, locator, hashes, status) but never the cached body,
    so an invalidated source can never reach a model or the user as content.
    """

    record: ChunkRecord
    status: EvidenceStatus
    score: float

    @property
    def text(self) -> str:
        return self.record.text if self.status == EvidenceStatus.CURRENT else ""


@dataclass(frozen=True, slots=True)
class ScanEntry:
    root_key: str
    relative_path: str
    absolute_path: str
    media_type: str
    size_bytes: int
    mtime_ns: int
    content_hash: str
    diagnostics: tuple[ExtractionDiagnostic, ...] = field(default_factory=tuple)


@dataclass(slots=True)
class ReconcileReport:
    mode: str
    roots: int = 0
    scanned: int = 0
    added: int = 0
    modified: int = 0
    moved: int = 0
    deleted: int = 0
    unchanged: int = 0
    versions_built: int = 0
    events: int = 0
    errors: tuple[ExtractionDiagnostic, ...] = ()

    def as_json(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "roots": self.roots,
            "scanned": self.scanned,
            "added": self.added,
            "modified": self.modified,
            "moved": self.moved,
            "deleted": self.deleted,
            "unchanged": self.unchanged,
            "versions_built": self.versions_built,
            "events": self.events,
            "errors": [item.as_json() for item in self.errors],
        }
