"""Gate 1 counterexamples: origin/URL allow-listing and adapter validation."""

from __future__ import annotations

import unittest

from personal_assistant.core.browser import (
    AdapterActionSpec,
    AdapterFieldSpec,
    BrowserPolicyError,
    TransactionAdapterDescriptor,
    evaluate_navigation,
    normalize_origin,
    validate_adapter_descriptor,
)
from personal_assistant.domain.enums import RiskLevel

ALLOWED = frozenset({"https://ehall.example.edu"})
PATHS = ("/apps/*", "/portal", "/sso/login")


def decide(url: str, *, paths: tuple[str, ...] = PATHS) -> str:
    return evaluate_navigation(url, allowed_origins=ALLOWED, allowed_paths=paths).reason


class OriginPolicyBypassTests(unittest.TestCase):
    """At least twelve independent origin/URL bypass counterexamples."""

    def test_allowed_official_url_is_accepted(self) -> None:
        decision = evaluate_navigation(
            "https://ehall.example.edu/apps/proof",
            allowed_origins=ALLOWED,
            allowed_paths=PATHS,
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.origin, "https://ehall.example.edu")
        self.assertEqual(decision.path, "/apps/proof")

    def test_plain_http_is_rejected(self) -> None:
        self.assertEqual(decide("http://ehall.example.edu/apps/proof"), "SCHEME_NOT_ALLOWED")

    def test_javascript_scheme_is_rejected(self) -> None:
        self.assertEqual(decide("javascript:alert(document.cookie)"), "SCHEME_NOT_ALLOWED")

    def test_data_scheme_is_rejected(self) -> None:
        self.assertEqual(decide("data:text/html,<script>alert(1)</script>"), "SCHEME_NOT_ALLOWED")

    def test_file_scheme_is_rejected(self) -> None:
        self.assertEqual(decide("file:///C:/Users/secret.txt"), "SCHEME_NOT_ALLOWED")

    def test_blob_scheme_is_rejected(self) -> None:
        self.assertEqual(decide("blob:https://ehall.example.edu/abc"), "SCHEME_NOT_ALLOWED")

    def test_userinfo_is_rejected(self) -> None:
        url = "https://user:pass@ehall.example.edu/apps/proof"
        self.assertEqual(decide(url), "USERINFO_NOT_ALLOWED")

    def test_non_default_port_is_rejected_unless_allow_listed(self) -> None:
        self.assertEqual(decide("https://ehall.example.edu:8443/apps/proof"), "HOST_NOT_ALLOWED")

    def test_non_official_subdomain_is_rejected(self) -> None:
        self.assertEqual(decide("https://evil.ehall.example.edu/apps/proof"), "HOST_NOT_ALLOWED")

    def test_suffix_confusion_host_is_rejected(self) -> None:
        url = "https://ehall.example.edu.evil.com/apps/proof"
        self.assertEqual(decide(url), "HOST_NOT_ALLOWED")

    def test_percent_encoded_at_in_host_is_rejected(self) -> None:
        url = "https://ehall.example.edu%40evil.com/apps/proof"
        self.assertEqual(decide(url), "MALFORMED_URL")

    def test_encoded_dot_dot_traversal_is_rejected(self) -> None:
        self.assertEqual(decide("https://ehall.example.edu/apps/%2e%2e/evil"), "ENCODED_TRAVERSAL")

    def test_double_encoded_dot_dot_traversal_is_rejected(self) -> None:
        url = "https://ehall.example.edu/apps/%252e%252e/evil"
        self.assertEqual(decide(url), "ENCODED_TRAVERSAL")

    def test_plain_dot_dot_segment_is_rejected(self) -> None:
        self.assertEqual(decide("https://ehall.example.edu/apps/../../evil"), "ENCODED_TRAVERSAL")

    def test_backslash_confusion_is_rejected(self) -> None:
        self.assertEqual(decide("https://ehall.example.edu/apps\\..\\evil"), "BACKSLASH")

    def test_open_redirect_query_with_absolute_url_is_rejected(self) -> None:
        self.assertEqual(
            decide("https://ehall.example.edu/sso/login?url=https://evil.com/steal"),
            "OPEN_REDIRECT",
        )

    def test_open_redirect_query_with_protocol_relative_url_is_rejected(self) -> None:
        self.assertEqual(
            decide("https://ehall.example.edu/sso/login?next=//evil.com/steal"),
            "OPEN_REDIRECT",
        )

    def test_open_redirect_query_with_encoded_absolute_url_is_rejected(self) -> None:
        self.assertEqual(
            decide("https://ehall.example.edu/sso/login?target=https%3A%2F%2Fevil.com"),
            "OPEN_REDIRECT",
        )

    def test_open_redirect_query_with_double_encoded_url_is_rejected(self) -> None:
        for value in (
            "https%253A%252F%252Fevil.com",
            "%252F%252Fevil.com",
            "https%253A%252F%252Fevil.com%252Fsteal",
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    decide(f"https://ehall.example.edu/sso/login?next={value}"),
                    "OPEN_REDIRECT",
                )

    def test_redirect_parameter_to_an_allowlisted_origin_is_accepted(self) -> None:
        # Standard CAS: the login hop carries service=<allowlisted origin>.
        self.assertEqual(
            decide(
                "https://ehall.example.edu/sso/login?service=https://ehall.example.edu/portal"
            ),
            "ALLOWED",
        )
        self.assertEqual(
            decide(
                "https://ehall.example.edu/sso/login?service=https%3A%2F%2Fehall.example.edu%2Fportal"
            ),
            "ALLOWED",
        )
        self.assertEqual(
            decide("https://ehall.example.edu/sso/login?service=/portal"), "ALLOWED"
        )

    def test_redirect_parameter_to_an_allowlisted_lookalike_is_rejected(self) -> None:
        for value, expected in (
            ("https://ehall.example.edu.evil.com/portal", "OPEN_REDIRECT"),
            ("https://ehall.example.edu:8443/portal", "OPEN_REDIRECT"),
            ("http://ehall.example.edu/portal", "OPEN_REDIRECT"),
            ("https://user@ehall.example.edu/portal", "OPEN_REDIRECT"),
            ("https:\ehall.example.edu\portal", "BACKSLASH"),
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    decide(f"https://ehall.example.edu/sso/login?service={value}"),
                    expected,
                )

    def test_open_redirect_query_with_javascript_value_is_rejected(self) -> None:
        self.assertEqual(
            decide("https://ehall.example.edu/sso/login?redirect_uri=javascript:alert(1)"),
            "OPEN_REDIRECT",
        )

    def test_port_zero_is_rejected(self) -> None:
        self.assertEqual(decide("https://ehall.example.edu:0/apps/proof"), "PORT_NOT_ALLOWED")

    def test_control_characters_are_rejected(self) -> None:
        url = "https://ehall.example.edu/apps\r\nHost: evil"
        self.assertEqual(decide(url), "CONTROL_CHARACTERS")

    def test_overlong_url_is_rejected(self) -> None:
        long_url = "https://ehall.example.edu/apps/" + ("a" * 3000)
        self.assertEqual(decide(long_url), "URL_TOO_LONG")

    def test_path_outside_adapter_patterns_is_rejected(self) -> None:
        self.assertEqual(decide("https://ehall.example.edu/admin/danger"), "PATH_NOT_ALLOWED")

    def test_populated_allowlist_required(self) -> None:
        decision = evaluate_navigation(
            "https://ehall.example.edu/apps/proof", allowed_origins=set()
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "HOST_NOT_ALLOWED")


