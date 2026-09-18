"""Version-stable chunking.

Chunks never cross section boundaries and always carry a locator whose slice is
exactly the chunk text.  Chunk ``content_hash`` is the SHA-256 of the chunk text,
so repeated builds of the same bytes produce identical rows and identifiers.
"""

from __future__ import annotations

import hashlib

from .models import Chunk, ExtractedDocument, ExtractedSection, Locator

MAX_CHUNK_CHARS = 1200
MAX_CHUNK_BYTES = 64 * 1024


def chunk_document(
    document: ExtractedDocument, *, max_chars: int = MAX_CHUNK_CHARS
) -> tuple[Chunk, ...]:
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars <= 0:
        raise ValueError("max_chars must be a positive integer")
    chunks: list[Chunk] = []
    ordinal = 0
    for section in document.sections:
        for text, locator in _split_section(document, section, max_chars=max_chars):
            if not text.strip():
                continue
            chunks.append(
                Chunk(
                    ordinal=ordinal,
                    text=text,
                    locator=locator,
                    heading_path=section.heading_path,
                    content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                )
            )
            ordinal += 1
    return tuple(chunks)


def _split_section(
    document: ExtractedDocument, section: ExtractedSection, *, max_chars: int
) -> list[tuple[str, Locator]]:
    if (
        len(section.text) <= max_chars
        and len(section.text.encode("utf-8")) <= MAX_CHUNK_BYTES
    ):
        return [(section.text, section.locator)]
    if section.locator.kind == "line_range":
        return _split_lines(document, section, max_chars=max_chars)
    if section.locator.kind == "page_fragment":
        spans = _bounded_spans(section.text, max_chars=max_chars)
        return [
            (
                section.text[start:end],
                Locator(
                    kind="page_fragment",
                    start=section.locator.start + start,
                    end=section.locator.start + end,
                    page=section.locator.page,
                    label=section.locator.label,
                ),
            )
            for start, end in spans
        ]
    return [(section.text, section.locator)]


def _split_lines(
    document: ExtractedDocument, section: ExtractedSection, *, max_chars: int
) -> list[tuple[str, Locator]]:
    lines = section.text.split("\n")
    first_line = section.locator.start
    pieces: list[tuple[str, Locator]] = []
    buffer: list[str] = []
    buffer_start = first_line

    def flush(end_line: int) -> None:
        nonlocal buffer
        if not buffer:
            return
        pieces.append(
            (
                "\n".join(buffer),
                Locator(
                    kind="line_range",
                    start=buffer_start,
                    end=end_line,
                    label=section.locator.label,
                ),
            )
        )
        buffer = []

    for offset, line in enumerate(lines):
        line_number = first_line + offset
        if len(line) > max_chars or len(line.encode("utf-8")) > MAX_CHUNK_BYTES:
            flush(line_number - 1)
            base = _line_offset(document.text, line_number)
            for start, end in _bounded_spans(line, max_chars=max_chars):
                pieces.append(
                    (
                        line[start:end],
                        Locator(
                            kind="document_fragment",
                            start=base + start,
                            end=base + end,
                            label=section.locator.label,
                        ),
                    )
                )
            buffer_start = line_number + 1
            continue
        candidate = "\n".join([*buffer, line])
        if buffer and (
            len(candidate) > max_chars
            or len(candidate.encode("utf-8")) > MAX_CHUNK_BYTES
        ):
            flush(line_number - 1)
            buffer_start = line_number
        buffer.append(line)
    flush(first_line + len(lines) - 1)
    return pieces


def _bounded_spans(text: str, *, max_chars: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        while end > start and len(text[start:end].encode("utf-8")) > MAX_CHUNK_BYTES:
            end = start + max(1, (end - start) // 2)
        if end <= start:
            raise ValueError("unable to create a bounded UTF-8 chunk")
        spans.append((start, end))
        start = end
    return spans


def _line_offset(text: str, line_number: int) -> int:
    if line_number <= 1:
        return 0
    lines = text.split("\n")
    return sum(len(line) + 1 for line in lines[: line_number - 1])


def rebuild_chunk_text(
    document: ExtractedDocument, locator: Locator
) -> str | None:
    """Re-derive the exact text for a locator; used by verification tests."""

    if locator.kind == "line_range":
        lines = document.text.split("\n")
        if locator.start < 1 or locator.end > len(lines) or locator.start > locator.end:
            return None
        return "\n".join(lines[locator.start - 1 : locator.end])
    if locator.kind == "page_fragment":
        if locator.page is None or locator.page < 1:
            return None
        pages = document.text.split("\f")
        if locator.page > len(pages):
            return None
        page_text = pages[locator.page - 1]
        if locator.start < 0 or locator.end > len(page_text) or locator.start > locator.end:
            return None
        return page_text[locator.start : locator.end]
    if locator.kind == "document_fragment":
        if locator.start < 0 or locator.end > len(document.text) or locator.start > locator.end:
            return None
        return document.text[locator.start : locator.end]
    return None


__all__ = [
    "MAX_CHUNK_BYTES",
    "MAX_CHUNK_CHARS",
    "chunk_document",
    "rebuild_chunk_text",
]
