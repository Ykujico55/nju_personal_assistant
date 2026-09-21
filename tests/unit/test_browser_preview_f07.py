"""Gate 1: structured preview canonical binding, limits and round-trips."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from personal_assistant.core.browser import (
    AttachmentPreview,
    BrowserLimitError,
    FieldChange,
    FieldValidation,
    FieldValueSource,
    FillPlan,
    PreviewExpiredError,
    TransactionPreview,
    canonical_preview_sha256,
)
from personal_assistant.domain.enums import RiskLevel


def build_hash(
    *,
    origin: str = "https://ehall.example.edu",
    app_id: str = "proof",
    transaction_id: str = "proof.apply",
    adapter_id: str = "nju.ehall.proof",
    adapter_version: str = "1.0.0",
    extension_id: str = "nju.ehall",
    extension_version: str = "0.1.0",
    page_fingerprint: str = "a" * 64,
    risk: RiskLevel = RiskLevel.EXTERNAL_WRITE,
    consequences: str = "提交后进入审核。",
    fields: tuple[FieldChange, ...] | None = None,
    attachments: tuple[AttachmentPreview, ...] | None = None,
) -> str:
    if fields is None:
        fields = (
            FieldChange(
                field_id="reason",
                locator="#reason",
                label="理由",
                old_value="",
                new_value="需要办理",
                source=FieldValueSource.USER_INPUT,
            ),
            FieldChange(
                field_id="phone",
                locator="#phone",
                label="电话",
                old_value="",
                new_value="13800000000",
                source=FieldValueSource.USER_INPUT,
            ),
        )
    if attachments is None:
        attachments = (
            AttachmentPreview(name="id.pdf", size_bytes=1024, sha256="b" * 64),
        )
    return canonical_preview_sha256(
        origin=origin,
        app_id=app_id,
        transaction_id=transaction_id,
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        extension_id=extension_id,
        extension_version=extension_version,
        page_fingerprint=page_fingerprint,
        risk=risk,
        consequences=consequences,
        fields=fields,
        attachments=attachments,
    )


class PreviewCanonicalBindingTests(unittest.TestCase):
    def test_field_order_does_not_change_the_digest(self) -> None:
        base_fields = (
            FieldChange("a", "#a", "A", "", "1"),
            FieldChange("b", "#b", "B", "", "2"),
        )
        reversed_fields = tuple(reversed(base_fields))
        self.assertEqual(build_hash(fields=base_fields), build_hash(fields=reversed_fields))

    def test_attachment_order_does_not_change_the_digest(self) -> None:
        first = AttachmentPreview("a.pdf", 10, "1" * 64)
        second = AttachmentPreview("b.pdf", 20, "2" * 64)
        self.assertEqual(
            build_hash(attachments=(first, second)),
            build_hash(attachments=(second, first)),
        )

    def test_new_value_change_changes_the_digest(self) -> None:
        changed = (FieldChange("a", "#a", "A", "", "DIFFERENT"),)
        base = build_hash(fields=(FieldChange("a", "#a", "A", "", "1"),))
        self.assertNotEqual(build_hash(fields=changed), base)

    def test_old_value_change_changes_the_digest(self) -> None:
        self.assertNotEqual(
            build_hash(fields=(FieldChange("a", "#a", "A", "old", "1"),)),
            build_hash(fields=(FieldChange("a", "#a", "A", "older", "1"),)),
        )

    def test_source_change_changes_the_digest(self) -> None:
        self.assertNotEqual(
            build_hash(fields=(FieldChange("a", "#a", "A", "", "1", FieldValueSource.USER_INPUT),)),
            build_hash(fields=(FieldChange("a", "#a", "A", "", "1", FieldValueSource.EVIDENCE),)),
        )

    def test_evidence_change_changes_the_digest(self) -> None:
        self.assertNotEqual(
            build_hash(
                fields=(FieldChange("a", "#a", "A", "", "1", evidence_sha256="c" * 64),)
            ),
            build_hash(
                fields=(FieldChange("a", "#a", "A", "", "1", evidence_sha256="d" * 64),)
            ),
        )

    def test_attachment_hash_change_changes_the_digest(self) -> None:
        self.assertNotEqual(
            build_hash(attachments=(AttachmentPreview("a.pdf", 10, "1" * 64),)),
            build_hash(attachments=(AttachmentPreview("a.pdf", 10, "2" * 64),)),
        )

    def test_attachment_size_change_changes_the_digest(self) -> None:
        self.assertNotEqual(
            build_hash(attachments=(AttachmentPreview("a.pdf", 10, "1" * 64),)),
            build_hash(attachments=(AttachmentPreview("a.pdf", 11, "1" * 64),)),
        )

    def test_page_fingerprint_change_changes_the_digest(self) -> None:
        self.assertNotEqual(
            build_hash(page_fingerprint="a" * 64), build_hash(page_fingerprint="b" * 64)
        )

    def test_transaction_change_changes_the_digest(self) -> None:
        self.assertNotEqual(
            build_hash(transaction_id="proof.apply"), build_hash(transaction_id="proof.reissue")
        )

    def test_adapter_version_change_changes_the_digest(self) -> None:
        self.assertNotEqual(
            build_hash(adapter_version="1.0.0"), build_hash(adapter_version="1.0.1")
        )

    def test_extension_version_change_changes_the_digest(self) -> None:
        self.assertNotEqual(
            build_hash(extension_version="0.1.0"), build_hash(extension_version="0.2.0")
        )

    def test_origin_change_changes_the_digest(self) -> None:
        self.assertNotEqual(
            build_hash(origin="https://ehall.example.edu"),
            build_hash(origin="https://other.example.edu"),
        )

    def test_risk_change_changes_the_digest(self) -> None:
        self.assertNotEqual(
            build_hash(risk=RiskLevel.READ), build_hash(risk=RiskLevel.EXTERNAL_WRITE)
        )

    def test_consequences_change_changes_the_digest(self) -> None:
        self.assertNotEqual(
            build_hash(consequences="提交后进入审核。"),
            build_hash(consequences="提交后不可撤回。"),
        )


class PreviewLimitsTests(unittest.TestCase):
    def _preview(self, **overrides: object) -> TransactionPreview:
        now = datetime.now(UTC)
        values: dict[str, object] = {
            "session_id": "brs_test",
            "origin": "https://ehall.example.edu",
            "app_id": "proof",
            "transaction_id": "proof.apply",
            "adapter_id": "nju.ehall.proof",
            "adapter_version": "1.0.0",
            "extension_id": "nju.ehall",
            "extension_version": "0.1.0",
            "page_fingerprint": "a" * 64,
            "risk": RiskLevel.EXTERNAL_WRITE,
            "consequences": "提交后进入审核。",
            "fields": (),
            "canonical_payload_hash": "c" * 64,
            "nonce": "nonce-value",
            "generated_at": now,
            "expires_at": now + timedelta(seconds=300),
        }
        values.update(overrides)
        return TransactionPreview(**values)  # type: ignore[arg-type]

    def test_more_than_128_fields_are_rejected(self) -> None:
        fields = tuple(
            FieldChange(f"f{index}", f"#f{index}", "F", "", "1") for index in range(129)
        )
        with self.assertRaises(BrowserLimitError):
            self._preview(fields=fields)

    def test_more_than_20_attachments_are_rejected(self) -> None:
        attachments = tuple(
            AttachmentPreview(f"a{index}.pdf", 10, f"{index:064x}") for index in range(21)
        )
        with self.assertRaises(BrowserLimitError):
            self._preview(attachments=attachments)

    def test_prohibited_preview_is_impossible(self) -> None:
        with self.assertRaises(BrowserLimitError):
            self._preview(risk=RiskLevel.PROHIBITED)

    def test_expiry_must_follow_generation(self) -> None:
        now = datetime.now(UTC)
        with self.assertRaises(BrowserLimitError):
            self._preview(generated_at=now, expires_at=now)

    def test_invalid_attachment_digest_is_rejected(self) -> None:
        with self.assertRaises(BrowserLimitError):
            AttachmentPreview("a.pdf", 10, "NOT-A-HASH")

    def test_expired_preview_requires_freshness(self) -> None:
        now = datetime.now(UTC)
        preview = self._preview(
            generated_at=now - timedelta(minutes=10),
            expires_at=now - timedelta(minutes=5),
        )
        with self.assertRaises(PreviewExpiredError):
            preview.require_fresh(now)

    def test_preview_round_trip_keeps_bindings(self) -> None:
        preview = self._preview(
            fields=(
                FieldChange(
                    "reason",
                    "#reason",
                    "理由",
                    "",
                    "需要办理",
                    source=FieldValueSource.USER_INPUT,
                    validation=FieldValidation.VALID,
                ),
            ),
            attachments=(AttachmentPreview("id.pdf", 10, "1" * 64),),
        )
        document = preview.to_document()
        restored = TransactionPreview.from_document(document)
        self.assertEqual(restored.canonical_payload_hash, preview.canonical_payload_hash)
        self.assertEqual(restored.nonce, preview.nonce)
        self.assertEqual(restored.fields[0].new_value, "需要办理")
        self.assertEqual(restored.attachments[0].sha256, "1" * 64)

    def test_fill_plan_requires_a_known_source(self) -> None:
        with self.assertRaises(BrowserLimitError):
            FillPlan(
                adapter_id="a.b",
                adapter_version="1.0.0",
                transaction_id="t",
                expected_origin="https://ehall.example.edu",
                expected_page_fingerprint="a" * 64,
                fields=(FieldChange("a", "#a", "A", "", "1", FieldValueSource.UNKNOWN),),
            )

    def test_field_confidence_must_be_bounded(self) -> None:
        with self.assertRaises(BrowserLimitError):
            FieldChange("a", "#a", "A", "", "1", confidence=1.5)


if __name__ == "__main__":
    unittest.main()