class NormalizeOriginTests(unittest.TestCase):
    def test_https_origin_is_canonicalized(self) -> None:
        self.assertEqual(normalize_origin("https://ehall.example.edu"), "https://ehall.example.edu")
        self.assertEqual(
            normalize_origin("https://ehall.example.edu:443"), "https://ehall.example.edu"
        )
        self.assertEqual(
            normalize_origin("https://EHALL.example.edu."), "https://ehall.example.edu"
        )

    def test_non_https_origin_is_rejected(self) -> None:
        for raw in ("http://ehall.example.edu", "ftp://ehall.example.edu", "javascript:x"):
            with self.subTest(raw=raw), self.assertRaises(BrowserPolicyError):
                normalize_origin(raw)

    def test_origin_with_path_query_or_fragment_is_rejected(self) -> None:
        for raw in (
            "https://ehall.example.edu/portal",
            "https://ehall.example.edu?x=1",
            "https://ehall.example.edu#frag",
            "https://user@ehall.example.edu",
        ):
            with self.subTest(raw=raw), self.assertRaises(BrowserPolicyError):
                normalize_origin(raw)


class AdapterDescriptorValidationTests(unittest.TestCase):
    def _descriptor(self, **overrides: object) -> TransactionAdapterDescriptor:
        values: dict[str, object] = {
            "extension_id": "nju.ehall",
            "extension_version": "0.1.0",
            "adapter_id": "nju.ehall.proof",
            "adapter_version": "1.0.0",
            "display_name": "在读证明",
            "allowed_origins": ("https://ehall.example.edu",),
            "allowed_paths": ("/apps/proof",),
            "declared_risk": RiskLevel.EXTERNAL_WRITE,
            "transaction_ids": ("proof.apply",),
            "fields": (
                AdapterFieldSpec(
                    field_id="reason", label="理由", locator="ctl:0:0", required=True
                ),
            ),
            "actions": (
                AdapterActionSpec(
                    action_id="proof.submit",
                    locator="act:0",
                    label="提交",
                    final=True,
                    method="POST",
                    target_origin="https://ehall.example.edu",
                    target_path="/apps/proof/submit",
                ),
            ),
            "login_paths": ("/sso/login",),
            "allowed_page_fingerprints": ("a" * 64,),
        }
        values.update(overrides)
        return TransactionAdapterDescriptor(**values)  # type: ignore[arg-type]

    def test_valid_descriptor_passes(self) -> None:
        validate_adapter_descriptor(self._descriptor(), allowed_origins=ALLOWED)

    def test_final_action_must_declare_a_non_get_method(self) -> None:
        for method in ("", "GET", "HEAD"):
            with self.subTest(method=method):
                descriptor = self._descriptor(
                    actions=(
                        AdapterActionSpec(
                            action_id="proof.submit",
                            locator="act:0",
                            label="提交",
                            final=True,
                            method=method,
                            target_origin="https://ehall.example.edu",
                            target_path="/apps/proof/submit",
                        ),
                    )
                )
                with self.assertRaises(BrowserPolicyError) as caught:
                    validate_adapter_descriptor(descriptor, allowed_origins=ALLOWED)
                self.assertEqual(caught.exception.reason, "ADAPTER_INVALID")

    def test_final_action_target_origin_must_be_allowlisted(self) -> None:
        descriptor = self._descriptor(
            actions=(
                AdapterActionSpec(
                    action_id="proof.submit",
                    locator="act:0",
                    label="提交",
                    final=True,
                    method="POST",
                    target_origin="https://evil.example.com",
                    target_path="/apps/proof/submit",
                ),
            )
        )
        with self.assertRaises(BrowserPolicyError) as caught:
            validate_adapter_descriptor(descriptor, allowed_origins=ALLOWED)
        self.assertEqual(caught.exception.reason, "ORIGIN_NOT_ALLOWED")

    def test_final_action_target_path_must_be_static(self) -> None:
        for path in ("", "apps/proof", "/apps/proof?x=1", "/apps/../secrets", "/a#b"):
            with self.subTest(path=path):
                descriptor = self._descriptor(
                    actions=(
                        AdapterActionSpec(
                            action_id="proof.submit",
                            locator="act:0",
                            label="提交",
                            final=True,
                            method="POST",
                            target_origin="https://ehall.example.edu",
                            target_path=path,
                        ),
                    )
                )
                with self.assertRaises(BrowserPolicyError) as caught:
                    validate_adapter_descriptor(descriptor, allowed_origins=ALLOWED)
                self.assertEqual(caught.exception.reason, "ADAPTER_INVALID")

    def test_page_fingerprints_are_required_and_validated(self) -> None:
        descriptor = self._descriptor(allowed_page_fingerprints=())
        with self.assertRaises(BrowserPolicyError) as caught:
            validate_adapter_descriptor(descriptor, allowed_origins=ALLOWED)
        self.assertEqual(caught.exception.reason, "ADAPTER_INVALID")
        descriptor = self._descriptor(allowed_page_fingerprints=("A" * 64,))
        with self.assertRaises(BrowserPolicyError) as caught:
            validate_adapter_descriptor(descriptor, allowed_origins=ALLOWED)
        self.assertEqual(caught.exception.reason, "ADAPTER_INVALID")
        descriptor = self._descriptor(allowed_page_fingerprints=("a" * 64,) * 9)
        with self.assertRaises(BrowserPolicyError) as caught:
            validate_adapter_descriptor(descriptor, allowed_origins=ALLOWED)
        self.assertEqual(caught.exception.reason, "ADAPTER_INVALID")

    def test_origin_outside_user_allowlist_is_rejected(self) -> None:
        descriptor = self._descriptor(allowed_origins=("https://evil.example.com",))
        with self.assertRaises(BrowserPolicyError) as caught:
            validate_adapter_descriptor(descriptor, allowed_origins=ALLOWED)
        self.assertEqual(caught.exception.reason, "ORIGIN_NOT_ALLOWED")

    def test_prohibited_transaction_is_rejected(self) -> None:
        descriptor = self._descriptor(declared_risk=RiskLevel.PROHIBITED)
        with self.assertRaises(BrowserPolicyError) as caught:
            validate_adapter_descriptor(descriptor, allowed_origins=ALLOWED)
        self.assertEqual(caught.exception.reason, "ADAPTER_RISK_PROHIBITED")

    def test_prohibited_action_is_rejected(self) -> None:
        descriptor = self._descriptor(
            actions=(
                AdapterActionSpec(
                    action_id="proof.withdraw",
                    locator="#withdraw",
                    label="退课",
                    risk=RiskLevel.PROHIBITED,
                    final=True,
                    method="POST",
                    target_origin="https://ehall.example.edu",
                    target_path="/apps/proof/submit",
                ),
            )
        )
        with self.assertRaises(BrowserPolicyError) as caught:
            validate_adapter_descriptor(descriptor, allowed_origins=ALLOWED)
        self.assertEqual(caught.exception.reason, "ADAPTER_RISK_PROHIBITED")

    def test_two_final_actions_are_rejected(self) -> None:
        descriptor = self._descriptor(
            actions=(
                AdapterActionSpec("proof.a", "#a", "A", final=True),
                AdapterActionSpec("proof.b", "#b", "B", final=True),
            )
        )
        with self.assertRaises(BrowserPolicyError):
            validate_adapter_descriptor(descriptor, allowed_origins=ALLOWED)

    def test_path_traversal_in_adapter_paths_is_rejected(self) -> None:
        descriptor = self._descriptor(allowed_paths=("/apps/../admin",))
        with self.assertRaises(BrowserPolicyError):
            validate_adapter_descriptor(descriptor, allowed_origins=ALLOWED)

    def test_dynamic_path_with_query_is_rejected(self) -> None:
        descriptor = self._descriptor(discovery_path="/portal?user=1")
        with self.assertRaises(BrowserPolicyError):
            validate_adapter_descriptor(descriptor, allowed_origins=ALLOWED)

    def test_invalid_field_pattern_is_rejected(self) -> None:
        descriptor = self._descriptor(
            fields=(
                AdapterFieldSpec(
                    field_id="reason", label="理由", locator="ctl:0:0", pattern="(["
                ),
            )
        )
        with self.assertRaises(BrowserPolicyError):
            validate_adapter_descriptor(descriptor, allowed_origins=ALLOWED)

    def test_field_without_locator_is_rejected(self) -> None:
        descriptor = self._descriptor(
            fields=(AdapterFieldSpec(field_id="reason", label="理由", locator=""),)
        )
        with self.assertRaises(BrowserPolicyError):
            validate_adapter_descriptor(descriptor, allowed_origins=ALLOWED)

    def test_raw_css_field_locator_is_rejected(self) -> None:
        descriptor = self._descriptor(
            fields=(
                AdapterFieldSpec(
                    field_id="reason", label="理由", locator="input[name='reason']"
                ),
            )
        )
        with self.assertRaises(BrowserPolicyError):
            validate_adapter_descriptor(descriptor, allowed_origins=ALLOWED)

    def test_raw_css_action_locator_is_rejected(self) -> None:
        descriptor = self._descriptor(
            actions=(
                AdapterActionSpec(action_id="proof.submit", locator="#submit", label="提交"),
            )
        )
        with self.assertRaises(BrowserPolicyError):
            validate_adapter_descriptor(descriptor, allowed_origins=ALLOWED)

    def test_unbounded_receipt_locator_is_rejected(self) -> None:
        descriptor = self._descriptor(receipt_locator="#receipt")
        with self.assertRaises(BrowserPolicyError):
            validate_adapter_descriptor(descriptor, allowed_origins=ALLOWED)


if __name__ == "__main__":
    unittest.main()
