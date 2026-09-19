"""F06 unit tests: bounded MIME handling and canonical dedupe identity."""

from __future__ import annotations

import hashlib
import unittest
from email.message import EmailMessage

from nju_smail.mime import (
    build_message_bytes as extension_build,
)
from nju_smail.mime import (
    canonical_content_hash as extension_hash,
)
from nju_smail.mime import (
    dedupe_key as extension_dedupe,
)
from nju_smail.mime import (
    parse_message as extension_parse,
)

from personal_assistant.infrastructure.mail.mime import (
    build_message_bytes,
    canonical_content_hash,
    dedupe_key,
    normalize_body_text,
    normalized_subject,
    parse_envelope,
    parse_message,
    sanitize_filename,
)


class CanonicalIdentityTests(unittest.TestCase):
    def test_normalized_subject_strips_reply_prefixes(self) -> None:
        self.assertEqual("quarterly report", normalized_subject("Re: Fwd:  Quarterly  report "))

    def test_normalize_body_collapses_trailing_whitespace_only(self) -> None:
        self.assertEqual("a\nb", normalize_body_text("a  \r\nb\r\n"))

    def test_content_hash_ignores_field_order_and_is_stable(self) -> None:
        first = canonical_content_hash(
            message_id="<a@b>",
            subject="Subject",
            from_address="A@B",
            to_addresses=("x@y", "z@y"),
            sent_at="2026-09-18T00:00:00+00:00",
            body_text="hello\n",
            attachment_hashes=("b", "a"),
        )
        second = canonical_content_hash(
            message_id="<a@b>",
            subject="subject",
            from_address="a@b",
            to_addresses=("z@y", "x@y"),
            sent_at="2026-09-18T00:00:00+00:00",
            body_text="hello",
            attachment_hashes=("a", "b"),
        )
        self.assertEqual(first, second)

    def test_content_hash_changes_with_body_or_attachments(self) -> None:
        base = dict(
            message_id="<a@b>",
            subject="s",
            from_address="a@b",
            to_addresses=("x@y",),
            sent_at=None,
            body_text="hello",
        )
        original = canonical_content_hash(**base)
        self.assertNotEqual(original, canonical_content_hash(**{**base, "body_text": "hello!"}))
        self.assertNotEqual(
            original,
            canonical_content_hash(**{**base, "attachment_hashes": ("deadbeef",)}),
        )

    def test_message_id_dedupe_binds_identity_and_content(self) -> None:
        with_id = dedupe_key(message_id="<X@Y>", content_hash="c" * 64)
        # Case-insensitive Message-ID with identical content dedupes.
        self.assertEqual(with_id, dedupe_key(message_id="<x@y>", content_hash="c" * 64))
        # A reused Message-ID with different bytes is a different message; new
        # mail must never be hidden behind an old body.
        self.assertNotEqual(
            with_id, dedupe_key(message_id="<x@y>", content_hash="d" * 64)
        )
        without = dedupe_key(message_id=None, content_hash="c" * 64)
        self.assertTrue(without.startswith("content:"))

    def test_extension_and_host_identity_agree(self) -> None:
        fields = dict(
            message_id="<m@x>",
            subject="Hi",
            from_address="a@b",
            to_addresses=["c@d"],
            sent_at=None,
            body_text="body",
            attachment_hashes=["aa"],
        )
        self.assertEqual(extension_hash(**fields), canonical_content_hash(**fields))
        self.assertEqual(
            extension_dedupe(message_id="<m@x>", content_hash="0" * 64),
            dedupe_key(message_id="<m@x>", content_hash="0" * 64),
        )


class MimeBoundsTests(unittest.TestCase):
    def test_filename_traversal_is_reduced_to_a_basename(self) -> None:
        self.assertEqual("evil.exe", sanitize_filename("../../evil.exe"))
        self.assertEqual("evil.exe", sanitize_filename("..\\..\\evil.exe"))
        self.assertEqual("attachment", sanitize_filename(".."))
        self.assertEqual("attachment", sanitize_filename(""))

    def test_built_message_hides_bcc_but_keeps_other_headers(self) -> None:
        raw = build_message_bytes(
            message_id="<smail.act@example.test>",
            from_address="me@example.test",
            to_addresses=("you@example.test",),
            cc_addresses=("cc@example.test",),
            bcc_addresses=("secret@example.test",),
            subject="Subject line",
            body_text="Body text",
        )
        parsed = parse_envelope(raw)
        self.assertEqual("<smail.act@example.test>", parsed["message_id"])
        self.assertEqual("Subject line", parsed["subject"])
        self.assertEqual(["you@example.test"], parsed["to_addresses"])
        self.assertNotIn(b"secret@example.test", raw)

    def test_deep_multipart_is_truncated_without_recursion(self) -> None:
        message: EmailMessage = EmailMessage()
        message.set_content("leaf")
        for _ in range(40):
            outer = EmailMessage()
            outer.make_mixed()
            outer.attach(message)
            message = outer
        parsed = parse_message(message.as_bytes())
        self.assertTrue(parsed.truncated)
        self.assertLessEqual(len(parsed.body_text), 8)

    def test_malformed_mime_is_data_not_a_crash(self) -> None:
        parsed = parse_message(b"\x00\x01not a message at all")
        self.assertIsInstance(parsed.subject, str)
        parsed_ext = extension_parse(b"Content-Type: multipart/mixed; boundary=zzz")
        self.assertIsInstance(parsed_ext.body_text, str)

    def test_compressed_attachment_is_hashed_raw_and_never_expanded(self) -> None:
        import io
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("bomb.txt", "A" * 2_000_000)
        payload = buffer.getvalue()
        self.assertLess(len(payload), 50_000)
        raw = build_message_bytes(
            message_id="<smail.bomb@example.test>",
            from_address="me@example.test",
            to_addresses=("you@example.test",),
            cc_addresses=(),
            bcc_addresses=(),
            subject="Zip",
            body_text="Attached",
            attachments=(("../../bomb.zip", "application/zip", payload),),
        )
        parsed = parse_message(raw)
        extension = extension_parse(raw)
        self.assertEqual("bomb.zip", parsed.attachments[0].filename)
        self.assertEqual(len(payload), parsed.attachments[0].size_bytes)
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(), parsed.attachments[0].sha256
        )
        self.assertEqual("application/zip", extension.attachments[0].media_type)
        self.assertEqual(len(payload), extension.attachments[0].size_bytes)

    def test_extension_and_host_parsers_agree_on_headers(self) -> None:
        raw = build_message_bytes(
            message_id="<smail.act2@example.test>",
            from_address="me@example.test",
            to_addresses=("you@example.test",),
            cc_addresses=(),
            bcc_addresses=(),
            subject="Hi",
            body_text="Body",
            attachments=(("../../note.txt", "text/plain", b"note"),),
        )
        host = parse_envelope(raw)
        extension = extension_parse(raw)
        self.assertEqual(host["message_id"], extension.message_id)
        self.assertEqual(host["subject"], extension.subject)
        self.assertEqual(["you@example.test"], list(extension.to_addresses))
        self.assertEqual("note.txt", extension.attachments[0].filename)
        self.assertEqual(
            extension_build(
                message_id="<smail.act2@example.test>",
                from_address="me@example.test",
                to_addresses=("you@example.test",),
                cc_addresses=(),
                bcc_addresses=(),
                subject="Hi",
                body_text="Body",
                attachments=(("../../note.txt", "text/plain", b"note"),),
            ).count(b"note.txt"),
            1,
        )


if __name__ == "__main__":
    unittest.main()
