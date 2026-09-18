"""F05: Markdown/TXT/PDF extraction, diagnostics and re-verifiable locators."""

from __future__ import annotations

import unittest
import zlib

from personal_knowledge.chunking import chunk_document, rebuild_chunk_text
from personal_knowledge.extractors import (
    MAX_PDF_STREAM_BYTES,
    _decode_stream,
    _page_content,
    _PdfObject,
    extract_document,
)
from personal_knowledge.models import (
    ExtractionDiagnosticCode,
    ExtractionError,
)


def build_pdf(pages: list[str], *, compress: bool = False, with_encrypt: bool = False) -> bytes:
    objects: dict[int, bytes] = {}
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{3 + index} 0 R" for index in range(len(pages)))
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode()
    contents_number = 3 + len(pages)
    for index in range(len(pages)):
        objects[3 + index] = (
            f"<< /Type /Page /Parent 2 0 R /Contents {contents_number + index} 0 R >>"
        ).encode()
    for index, script in enumerate(pages):
        body = script.encode("latin-1")
        if compress:
            body = zlib.compress(body)
            header = f"<< /Length {len(body)} /Filter /FlateDecode >>".encode()
        else:
            header = f"<< /Length {len(body)} >>".encode()
        objects[contents_number + index] = (
            header + b"\nstream\n" + body + b"\nendstream"
        )
    output = bytearray(b"%PDF-1.4\n")
    if with_encrypt:
        output += b"trailer\n<< /Encrypt 99 0 R >>\n"
    for number in sorted(objects):
        output += f"{number} 0 obj\n".encode() + objects[number] + b"\nendobj\n"
    output += b"trailer\n<< /Size 10 >>\n%%EOF\n"
    return bytes(output)


class MarkdownExtractionTests(unittest.TestCase):
    def test_heading_hierarchy_and_line_locators(self) -> None:
        data = (
            b"# Title\n"
            b"\n"
            b"Intro line one.\n"
            b"Intro line two.\n"
            b"\n"
            b"## Section A\n"
            b"\n"
            b"Body A.\n"
        )
        document = extract_document(data, "text/markdown")
        kinds = [
            (section.locator.kind, section.locator.start, section.locator.end)
            for section in document.sections
        ]
        self.assertEqual(
            [
                ("line_range", 1, 1),
                ("line_range", 3, 4),
                ("line_range", 6, 6),
                ("line_range", 8, 8),
            ],
            kinds,
        )
        self.assertEqual(("Title",), document.sections[1].heading_path)
        self.assertEqual(("Title", "Section A"), document.sections[3].heading_path)
        lines = document.text.split("\n")
        for section in document.sections:
            self.assertEqual(
                section.text,
                "\n".join(lines[section.locator.start - 1 : section.locator.end]),
            )

    def test_code_fence_does_not_create_headings(self) -> None:
        data = b"# Real\n\n```\n# not a heading\n```\n\nAfter.\n"
        document = extract_document(data, "text/markdown")
        self.assertNotIn("# not a heading", [s.locator.label for s in document.sections])
        self.assertEqual(("Real",), document.sections[-1].heading_path)

    def test_plain_text_paragraphs(self) -> None:
        data = b"first paragraph\nstill first\n\nsecond paragraph\n"
        document = extract_document(data, "text/plain")
        self.assertEqual(
            [(1, 2), (4, 4)],
            [(s.locator.start, s.locator.end) for s in document.sections],
        )

    def test_invalid_utf8_is_a_typed_error(self) -> None:
        with self.assertRaises(ExtractionError) as captured:
            extract_document(b"\xff\xfe\x00\x01", "text/plain")
        self.assertEqual(ExtractionDiagnosticCode.DECODE_ERROR, captured.exception.code)

    def test_empty_file_is_a_typed_error(self) -> None:
        with self.assertRaises(ExtractionError) as captured:
            extract_document(b"   \n\n", "text/plain")
        self.assertEqual(ExtractionDiagnosticCode.EMPTY_FILE, captured.exception.code)

    def test_binary_utf8_looking_file_is_rejected(self) -> None:
        with self.assertRaises(ExtractionError) as captured:
            extract_document(b"\x00\x01\x02binary", "text/markdown")
        self.assertEqual(ExtractionDiagnosticCode.DECODE_ERROR, captured.exception.code)

    def test_unsupported_media_type_is_a_typed_error(self) -> None:
        with self.assertRaises(ExtractionError) as captured:
            extract_document(b"data", "application/octet-stream")
        self.assertEqual(ExtractionDiagnosticCode.UNSUPPORTED_FORMAT, captured.exception.code)

    def test_oversized_file_is_a_typed_error(self) -> None:
        with self.assertRaises(ExtractionError) as captured:
            extract_document(b"x" * 100, "text/plain", max_file_bytes=10)
        self.assertEqual(ExtractionDiagnosticCode.FILE_TOO_LARGE, captured.exception.code)


