"""Deterministic page-structure fingerprint.

The fingerprint is computed from a bounded, vendor-neutral structure document
(accessibility/DOM controls and headings) instead of raw HTML, so the same
algorithm works for any page and never captures rendered values, cookies or
image data.  Values are deliberately excluded: a changed value is a field
change, not a page-version drift.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from personal_assistant.core.approvals.canonicalize import canonical_json, canonical_sha256
from personal_assistant.core.browser.errors import BrowserLimitError

MAX_STRUCTURE_CONTROLS = 512
MAX_STRUCTURE_HEADINGS = 128
MAX_STRUCTURE_LINKS = 128
MAX_STRUCTURE_FORMS = 32
MAX_STRUCTURE_FORM_FIELD = 512
MAX_STRUCTURE_OPTIONS = 64
MAX_STRUCTURE_TEXT = 256
MAX_STRUCTURE_BYTES = 256 * 1024

_CONTROL_FIELDS = (
    "tag",
    "type",
    "name",
    "element_id",
    "locator",
    "role",
    "aria_label",
    "placeholder",
    "inputmode",
    "autocomplete",
)
_CONTROL_FLAGS = ("required", "readonly", "disabled", "multiple")


def _bounded_text(value: Any) -> str:
    text: str
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value
    else:
        text = str(value)
    text = text.strip()
    if len(text) > MAX_STRUCTURE_TEXT:
        return text[:MAX_STRUCTURE_TEXT]
    return text


def _normalize_control(raw: Mapping[str, Any], index: int) -> dict[str, Any]:
    control: dict[str, Any] = {"order": index}
    for key in _CONTROL_FIELDS:
        control[key] = _bounded_text(raw.get(key))
    for key in _CONTROL_FLAGS:
        value = raw.get(key)
        control[key] = bool(value) if isinstance(value, bool) else False
    options_raw = raw.get("options")
    options: list[str] = []
    if isinstance(options_raw, Sequence) and not isinstance(options_raw, (str, bytes)):
        for item in options_raw:
            if len(options) >= MAX_STRUCTURE_OPTIONS:
                break
            options.append(_bounded_text(item))
    control["options"] = options
    max_length = raw.get("max_length")
    control["max_length"] = int(max_length) if isinstance(max_length, int) else 0
    return control


def page_structure_document(
    *,
    controls: Sequence[Mapping[str, Any]],
    headings: Sequence[Mapping[str, Any]] = (),
    links: Sequence[Mapping[str, Any]] = (),
    forms: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Normalize raw companion structure into the canonical fingerprint input."""

    if len(controls) > MAX_STRUCTURE_CONTROLS:
        raise BrowserLimitError("page structure exceeds the control limit")
    if len(headings) > MAX_STRUCTURE_HEADINGS:
        raise BrowserLimitError("page structure exceeds the heading limit")
    if len(links) > MAX_STRUCTURE_LINKS:
        raise BrowserLimitError("page structure exceeds the link limit")
    if len(forms) > MAX_STRUCTURE_FORMS:
        raise BrowserLimitError("page structure exceeds the form limit")
    normalized_controls = [_normalize_control(item, index) for index, item in enumerate(controls)]
    normalized_headings: list[dict[str, Any]] = []
    for index, item in enumerate(headings):
        normalized_headings.append(
            {
                "order": index,
                "level": int(item.get("level", 0) or 0),
                "text": _bounded_text(item.get("text")),
            }
        )
    normalized_links: list[dict[str, Any]] = []
    for index, item in enumerate(links):
        normalized_links.append(
            {
                "order": index,
                "text": _bounded_text(item.get("text")),
                "path": _bounded_text(item.get("path")),
            }
        )
    normalized_forms: list[dict[str, Any]] = []
    for index, item in enumerate(forms):
        normalized_forms.append(
            {
                "order": index,
                "action": _bounded_text(item.get("action"))[:MAX_STRUCTURE_FORM_FIELD],
                "method": _bounded_text(item.get("method")).lower(),
                "element_id": _bounded_text(item.get("element_id")),
                "name": _bounded_text(item.get("name")),
            }
        )
    document = {
        "controls": normalized_controls,
        "headings": normalized_headings,
        "links": normalized_links,
        "forms": normalized_forms,
    }
    if len(canonical_json(document).encode("utf-8")) > MAX_STRUCTURE_BYTES:
        raise BrowserLimitError("page structure exceeds the byte limit")
    return document


def compute_page_fingerprint(structure: Mapping[str, Any]) -> str:
    """Return the canonical SHA-256 of a normalized page structure document."""

    return canonical_sha256(structure)


__all__ = [
    "MAX_STRUCTURE_BYTES",
    "MAX_STRUCTURE_CONTROLS",
    "MAX_STRUCTURE_FORMS",
    "MAX_STRUCTURE_HEADINGS",
    "MAX_STRUCTURE_LINKS",
    "MAX_STRUCTURE_OPTIONS",
    "MAX_STRUCTURE_TEXT",
    "compute_page_fingerprint",
    "page_structure_document",
]
