from __future__ import annotations

from personal_assistant.domain import Sensitivity

from .models import Evidence


def disclosure_summary(evidence: Evidence) -> dict[str, str]:
    """Metadata preview for consent UI; intentionally excludes evidence text."""

    return {
        "id": evidence.id,
        "source_uri": evidence.source_uri,
        "locator": str(dict(evidence.locator)),
        "classification": evidence.sensitivity.value,
        "content_hash": evidence.content_hash,
    }


def may_send_to_any_model(evidence: Evidence) -> bool:
    return evidence.sensitivity is not Sensitivity.SECRET