class PdfExtractionTests(unittest.TestCase):
    def test_pages_and_fragments_are_stable(self) -> None:
        script_a = "BT /F1 12 Tf 72 720 Td (Alpha fact one) Tj T* (Alpha fact two) Tj ET"
        script_b = "BT /F1 12 Tf 72 720 Td (Beta page text) Tj ET"
        document = extract_document(build_pdf([script_a, script_b]), "application/pdf")
        self.assertEqual(2, len(document.sections))
        self.assertEqual([1, 2], [section.locator.page for section in document.sections])
        pages = document.text.split("\f")
        for section in document.sections:
            assert section.locator.page is not None
            page_text = pages[section.locator.page - 1]
            self.assertEqual(
                section.text, page_text[section.locator.start : section.locator.end]
            )
        self.assertIn("Alpha fact one", document.sections[0].text)

    def test_flate_compressed_content_is_supported(self) -> None:
        script = "BT /F1 12 Tf 72 720 Td (Compressed text) Tj ET"
        document = extract_document(build_pdf([script], compress=True), "application/pdf")
        self.assertIn("Compressed text", document.text)

    def test_flate_stream_is_bounded_after_decompression(self) -> None:
        compressed = zlib.compress(b"x" * (MAX_PDF_STREAM_BYTES + 1))
        item = _PdfObject(
            number=1,
            header=b"<< /Filter /FlateDecode >>",
            stream=compressed,
        )
        with self.assertRaises(ExtractionError) as captured:
            _decode_stream(item)
        self.assertEqual(ExtractionDiagnosticCode.FILE_TOO_LARGE, captured.exception.code)

    def test_page_content_has_an_aggregate_decoded_stream_limit(self) -> None:
        half_plus_one = b"x" * (MAX_PDF_STREAM_BYTES // 2 + 1)
        objects = {
            1: _PdfObject(
                number=1,
                header=b"<< /Type /Page /Contents [2 0 R 3 0 R] >>",
                stream=None,
            ),
            2: _PdfObject(number=2, header=b"", stream=half_plus_one),
            3: _PdfObject(number=3, header=b"", stream=half_plus_one),
        }
        with self.assertRaises(ExtractionError) as captured:
            _page_content(objects, objects[1])
        self.assertEqual(ExtractionDiagnosticCode.FILE_TOO_LARGE, captured.exception.code)

    def test_chunk_locators_resolve_to_exact_text(self) -> None:
        script = "BT /F1 12 Tf 72 720 Td (Windowed content) Tj ET"
        data = build_pdf([script])
        document = extract_document(data, "application/pdf")
        for chunk in chunk_document(document):
            self.assertEqual(chunk.text, rebuild_chunk_text(document, chunk.locator))

    def test_encrypted_marker_is_a_typed_error(self) -> None:
        with self.assertRaises(ExtractionError) as captured:
            extract_document(build_pdf(["test"], with_encrypt=True), "application/pdf")
        self.assertEqual(ExtractionDiagnosticCode.PDF_ENCRYPTED, captured.exception.code)

    def test_truncated_pdf_is_a_typed_error(self) -> None:
        with self.assertRaises(ExtractionError) as captured:
            extract_document(b"%PDF-1.4\ngarbage without objects", "application/pdf")
        self.assertEqual(ExtractionDiagnosticCode.PDF_MALFORMED, captured.exception.code)

    def test_non_pdf_bytes_are_a_typed_error(self) -> None:
        with self.assertRaises(ExtractionError) as captured:
            extract_document(b"not a pdf at all", "application/pdf")
        self.assertEqual(ExtractionDiagnosticCode.PDF_MALFORMED, captured.exception.code)

    def test_unsupported_filter_is_reported_as_a_diagnostic(self) -> None:
        objects = (
            b"%PDF-1.4\n"
            b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
            b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>\nendobj\n"
            b"4 0 obj\n<< /Length 4 /Filter /DCTDecode >>\nstream\nxxxx\nendstream\nendobj\n"
            b"trailer\n<< /Size 5 >>\n%%EOF\n"
        )
        document = extract_document(objects, "application/pdf")
        self.assertEqual(
            [ExtractionDiagnosticCode.PDF_UNSUPPORTED_FILTER],
            [item.code for item in document.diagnostics],
        )
        self.assertEqual((), document.sections)


class LocatorVerificationTests(unittest.TestCase):
    def test_markdown_chunk_slices_match_source_lines(self) -> None:
        data = (
            "# Heading\n\n"
            + "\n".join(f"line {index} content" for index in range(60))
            + "\n"
        ).encode()
        document = extract_document(data, "text/markdown")
        chunks = chunk_document(document)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertEqual(chunk.text, rebuild_chunk_text(document, chunk.locator))
            self.assertLessEqual(len(chunk.text), 1200)
            self.assertEqual(
                chunk.content_hash, __import__("hashlib").sha256(chunk.text.encode()).hexdigest()
            )

    def test_one_very_long_line_is_split_into_exact_bounded_fragments(self) -> None:
        document = extract_document(("界" * 100_000).encode(), "text/plain")
        chunks = chunk_document(document)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk.text) <= 1200 for chunk in chunks))
        self.assertTrue(all(len(chunk.text.encode("utf-8")) <= 64 * 1024 for chunk in chunks))
        self.assertEqual(document.text, "".join(chunk.text for chunk in chunks))
        for chunk in chunks:
            self.assertEqual(chunk.text, rebuild_chunk_text(document, chunk.locator))

    def test_one_large_pdf_page_is_split_without_losing_locator_fidelity(self) -> None:
        script = "BT /F1 12 Tf 72 720 Td (" + ("x" * 10_000) + ") Tj ET"
        document = extract_document(build_pdf([script]), "application/pdf")
        chunks = chunk_document(document)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(document.text, "".join(chunk.text for chunk in chunks))
        for chunk in chunks:
            self.assertEqual(chunk.text, rebuild_chunk_text(document, chunk.locator))


if __name__ == "__main__":
    unittest.main()
