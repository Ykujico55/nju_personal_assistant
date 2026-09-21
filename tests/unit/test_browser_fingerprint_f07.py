"""Gate 1: page fingerprint stability and drift detection."""

from __future__ import annotations

import unittest

from personal_assistant.core.browser import (
    compute_page_fingerprint,
    page_structure_document,
)


def control(
    *,
    tag: str = "input",
    type_: str = "text",
    name: str = "field",
    element_id: str = "f1",
    required: bool = False,
    readonly: bool = False,
    options: tuple[str, ...] = (),
    max_length: int = 0,
) -> dict[str, object]:
    return {
        "tag": tag,
        "type": type_,
        "name": name,
        "element_id": element_id,
        "required": required,
        "readonly": readonly,
        "options": list(options),
        "max_length": max_length,
    }


def fingerprint(
    controls: list[dict[str, object]], headings: list[dict[str, object]] | None = None
) -> str:
    document = page_structure_document(controls=controls, headings=headings or [])
    return compute_page_fingerprint(document)


class FingerprintDriftTests(unittest.TestCase):
    """Ten independent structural change scenarios must all change the digest."""

    def setUp(self) -> None:
        self.base_controls = [
            control(name="reason", element_id="reason", required=True, max_length=200),
            control(
                tag="select",
                type_="select",
                name="delivery",
                element_id="delivery",
                options=("paper", "email"),
            ),
        ]
        self.base = fingerprint(self.base_controls, [{"level": 1, "text": "在读证明申请"}])

    def test_identical_structure_is_stable(self) -> None:
        again = fingerprint(
            [dict(item) for item in self.base_controls],
            [{"level": 1, "text": "在读证明申请"}],
        )
        self.assertEqual(self.base, again)

    def test_added_field_changes_the_fingerprint(self) -> None:
        controls = [*self.base_controls, control(name="extra", element_id="extra")]
        changed = fingerprint(controls, [{"level": 1, "text": "在读证明申请"}])
        self.assertNotEqual(self.base, changed)

    def test_removed_field_changes_the_fingerprint(self) -> None:
        self.assertNotEqual(
            self.base, fingerprint([self.base_controls[0]], [{"level": 1, "text": "在读证明申请"}])
        )

    def test_field_order_change_changes_the_fingerprint(self) -> None:
        reversed_controls = list(reversed(self.base_controls))
        self.assertNotEqual(
            self.base, fingerprint(reversed_controls, [{"level": 1, "text": "在读证明申请"}])
        )

    def test_required_flag_change_changes_the_fingerprint(self) -> None:
        changed = [dict(item) for item in self.base_controls]
        changed[0]["required"] = False
        self.assertNotEqual(self.base, fingerprint(changed, [{"level": 1, "text": "在读证明申请"}]))

    def test_readonly_flag_change_changes_the_fingerprint(self) -> None:
        changed = [dict(item) for item in self.base_controls]
        changed[1]["readonly"] = True
        self.assertNotEqual(self.base, fingerprint(changed, [{"level": 1, "text": "在读证明申请"}]))

    def test_input_type_change_changes_the_fingerprint(self) -> None:
        changed = [dict(item) for item in self.base_controls]
        changed[0]["type"] = "password"
        self.assertNotEqual(self.base, fingerprint(changed, [{"level": 1, "text": "在读证明申请"}]))

    def test_name_change_changes_the_fingerprint(self) -> None:
        changed = [dict(item) for item in self.base_controls]
        changed[0]["name"] = "reason2"
        self.assertNotEqual(self.base, fingerprint(changed, [{"level": 1, "text": "在读证明申请"}]))

    def test_select_options_change_changes_the_fingerprint(self) -> None:
        changed = [dict(item) for item in self.base_controls]
        changed[1]["options"] = ["paper", "email", "sms"]
        self.assertNotEqual(self.base, fingerprint(changed, [{"level": 1, "text": "在读证明申请"}]))

    def test_max_length_change_changes_the_fingerprint(self) -> None:
        changed = [dict(item) for item in self.base_controls]
        changed[0]["max_length"] = 100
        self.assertNotEqual(self.base, fingerprint(changed, [{"level": 1, "text": "在读证明申请"}]))

    def test_heading_change_changes_the_fingerprint(self) -> None:
        self.assertNotEqual(
            self.base, fingerprint(self.base_controls, [{"level": 1, "text": "成绩单打印"}])
        )

    def test_heading_level_change_changes_the_fingerprint(self) -> None:
        self.assertNotEqual(
            self.base, fingerprint(self.base_controls, [{"level": 2, "text": "在读证明申请"}])
        )

    def test_value_changes_do_not_change_the_fingerprint(self) -> None:
        with_value = [dict(item) for item in self.base_controls]
        with_value[0]["value"] = "some typed value"
        self.assertEqual(
            self.base, fingerprint(with_value, [{"level": 1, "text": "在读证明申请"}])
        )


if __name__ == "__main__":
    unittest.main()
