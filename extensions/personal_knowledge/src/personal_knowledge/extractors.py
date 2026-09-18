"""Deterministic extractors with stable, re-verifiable locators.

Markdown and plain text use 1-based inclusive line ranges; PDF uses 1-based page
numbers plus 0-based character offsets inside the extracted page text.  A
locator always maps back to the exact same substring when the same extractor
version re-reads the same bytes, which is what citation verification checks.

PDF support is deliberately dependency-free: classic (non-encrypted, non-object
stream) PDF content streams with ``FlateDecode`` are parsed with the standard
library.  Other constructs produce typed diagnostics instead of garbage text.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass

from .models import (
    ExtractedDocument,
    ExtractedSection,
    ExtractionDiagnostic,
    ExtractionDiagnosticCode,
    ExtractionError,
    Locator,
)

EXTRACTOR_VERSION = "1.0.0"
EXTRACTOR_NAME_LINE = "line-paragraph"
EXTRACTOR_NAME_MARKDOWN = "markdown-line-paragraph"
EXTRACTOR_NAME_TEXT = "plain-line-paragraph"
EXTRACTOR_NAME_PDF = "stdlib-pdf"
MAX_PDF_PAGES = 200
MAX_PDF_STREAM_BYTES = 8 * 1024 * 1024
MAX_PDF_TEXT_CHARS = 4 * 1024 * 1024
MAX_PAGE_FRAGMENT_CHARS = 700
MAX_TEXT_CHARS = 8 * 1024 * 1024


def extract_document(
    data: bytes,
    media_type: str,
    *,
    path: str = "",
    max_file_bytes: int | None = None,
) -> ExtractedDocument:
    if max_file_bytes is not None and len(data) > max_file_bytes:
        raise ExtractionError(
            ExtractionDiagnosticCode.FILE_TOO_LARGE, "file exceeds the configured size limit"
        )
    if media_type == "text/markdown":
        return _extract_lines(data, markdown=True, path=path)
    if media_type == "text/plain":
        return _extract_lines(data, markdown=False, path=path)
    if media_type == "application/pdf":
        return _extract_pdf(data, path=path)
    raise ExtractionError(
        ExtractionDiagnosticCode.UNSUPPORTED_FORMAT, f"unsupported media type: {media_type}"
    )


def _decode_text(data: bytes) -> str:
    if b"\x00" in data[:4096]:
        raise ExtractionError(
            ExtractionDiagnosticCode.DECODE_ERROR, "file does not look like UTF-8 text"
        )
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ExtractionError(
            ExtractionDiagnosticCode.DECODE_ERROR, "file is not valid UTF-8"
        ) from exc
    if len(text) > MAX_TEXT_CHARS:
        raise ExtractionError(
            ExtractionDiagnosticCode.FILE_TOO_LARGE, "text exceeds the extractor size limit"
        )
    if not text.strip():
        raise ExtractionError(ExtractionDiagnosticCode.EMPTY_FILE, "file has no text content")
    return text


def _extract_lines(data: bytes, *, markdown: bool, path: str) -> ExtractedDocument:
    text = _decode_text(data)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    sections: list[ExtractedSection] = []
    heading_path: tuple[str, ...] = ()
    block_start: int | None = None
    in_fence = False

    def flush(end_line: int) -> None:
        nonlocal block_start
        if block_start is None:
            return
        body = "\n".join(lines[block_start - 1 : end_line])
        sections.append(
            ExtractedSection(
                text=body,
                locator=Locator(
                    kind="line_range",
                    start=block_start,
                    end=end_line,
                    label=_first_line_label(body),
                ),
                heading_path=heading_path,
            )
        )
        block_start = None

    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        fence = stripped.startswith("```") or stripped.startswith("~~~")
        heading = None if (in_fence or not markdown) else _heading(line)
        if heading is not None:
            flush(number - 1)
            level, title = heading
            heading_path = heading_path[: level - 1] + (title,)
            sections.append(
                ExtractedSection(
                    text=line,
                    locator=Locator(kind="line_range", start=number, end=number, label=title),
                    heading_path=heading_path,
                    level=level,
                )
            )
            continue
        if not stripped and not in_fence:
            flush(number - 1)
            continue
        if block_start is None:
            block_start = number
        if fence:
            in_fence = not in_fence
    flush(len(lines))
    if not sections:
        raise ExtractionError(ExtractionDiagnosticCode.EMPTY_FILE, "file has no text content")
    return ExtractedDocument(
        media_type="text/markdown" if markdown else "text/plain",
        text=text,
        sections=tuple(sections),
        extractor_name=EXTRACTOR_NAME_MARKDOWN if markdown else EXTRACTOR_NAME_TEXT,
        extractor_version=EXTRACTOR_VERSION,
    )


def extractor_name_for(media_type: str) -> str:
    if media_type == "application/pdf":
        return EXTRACTOR_NAME_PDF
    if media_type == "text/markdown":
        return EXTRACTOR_NAME_MARKDOWN
    if media_type == "text/plain":
        return EXTRACTOR_NAME_TEXT
    raise ExtractionError(
        ExtractionDiagnosticCode.UNSUPPORTED_FORMAT, f"unsupported media type: {media_type}"
    )


def _heading(line: str) -> tuple[int, str] | None:
    match = re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)
    if match is None:
        return None
    return len(match.group(1)), match.group(2)


def _first_line_label(body: str) -> str:
    first = body.strip().splitlines()[0] if body.strip() else ""
    return first[:80]


_OBJECT_RE = re.compile(rb"(\d+)\s+(\d+)\s+obj\b(.*?)\bendobj", re.DOTALL)
_STREAM_RE = re.compile(rb"<<(.*?)>>\s*stream\r?\n(.*?)(?:\r?\n)?endstream", re.DOTALL)
_PAGE_RE = re.compile(rb"/Type\s*/Page(?![A-Za-z])")
_KIDS_RE = re.compile(rb"/Kids\s*\[(.*?)\]", re.DOTALL)
_REF_RE = re.compile(rb"(\d+)\s+\d+\s+R")
_CONTENTS_RE = re.compile(rb"/Contents\s*(\[[^\]]*\]|\d+\s+\d+\s+R)", re.DOTALL)
_FILTER_RE = re.compile(rb"/Filter\s*(\[[^\]]*\]|/\w+)", re.DOTALL)
_TOKEN_RE = re.compile(rb"\((?:\\.|[^\\()])*\)|<[0-9A-Fa-f\s]*>|\[|\]|[-+]?[0-9.]+|[A-Za-z*'\"]+")
_NUMBER_RE = re.compile(rb"^[-+]?[0-9.]+$")
_OPERATOR_RE = re.compile(rb"^[A-Za-z*'\"]+$")
_TJ_STACK_DEPTH = 32


@dataclass(frozen=True, slots=True)
class _PdfObject:
    number: int
    header: bytes
    stream: bytes | None


def _extract_pdf(data: bytes, *, path: str) -> ExtractedDocument:
    if not data[:1024].lstrip().startswith(b"%PDF-"):
        raise ExtractionError(ExtractionDiagnosticCode.PDF_MALFORMED, "missing PDF header")
    if b"/Encrypt" in data:
        raise ExtractionError(
            ExtractionDiagnosticCode.PDF_ENCRYPTED, "encrypted PDFs are not supported"
        )
    objects: dict[int, _PdfObject] = {}
    for match in _OBJECT_RE.finditer(data):
        number = int(match.group(1))
        body = match.group(3)
        header = body
        stream: bytes | None = None
        stream_match = _STREAM_RE.search(body)
        if stream_match is not None:
            header = stream_match.group(1)
            stream = stream_match.group(2)
            if len(stream) > MAX_PDF_STREAM_BYTES:
                raise ExtractionError(
                    ExtractionDiagnosticCode.PDF_MALFORMED, "PDF stream exceeds the size limit"
                )
        objects[number] = _PdfObject(number=number, header=header, stream=stream)
    page_numbers = [
        number for number, item in objects.items() if _PAGE_RE.search(item.header)
    ]
    if not page_numbers:
        raise ExtractionError(
            ExtractionDiagnosticCode.PDF_MALFORMED, "no page objects were found"
        )
    page_numbers = _order_pages(objects, page_numbers)[:MAX_PDF_PAGES]
    diagnostics: list[ExtractionDiagnostic] = []
    pages: list[str] = []
    total_chars = 0
    for page_number in page_numbers:
        item = objects[page_number]
        try:
            content = _page_content(objects, item)
        except ExtractionError as exc:
            if exc.code == ExtractionDiagnosticCode.PDF_UNSUPPORTED_FILTER:
                diagnostics.append(
                    ExtractionDiagnostic(code=exc.code, message=str(exc), path=path)
                )
                pages.append("")
                continue
            raise
        page_text = _content_to_text(content)
        total_chars += len(page_text)
        if total_chars > MAX_PDF_TEXT_CHARS:
            raise ExtractionError(
                ExtractionDiagnosticCode.FILE_TOO_LARGE, "PDF text exceeds the extractor limit"
            )
        pages.append(page_text)
    sections: list[ExtractedSection] = []
    for page_index, page_text in enumerate(pages, start=1):
        if not page_text.strip():
            continue
        for start, end in _fragment_ranges(page_text, MAX_PAGE_FRAGMENT_CHARS):
            body = page_text[start:end]
            if not body.strip():
                continue
            sections.append(
                ExtractedSection(
                    text=body,
                    locator=Locator(
                        kind="page_fragment",
                        start=start,
                        end=end,
                        page=page_index,
                        label=f"page {page_index}",
                    ),
                )
            )
    if not sections:
        if diagnostics:
            return ExtractedDocument(
                media_type="application/pdf",
                text="",
                sections=(),
                extractor_name=EXTRACTOR_NAME_PDF,
                extractor_version=EXTRACTOR_VERSION,
                diagnostics=tuple(diagnostics),
            )
        raise ExtractionError(
            ExtractionDiagnosticCode.EMPTY_FILE, "PDF contains no extractable text"
        )
    return ExtractedDocument(
        media_type="application/pdf",
        text="\f".join(pages),
        sections=tuple(sections),
        extractor_name=EXTRACTOR_NAME_PDF,
        extractor_version=EXTRACTOR_VERSION,
        diagnostics=tuple(diagnostics),
    )


def _order_pages(
    objects: dict[int, _PdfObject], page_numbers: list[int]
) -> list[int]:
    for item in objects.values():
        if _PAGE_RE.search(item.header):
            continue
        kids = _KIDS_RE.search(item.header)
        if kids is None:
            continue
        order = [int(ref.group(1)) for ref in _REF_RE.finditer(kids.group(1))]
        ordered = [number for number in order if number in page_numbers]
        if ordered:
            remaining = [number for number in page_numbers if number not in ordered]
            return ordered + remaining
    return sorted(page_numbers)


def _page_content(objects: dict[int, _PdfObject], page: _PdfObject) -> bytes:
    match = _CONTENTS_RE.search(page.header)
    if match is None:
        return b""
    target = match.group(1)
    references = [int(ref.group(1)) for ref in _REF_RE.finditer(target)]
    parts: list[bytes] = []
    total = 0
    for reference in references:
        item = objects.get(reference)
        if item is None or item.stream is None:
            continue
        decoded = _decode_stream(item)
        total += len(decoded) + (1 if parts else 0)
        if total > MAX_PDF_STREAM_BYTES:
            raise ExtractionError(
                ExtractionDiagnosticCode.FILE_TOO_LARGE,
                "decoded PDF page streams exceed the size limit",
            )
        parts.append(decoded)
    return b"\n".join(parts)


def _decode_stream(item: _PdfObject) -> bytes:
    stream = item.stream or b""
    filters = _FILTER_RE.search(item.header)
    names: list[str] = []
    if filters is not None:
        names = [name.decode("latin-1") for name in re.findall(rb"/(\w+)", filters.group(1))]
    if not names:
        return stream
    if names != ["FlateDecode"]:
        raise ExtractionError(
            ExtractionDiagnosticCode.PDF_UNSUPPORTED_FILTER,
            f"unsupported PDF stream filter: {','.join(names)}",
        )
    try:
        decompressor = zlib.decompressobj()
        decoded = decompressor.decompress(stream, MAX_PDF_STREAM_BYTES + 1)
        if len(decoded) > MAX_PDF_STREAM_BYTES or decompressor.unconsumed_tail:
            raise ExtractionError(
                ExtractionDiagnosticCode.FILE_TOO_LARGE,
                "decompressed PDF stream exceeds the size limit",
            )
        decoded += decompressor.flush(MAX_PDF_STREAM_BYTES + 1 - len(decoded))
        if len(decoded) > MAX_PDF_STREAM_BYTES:
            raise ExtractionError(
                ExtractionDiagnosticCode.FILE_TOO_LARGE,
                "decompressed PDF stream exceeds the size limit",
            )
        if not decompressor.eof:
            raise ExtractionError(
                ExtractionDiagnosticCode.PDF_MALFORMED,
                "PDF stream could not be decompressed",
            )
        return decoded
    except ExtractionError:
        raise
    except zlib.error as exc:
        raise ExtractionError(
            ExtractionDiagnosticCode.PDF_MALFORMED, "PDF stream could not be decompressed"
        ) from exc


def _content_to_text(content: bytes) -> str:
    if not content:
        return ""
    lines: list[str] = []
    current: list[bytes] = []
    operands: list[bytes] = []
    array_items: list[bytes] = []
    in_array = False

    def break_line() -> None:
        if current:
            lines.append(b"".join(current).decode("latin-1").rstrip())
            current.clear()

    def emit(data: bytes) -> None:
        current.append(data)

    for token in _TOKEN_RE.findall(content):
        if token == b"[":
            in_array = True
            array_items = []
            operands = []
            continue
        if token == b"]":
            in_array = False
            operands = list(array_items)
            continue
        if _NUMBER_RE.match(token):
            if in_array and float(token) <= -200:
                array_items.append(b" ")
            continue
        if _OPERATOR_RE.match(token):
            operator = token.decode("latin-1")
            if operator == "Tj":
                emit(_string_operand(operands))
            elif operator == "TJ":
                emit(b"".join(array_items))
            elif operator in {"'", '"'}:
                break_line()
                emit(_string_operand(operands))
            elif operator in {"Td", "TD", "T*", "Tm"}:
                break_line()
            operands = []
            array_items = []
            in_array = False
            if len(lines) > 200_000:
                break
            continue
        if in_array:
            array_items.append(_decode_string_token(token) or b"")
        else:
            if len(operands) < _TJ_STACK_DEPTH:
                operands.append(token)
            else:
                operands = operands[-_TJ_STACK_DEPTH:]
    if current:
        lines.append(b"".join(current).decode("latin-1").rstrip())
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _string_operand(operands: list[bytes]) -> bytes:
    for token in reversed(operands):
        decoded = _decode_string_token(token)
        if decoded is not None:
            return decoded
    return b""


def _decode_string_token(token: bytes) -> bytes | None:
    if token.startswith(b"(") and token.endswith(b")") and len(token) >= 2:
        return _decode_literal(token[1:-1])
    if token.startswith(b"<") and token.endswith(b">") and len(token) >= 2:
        digits = re.sub(rb"\s+", b"", token[1:-1])
        if len(digits) % 2:
            digits += b"0"
        try:
            return bytes.fromhex(digits.decode("ascii"))
        except ValueError:
            return b""
    return None


def _decode_literal(body: bytes) -> bytes:
    output = bytearray()
    index = 0
    length = len(body)
    while index < length:
        character = body[index]
        if character != 0x5C:
            output.append(character)
            index += 1
            continue
        index += 1
        if index >= length:
            break
        escape = body[index]
        index += 1
        mapping = {
            ord("n"): b"\n",
            ord("r"): b"\r",
            ord("t"): b"\t",
            ord("b"): b"\b",
            ord("f"): b"\f",
            ord("("): b"(",
            ord(")"): b")",
            ord("\\"): b"\\",
        }
        if escape in mapping:
            output.extend(mapping[escape])
            continue
        if 0x30 <= escape <= 0x37:
            octal = bytes([escape])
            for _ in range(2):
                if index < length and 0x30 <= body[index] <= 0x37:
                    octal += bytes([body[index]])
                    index += 1
            output.append(int(octal, 8) & 0xFF)
            continue
        if escape in (0x0A, 0x0D):
            if escape == 0x0D and index < length and body[index] == 0x0A:
                index += 1
            continue
        output.append(escape)
    return bytes(output)


def _fragment_ranges(text: str, limit: int) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + limit, length)
        if end < length:
            boundary = text.rfind("\n", start, end)
            if boundary <= start:
                boundary = text.rfind(" ", start, end)
            if boundary > start:
                end = boundary + 1
        ranges.append((start, end))
        start = end
    return ranges


__all__ = [
    "EXTRACTOR_NAME_LINE",
    "EXTRACTOR_NAME_MARKDOWN",
    "EXTRACTOR_NAME_PDF",
    "EXTRACTOR_NAME_TEXT",
    "EXTRACTOR_VERSION",
    "extractor_name_for",
    "ExtractedDocument",
    "Locator",
    "extract_document",
]
