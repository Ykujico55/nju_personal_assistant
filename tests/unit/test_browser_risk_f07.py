"""Gate 1 counterexamples: permanent R3 classification and risk escalation."""

from __future__ import annotations

import unittest

from personal_assistant.core.browser import (
    ProhibitedCategory,
    RiskSignal,
    assess_risk,
    classify_labels,
    escalate_risk,
    risk_rank,
    scan_text_for_prohibited_terms,
)
from personal_assistant.domain.enums import RiskLevel

# Twelve independent forbidden-semantics examples, one per class plus synonyms.
PROHIBITED_TEXTS = {
    "course_withdrawal_cn": ("退课申请", ProhibitedCategory.COURSE_WITHDRAWAL),
    "course_withdrawal_en": (
        "Withdraw Course from this term",
        ProhibitedCategory.COURSE_WITHDRAWAL,
    ),
    "course_drop_en": ("Drop course confirmation", ProhibitedCategory.COURSE_WITHDRAWAL),
    "revocation_cn": ("撤销申请", ProhibitedCategory.REVOCATION),
    "revocation_en": ("Revoke application", ProhibitedCategory.REVOCATION),
    "payment_cn": ("在线缴费", ProhibitedCategory.PAYMENT),
    "payment_en": ("Make a payment now", ProhibitedCategory.PAYMENT),
    "refund_en": ("Refund request", ProhibitedCategory.PAYMENT),
    "course_change_cn": ("选课变更", ProhibitedCategory.COURSE_CHANGE),
    "course_add_en": ("Add course to your schedule", ProhibitedCategory.COURSE_CHANGE),
    "legal_cn": ("签署承诺书", ProhibitedCategory.LEGAL_DECLARATION),
    "legal_en": ("Sign the waiver of liability", ProhibitedCategory.LEGAL_DECLARATION),
    "penalty_cn": ("违约金确认", ProhibitedCategory.HIGH_CONSEQUENCE),
    "penalty_en": ("Penalty acknowledged", ProhibitedCategory.HIGH_CONSEQUENCE),
}


class ProhibitedTermTests(unittest.TestCase):
    def test_every_catalog_example_matches_its_category(self) -> None:
        self.assertGreaterEqual(len(PROHIBITED_TEXTS), 12)
        for name, (text, category) in PROHIBITED_TEXTS.items():
            with self.subTest(example=name):
                matches = scan_text_for_prohibited_terms(text)
                self.assertTrue(matches, f"{text!r} must be detected")
                self.assertIn(category, [item.category for item in matches])

    def test_scan_never_returns_surrounding_text(self) -> None:
        secret = "secret-token-abcdef"
        text = f"{secret} 请确认退课 {secret}"
        matches = scan_text_for_prohibited_terms(text)
        self.assertTrue(matches)
        for match in matches:
            self.assertNotIn(secret, match.term)

    def test_extra_adapter_terms_are_enforced(self) -> None:
        matches = scan_text_for_prohibited_terms("确认放弃本次机会", ("放弃本次机会",))
        self.assertTrue(matches)
        self.assertTrue(any(item.term == "放弃本次机会" for item in matches))

    def test_benign_text_has_no_matches(self) -> None:
        self.assertEqual(scan_text_for_prohibited_terms("在读证明申请，请填写联系方式"), ())

    def test_app_list_labels_are_classified_individually(self) -> None:
        labels = ["在读证明", "退课申请", "成绩单打印", "在线缴费"]
        classified = classify_labels(labels)
        self.assertEqual(classified[0][1], ())
        self.assertTrue(classified[1][1])
        self.assertEqual(classified[2][1], ())
        self.assertTrue(classified[3][1])


class RiskAssessmentTests(unittest.TestCase):
    def test_unknown_transaction_is_permanently_prohibited(self) -> None:
        assessment = assess_risk(RiskLevel.READ, transaction_known=False)
        self.assertTrue(assessment.prohibited)
        self.assertIn(ProhibitedCategory.UNKNOWN_TRANSACTION, assessment.categories)

    def test_unknown_page_version_is_permanently_prohibited(self) -> None:
        assessment = assess_risk(RiskLevel.READ, page_version_known=False)
        self.assertTrue(assessment.prohibited)
        self.assertIn(ProhibitedCategory.UNKNOWN_PAGE_VERSION, assessment.categories)

    def test_prohibited_term_raises_the_declared_risk(self) -> None:
        matches = scan_text_for_prohibited_terms("退课")
        assessment = assess_risk(RiskLevel.READ, matches=matches)
        self.assertTrue(assessment.prohibited)
        self.assertTrue(assessment.escalated)

    def test_declared_prohibited_can_never_be_lowered(self) -> None:
        assessment = assess_risk(RiskLevel.PROHIBITED)
        self.assertEqual(assessment.risk, RiskLevel.PROHIBITED)

    def test_page_text_only_escalates(self) -> None:
        self.assertEqual(
            escalate_risk(RiskLevel.READ, RiskLevel.EXTERNAL_WRITE), RiskLevel.EXTERNAL_WRITE
        )
        self.assertEqual(
            escalate_risk(RiskLevel.EXTERNAL_WRITE, RiskLevel.READ), RiskLevel.EXTERNAL_WRITE
        )
        self.assertEqual(
            escalate_risk(RiskLevel.INTERNAL_WRITE, RiskLevel.PROHIBITED), RiskLevel.PROHIBITED
        )

    def test_rank_ordering_is_fixed(self) -> None:
        self.assertLess(risk_rank(RiskLevel.READ), risk_rank(RiskLevel.INTERNAL_WRITE))
        self.assertLess(risk_rank(RiskLevel.INTERNAL_WRITE), risk_rank(RiskLevel.EXTERNAL_WRITE))
        self.assertLess(risk_rank(RiskLevel.EXTERNAL_WRITE), risk_rank(RiskLevel.PROHIBITED))

    def test_unknown_signal_risk_fails_closed(self) -> None:
        assessment = assess_risk(
            RiskLevel.READ,
            extra_signals=(
                RiskSignal(code="UNEXPECTED_STRUCTURE", detail="", risk=RiskLevel.PROHIBITED),
            ),
        )
        self.assertTrue(assessment.prohibited)

    def test_autosave_signal_escalates_to_external_write(self) -> None:
        assessment = assess_risk(
            RiskLevel.READ,
            extra_signals=(
                RiskSignal(
                    code="AUTOSAVE_ATTEMPT_BLOCKED",
                    detail="",
                    risk=RiskLevel.EXTERNAL_WRITE,
                ),
            ),
        )
        self.assertEqual(assessment.risk, RiskLevel.EXTERNAL_WRITE)
        self.assertTrue(assessment.escalated)

    def test_benign_page_signals_do_not_escalate(self) -> None:
        assessment = assess_risk(RiskLevel.READ)
        self.assertEqual(assessment.risk, RiskLevel.READ)
        self.assertFalse(assessment.escalated)
        self.assertEqual(assessment.categories, ())


if __name__ == "__main__":
    unittest.main()
